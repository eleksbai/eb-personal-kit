"""Monitor sampling client: collect system metrics and print/upload samples.

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
import socket
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

_MACHINE_ID_PATHS = ("/etc/machine-id", "/var/lib/dbus/machine-id")

REPLAY_WINDOW = 300  # server anti-replay window, seconds
_BACKOFF_MAX = 30


@dataclass
class DiskUsage:
    """Usage of one mounted filesystem."""

    mount: str  # mount point
    used: int  # Bytes
    total: int  # Bytes


@dataclass
class Sample:
    """One raw metrics sample; units follow the upload payload contract."""

    ts: int  # unix seconds
    rid: str  # random sample id
    cpu_usage: int  # percent x100
    mem_used: int  # Bytes
    mem_total: int  # Bytes
    disk_read: int  # B/s
    disk_write: int  # B/s
    disks: list[DiskUsage]  # per-mount usage
    cpu_temp: int  # degC x10
    net_in: int  # B/s
    net_out: int  # B/s

    def to_payload(self) -> dict:
        """Return the JSON-ready payload in the wire format the server expects
        (rates converted to the wire unit KB/s)."""
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
        }

    def status_line(self) -> str:
        """Render the volatile metrics as a fixed-width single line so columns
        align across consecutive samples. Widths are reserved for the largest
        value of each 1024-based unit step (e.g. ``1023.9KB``)."""
        return (
            "{time} cpu= {cpu:>5} | mem= {mem:<6}({pct:>5})"
            " | disk r= {disk_r:>8}  ,w= {disk_w:>8}"
            " | net in= {net_in:>8} ,out= {net_out:>8}"
            " | temp= {temp:>6}"
        ).format(
            time=time.strftime("%X", time.localtime(self.ts)),
            cpu=f"{self.cpu_usage / 100:3.1f}%",
            mem=human_bytes(self.mem_used),
            pct=f"{self.mem_used * 100 / self.mem_total:3.1f}%",
            disk_r=f"{human_bytes(self.disk_read)}/s",
            disk_w=f"{human_bytes(self.disk_write)}/s",
            net_in=f"{human_bytes(self.net_in)}/s",
            net_out=f"{human_bytes(self.net_out)}/s",
            temp=f"{self.cpu_temp / 10}°C",
        )


def get_device_key() -> str:
    for path in _MACHINE_ID_PATHS:
        try:
            value = Path(path).read_text().strip()
            if value:
                return value
        except OSError:
            continue
    return socket.gethostname()


def cpu_temp() -> float:
    try:
        groups = psutil.sensors_temperatures()
    except Exception:  # noqa: BLE001 - sensors are unavailable on many hosts
        return 0.0
    values = []
    for entries in (groups or {}).values():
        for e in entries:
            if e.current:
                values.append(e.current)
    return max(values) if values else 0.0


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
        asyncio.run(self._main())

    async def _main(self) -> None:
        queue: asyncio.Queue = asyncio.Queue()
        prev_disk = psutil.disk_io_counters()
        prev_net = psutil.net_io_counters()
        prev_time = time.time()

        async with httpx.AsyncClient() as client:
            if self.config.upload:
                asyncio.create_task(self._sender(client, queue))
            while True:
                await asyncio.sleep(self.config.interval)
                sample = self._build_sample(prev_disk, prev_net, prev_time)
                prev_disk, prev_net, prev_time = (
                    psutil.disk_io_counters(),
                    psutil.net_io_counters(),
                    float(sample.ts),
                )
                if not self.config.quiet:
                    print(sample.status_line())
                if self.config.upload:
                    queue.put_nowait(self._encrypt(sample))

    def _build_sample(self, prev_disk: Any, prev_net: Any, prev_time: float) -> Sample:
        """Collect one raw metrics sample."""
        now = time.time()
        dt = now - prev_time
        disk = psutil.disk_io_counters()
        net = psutil.net_io_counters()

        vm = psutil.virtual_memory()
        disks = []
        for part in psutil.disk_partitions(all=False):
            try:
                usage = psutil.disk_usage(part.mountpoint)
            except OSError:
                continue
            disks.append(DiskUsage(part.mountpoint, int(usage.used), int(usage.total)))

        return Sample(
            ts=int(now),
            rid=secrets.token_hex(16),
            cpu_usage=int(psutil.cpu_percent(interval=None) * 100),
            mem_used=int(vm.used),
            mem_total=int(vm.total),
            disk_read=int((disk.read_bytes - prev_disk.read_bytes) / dt),
            disk_write=int((disk.write_bytes - prev_disk.write_bytes) / dt),
            disks=disks,
            cpu_temp=int(cpu_temp() * 10),
            net_in=int((net.bytes_recv - prev_net.bytes_recv) / dt),
            net_out=int((net.bytes_sent - prev_net.bytes_sent) / dt),
        )

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
