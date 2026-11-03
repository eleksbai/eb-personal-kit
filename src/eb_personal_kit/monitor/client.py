"""Monitor sampling client: collect system metrics and print/upload samples.

Dimensions collected every interval: CPU load and package temperature, memory,
per-mount disk space, disk I/O (throughput, IOPS, busy percentage), network
throughput, NVMe temperature and, where ``nvidia-smi`` exists, one entry per
NVIDIA GPU (utilisation, memory, temperature).

Device identity is auto-detected (Linux machine-id, falling back to a hostname
hash) — no device_key config needed. By default the client only samples and
prints one status line per interval; uploading is opt-in via the ``upload``
setting, which requires the server RSA public key (``public_key_path``) and
the server base URL (``server_url``).

Uploads go through an async retry queue: sampling keeps a fixed cadence, failed
sends are retried with exponential backoff until a sample ages out of the
server's anti-replay window (``REPLAY_WINDOW`` = 300s). Failures never block
the sampling rhythm.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import secrets
import shutil
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import psutil
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from eb_personal_kit.monitor.config import Settings
from eb_personal_kit.utils import human_bytes

logger = logging.getLogger("monitor")

UPLOAD_TIMEOUT = 10
# ``nvidia-smi`` stalls on a busy or half-initialised driver, so the query is
# given its own budget instead of relying on the caller's cadence.
GPU_TIMEOUT = 5

_MACHINE_ID_PATHS = ("/etc/machine-id", "/var/lib/dbus/machine-id")

# Sensor driver names that report CPU temperatures, and the group used for the
# system disk. Looked up by name so the readings stay separate (see
# :func:`read_temperatures`).
_CPU_SENSOR_GROUPS = ("coretemp", "k10temp", "cpu_thermal", "soc_thermal", "zenpower")
_DISK_SENSOR_GROUP = "nvme"

_GPU_FIELDS = "utilization.gpu,memory.used,memory.total,temperature.gpu"
_GPU_QUERY = [
    "nvidia-smi",
    f"--query-gpu={_GPU_FIELDS}",
    "--format=csv,noheader,nounits",
]

REPLAY_WINDOW = 300  # server anti-replay window, seconds
_BACKOFF_MAX = 30


@dataclass
class DiskUsage:
    """Usage of one mounted filesystem."""

    mount: str  # mount point
    used: int  # Bytes
    total: int  # Bytes


@dataclass
class GpuUsage:
    """One NVIDIA GPU as reported by ``nvidia-smi``."""

    util: int  # percent x100
    mem_used: int  # Bytes
    mem_total: int  # Bytes
    temp: int  # degC x10


@dataclass
class Sample:
    """One raw metrics sample; units follow the upload payload contract.

    The rates are measurements *over a window*, not instantaneous readings, so the
    first sample of a run — taken before any counter baseline exists — leaves them
    at ``0`` with :attr:`window` unset. It is printed with the rate columns blank
    and never uploaded.
    """

    ts: int  # unix seconds
    rid: str  # random sample id
    mem_used: int  # Bytes
    mem_total: int  # Bytes
    disks: list[DiskUsage]  # per-mount usage
    cpu_temp: int  # degC x10 (CPU package sensor)
    disk_temp: int  # degC x10 (NVMe composite sensor)
    gpus: list[GpuUsage]  # empty on hosts without an NVIDIA GPU

    # Differential fields: only meaningful once ``window`` covers a baseline.
    window: float | None = None  # seconds the rates below were measured over
    cpu_usage: int = 0  # percent x100
    disk_read: int = 0  # B/s
    disk_write: int = 0  # B/s
    net_in: int = 0  # B/s
    net_out: int = 0  # B/s
    disk_iops: int = 0  # completed operations per second (reads + writes)
    disk_busy: int = 0  # percent x100 of the window with I/O in flight

    @property
    def measured(self) -> bool:
        """Whether the differential fields carry a real measurement window."""
        return self.window is not None

    def to_payload(self) -> dict:
        """Return the JSON-ready payload in the wire format the server expects
        (rates converted to the wire unit KB/s). Only ever called on a measured
        sample: the warm-up one has no window and the caller keeps it off the wire."""
        return {
            "ts": self.ts,
            "rid": self.rid,
            "cpu_usage": self.cpu_usage,
            "mem_used": self.mem_used,
            "mem_total": self.mem_total,
            "disk_read": self.disk_read // 1024,
            "disk_write": self.disk_write // 1024,
            "disks": [{"mount": d.mount, "used": d.used, "total": d.total} for d in self.disks],
            "cpu_temp": self.cpu_temp,
            "net_in": self.net_in // 1024,
            "net_out": self.net_out // 1024,
            "disk_iops": self.disk_iops,
            "disk_busy": self.disk_busy,
            "disk_temp": self.disk_temp,
            # GPU memory stays in bytes, like the ``disks`` entries.
            "gpus": [
                {
                    "util": g.util,
                    "mem_used": g.mem_used,
                    "mem_total": g.mem_total,
                    "temp": g.temp,
                }
                for g in self.gpus
            ],
        }

    def status_line(self) -> str:
        """Render the volatile metrics as a fixed-width single line so columns
        align across consecutive samples. Percentages and temperatures round to
        whole numbers — they are watched for a trend, not for precision — while
        byte counts keep a decimal and reserve the width of the largest value of
        each 1024-based unit step (e.g. ``1023.9KB``).

        The established field order is untouched and every new dimension joins
        the segment it belongs to: IOPS and busy percentage continue the disk
        rates, the NVMe reading sits beside the CPU one. Each GPU appends one more
        segment, so the line has no fixed end — it stops after the temperature
        segment on hosts without GPU data. Disk *space* is deliberately absent:
        it changes slower than the interval, so it is only reported at startup
        (see :func:`root_disk_usage`), while still being sampled for the payload.
        """
        # The warm-up sample has no window yet, so its rate columns print as ``-``
        # at the reserved width and the lines stay aligned all the same.
        rates = self.measured
        line = (
            "{time} cpu= {cpu:>4} | mem= {mem:<6}({pct:>4})"
            " | disk r= {disk_r:>8}  ,w= {disk_w:>8}  ,iops= {iops:>6}  ,busy= {busy:>4}"
            " | net in= {net_in:>8} ,out= {net_out:>8}"
            " | cpu= {temp:>5} ,nvme= {disk_temp:>5}"
        ).format(
            time=time.strftime("%X", time.localtime(self.ts)),
            cpu=f"{self.cpu_usage / 100:.0f}%" if rates else "-",
            mem=human_bytes(self.mem_used),
            pct=f"{self.mem_used * 100 / self.mem_total:.0f}%",
            disk_r=f"{human_bytes(self.disk_read)}/s" if rates else "-",
            disk_w=f"{human_bytes(self.disk_write)}/s" if rates else "-",
            iops=self.disk_iops if rates else "-",
            busy=f"{self.disk_busy / 100:.0f}%" if rates else "-",
            net_in=f"{human_bytes(self.net_in)}/s" if rates else "-",
            net_out=f"{human_bytes(self.net_out)}/s" if rates else "-",
            temp=f"{self.cpu_temp / 10:.0f}°C",
            disk_temp=f"{self.disk_temp / 10:.0f}°C",
        )
        segments = []
        for idx, gpu in enumerate(self.gpus):
            pct = f"{gpu.mem_used * 100 / gpu.mem_total:.0f}%" if gpu.mem_total else "-"
            segments.append(
                " | gpu{idx} util= {util:>4}  mem= {mem:<6}({pct:>4})  temp= {temp:>5}".format(
                    idx=idx,
                    util=f"{gpu.util / 100:.0f}%",
                    mem=human_bytes(gpu.mem_used),
                    pct=pct,
                    temp=f"{gpu.temp / 10:.0f}°C",
                )
            )
        return line + "".join(segments)


def get_device_key() -> str:
    for path in _MACHINE_ID_PATHS:
        try:
            value = Path(path).read_text().strip()
            if value:
                return value
        except OSError:
            continue
    return socket.gethostname()


def root_disk_usage() -> str:
    """Root-filesystem usage as ``pct(used/total)``, or ``-`` if unreadable.

    Reported once on startup rather than per sample: space moves too slowly for
    the real-time line, but still travels in every payload as the ``disks`` list.
    """
    try:
        usage = psutil.disk_usage("/")
    except OSError:
        return "-"
    return f"{usage.percent:.0f}%({human_bytes(usage.used)}/{human_bytes(usage.total)})"


def _pick_temp(entries, hint: str) -> float:
    """The reading of the entry whose label contains ``hint`` (the package or
    composite sensor), else the hottest entry. ``0.0`` when nothing reported."""
    values = [(e.label, e.current) for e in entries if e.current]
    for label, current in values:
        if hint in label:
            return current
    return max((current for _, current in values), default=0.0)


def read_temperatures() -> tuple[float, float]:
    """Return the ``(cpu, disk)`` temperature in degC, ``0.0`` when unavailable.

    Selecting the sensor groups by driver name keeps the CPU and NVMe readings
    apart: taking the maximum over *every* sensor, as this helper used to, made
    ``cpu_temp`` report the disk temperature on hosts that expose an NVMe one.
    """
    try:
        groups = psutil.sensors_temperatures() or {}
    except Exception:  # noqa: BLE001 - sensors are unavailable on many hosts
        return 0.0, 0.0
    cpu = [e for name, entries in groups.items() if name in _CPU_SENSOR_GROUPS for e in entries]
    return (
        _pick_temp(cpu, "Package"),
        _pick_temp(groups.get(_DISK_SENSOR_GROUP, []), "Composite"),
    )


def _num(value: str) -> float:
    """Parse one ``nvidia-smi`` CSV field; ``N/A`` (MIG mode) and junk become 0."""
    try:
        return float(value)
    except ValueError:
        return 0.0


def get_gpu_info() -> list[GpuUsage]:
    """Return one entry per NVIDIA GPU, or an empty list when there is none.

    Any failure — binary missing, a driver that does not answer within
    ``GPU_TIMEOUT``, non-zero exit, unparsable row — means "no GPU", which is what
    the sampling loop degrades to. ``nvidia-smi`` reports memory in MiB while the
    payload contract is bytes, hence the ``* 1024 * 1024``.
    """
    if not shutil.which("nvidia-smi"):
        return []
    try:
        result = subprocess.run(_GPU_QUERY, capture_output=True, text=True, timeout=GPU_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    gpus = []
    for row in result.stdout.strip().splitlines():
        fields = [f.strip() for f in row.split(",")]
        if len(fields) != 4:
            continue
        util, mem_used, mem_total, temp = (_num(f) for f in fields)
        gpus.append(
            GpuUsage(
                util=int(util * 100),
                mem_used=int(mem_used * 1024 * 1024),
                mem_total=int(mem_total * 1024 * 1024),
                temp=int(temp * 10),
            )
        )
    return gpus


def _fmt_ts(ts: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


class Monitor:
    """Sample system metrics on a fixed cadence and upload encrypted samples."""

    def __init__(self, config: Settings):
        self.config = config
        # The server public key is only needed when uploading is enabled.
        self.public_key = (
            serialization.load_pem_public_key(Path(config.public_key_path).read_bytes())
            if config.upload
            else None
        )
        self.device_key = get_device_key()
        # Latest ``nvidia-smi`` snapshot, refreshed by a background task so the
        # sampling cadence never waits on the query (see :meth:`_poll_gpus`).
        self._gpus: list[GpuUsage] = []
        # Global retry-backoff counter shared by all queued samples; it keeps
        # growing while the server is unreachable and resets on a success.
        self._attempt = 0

    def run(self) -> None:
        if self.config.upload:
            logger.info(
                "start... device_key=%s  server=%s  interval=%ss",
                self.device_key,
                self.config.server_url,
                self.config.interval,
            )
        else:
            logger.info(
                "start... device_key=%s  interval=%ss (upload disabled)",
                self.device_key,
                self.config.interval,
            )
        logger.info("disk /= %s", root_disk_usage())
        asyncio.run(self._main())

    async def _main(self) -> None:
        queue: asyncio.Queue = asyncio.Queue()
        # No baseline exists yet, so the first round stays with the instantaneous
        # fields; sleeping ``0`` before it means a start is not met by an idle
        # terminal for a whole interval.
        prev_disk = prev_net = prev_time = None
        delay = 0.0

        # Probe once before the loop so the very first sample already carries GPU
        # data. A host without the binary, or with one that reports no device, is
        # left on the empty snapshot and never queried again.
        if shutil.which("nvidia-smi"):
            self._gpus = await asyncio.to_thread(get_gpu_info)
            logger.info("gpu: %d nvidia-smi device(s) detected", len(self._gpus))
            if self._gpus:
                asyncio.create_task(self._poll_gpus())

        # ``trust_env=False`` pins the client to a direct connection: it ignores
        # the ``*_PROXY`` environment variables (httpx rejects schemes it does not
        # recognise, such as ``socks://``, and would fail on client construction)
        # and skips ``.netrc`` lookups.
        async with httpx.AsyncClient(trust_env=False) as client:
            if self.config.upload:
                asyncio.create_task(self._sender(client, queue))
            while True:
                await asyncio.sleep(delay)
                delay = self.config.interval
                sample = self._build_sample(prev_disk, prev_net, prev_time)
                prev_disk, prev_net, prev_time = (
                    psutil.disk_io_counters(),
                    psutil.net_io_counters(),
                    float(sample.ts),
                )
                if not self.config.quiet:
                    print(sample.status_line())
                if self.config.upload and sample.measured:
                    queue.put_nowait(self._encrypt(sample))

    async def _poll_gpus(self) -> None:
        """Keep the GPU snapshot fresh outside the event loop.

        ``nvidia-smi`` is a subprocess that on its own can take longer than the
        sampling interval, so the query runs in a worker thread and the sampler
        reads whichever snapshot is current: GPU figures may trail the other
        metrics by up to one interval, but the cadence stays fixed.
        """
        while True:
            self._gpus = await asyncio.to_thread(get_gpu_info)
            await asyncio.sleep(self.config.interval)

    def _build_sample(self, prev_disk: Any, prev_net: Any, prev_time: float | None) -> Sample:
        """Collect one sample.

        ``prev_disk`` / ``prev_net`` / ``prev_time`` are the previous counter
        snapshot and the moment it was taken; every rate is the change between it
        and now, per second. Without them (the round right after startup) only the
        instantaneous fields are filled in. The CPU call happens either way: it
        opens the window that the next call measures against, since a first call
        with no previous reading returns ``0.0``.
        """
        now = time.time()
        disk = psutil.disk_io_counters()
        net = psutil.net_io_counters()
        cpu_temperature, disk_temperature = read_temperatures()
        cpu_percent = psutil.cpu_percent(interval=None)

        vm = psutil.virtual_memory()
        disks = []
        for part in psutil.disk_partitions(all=False):
            try:
                usage = psutil.disk_usage(part.mountpoint)
            except OSError:
                continue
            disks.append(DiskUsage(part.mountpoint, int(usage.used), int(usage.total)))

        sample = Sample(
            ts=int(now),
            rid=secrets.token_hex(16),
            mem_used=int(vm.used),
            mem_total=int(vm.total),
            disks=disks,
            cpu_temp=int(cpu_temperature * 10),
            disk_temp=int(disk_temperature * 10),
            gpus=list(self._gpus),
        )
        if prev_disk is None or prev_net is None or prev_time is None:
            return sample  # warm-up: no window to measure the rates over

        dt = now - prev_time
        # ``busy_time`` is the aggregate milliseconds with I/O in flight, so the
        # share of the window it covers is the ``iostat`` ``%util``. Summing every
        # device can exceed one disk's range on multi-disk hosts, hence the cap.
        busy = min(100.0, max(0.0, (disk.busy_time - prev_disk.busy_time) / (dt * 1000) * 100))
        sample.window = dt
        sample.cpu_usage = int(cpu_percent * 100)
        sample.disk_read = int((disk.read_bytes - prev_disk.read_bytes) / dt)
        sample.disk_write = int((disk.write_bytes - prev_disk.write_bytes) / dt)
        sample.net_in = int((net.bytes_recv - prev_net.bytes_recv) / dt)
        sample.net_out = int((net.bytes_sent - prev_net.bytes_sent) / dt)
        ops = disk.read_count + disk.write_count - prev_disk.read_count - prev_disk.write_count
        sample.disk_iops = int(ops / dt)
        sample.disk_busy = int(busy * 100)
        return sample

    def _encrypt(self, sample: Sample) -> dict:
        """Wrap the sample with AES-256-GCM; the AES key goes RSA-OAEP encrypted."""
        aes_key = secrets.token_bytes(32)
        nonce = secrets.token_bytes(12)
        ciphertext = AESGCM(aes_key).encrypt(nonce, json.dumps(sample.to_payload()).encode(), None)
        encrypted_key = self.public_key.encrypt(
            aes_key,
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None,
            ),
        )
        return {
            "ts": sample.ts,
            "device_key": self.device_key,
            "encrypted_key": base64.b64encode(encrypted_key).decode(),
            "nonce": base64.b64encode(nonce).decode(),
            "ciphertext": base64.b64encode(ciphertext).decode(),
        }

    def _stale(self, body: dict) -> bool:
        """Whether the sample has aged out of the server's anti-replay window."""
        return time.time() - body["ts"] >= REPLAY_WINDOW

    async def _send_sample(self, client: httpx.AsyncClient, body: dict) -> None:
        """Send one sample, retrying with exponential backoff until the anti-replay
        window expires. Failures (incl. DNS and timeouts) are treated as a kind
        of response, so retry info is appended to the same line rather than
        logging extra entries.

        The backoff counter is shared across samples (``self._attempt``): a
        persistent outage keeps the delay at its maximum instead of restarting
        the ladder at 1s for every queued sample, and only a successful send
        resets it."""
        tag = f"[sample {_fmt_ts(body['ts'])}]"
        while not self._stale(body):
            try:
                resp = await client.post(self.config.server_url, json=body, timeout=UPLOAD_TIMEOUT)
            except httpx.TransportError as e:
                message = f"upload failed: {e}"
            else:
                code = resp.status_code
                if code == 200:
                    logger.info("%s upload -> 200 %s", tag, resp.text[:120])
                    self._attempt = 0
                    return
                if code == 409:
                    logger.info("%s upload -> 409 replay, already accepted", tag)
                    self._attempt = 0
                    return
                if code == 429 or code >= 500:
                    message = f"upload -> {code} {resp.text[:120]}"
                else:
                    logger.info("%s upload -> %s drop: %s", tag, code, resp.text[:120])
                    return

            if self._stale(body):
                logger.info("%s %s | drop after %ss window", tag, message, REPLAY_WINDOW)
                return
            delay = min(_BACKOFF_MAX, 2**self._attempt)
            self._attempt += 1
            logger.info("%s %s | retry in %ss", tag, message, delay)
            await asyncio.sleep(delay)

    async def _sender(self, client: httpx.AsyncClient, queue: asyncio.Queue):
        while True:
            body = await queue.get()
            if body is None:
                return
            if self._stale(body):
                logger.info("[sample %s] drop stale sample", _fmt_ts(body["ts"]))
                continue
            await self._send_sample(client, body)


if __name__ == "__main__":
    Monitor(Settings()).run()  # pragma: no cover - manual debugging helper
