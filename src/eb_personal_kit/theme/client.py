"""Sunrise/sunset driven system colour-scheme switcher.

Two orthogonal dimensions:
- action (``mode``): ``toggle`` flips the current OS colour scheme, ``auto``
  switches per the current sun position (light during daytime, dark after
  dusk), computed offline via ``astral``.
- repetition (``loop``): run the action once, or keep repeating it on a
  fixed cadence.

Supported platforms:
- Windows: ``AppsUseLightTheme`` / ``SystemUsesLightTheme`` registry values
  (per ``target``), followed by a ``WM_SETTINGCHANGE`` broadcast so the
  change takes effect immediately.
- Linux (GNOME): ``gsettings color-scheme``.
- macOS: AppleScript ``dark mode`` / ``defaults``.

``astral`` is imported lazily; the platform applier and current-theme getter
are resolved once at construction and are injectable for tests.
"""

from __future__ import annotations

import logging
import shlex
import shutil
import subprocess
import sys
import time
from datetime import datetime, tzinfo
from pathlib import Path

from eb_personal_kit.theme.config import Settings

logger = logging.getLogger("theme")

WINREG_KEY = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"

# WM_SETTINGCHANGE broadcast parameters: HWND_BROADCAST, and a short timeout
# so an unresponsive window cannot stall the switcher.
_HWND_BROADCAST = 0xFFFF
_WM_SETTINGCHANGE = 0x001A
_SMTO_ABORTIFHUNG = 0x0002
_BROADCAST_TIMEOUT_MS = 1000

# Registry values written/read per ``target`` (1 = light, 0 = dark).
_WINREG_NAMES = {
    "apps": ("AppsUseLightTheme",),
    "system": ("SystemUsesLightTheme",),
    "both": ("AppsUseLightTheme", "SystemUsesLightTheme"),
}

# Name of the autostart entry (registry value / systemd unit / launchd label).
AUTOSTART_TASK_NAME = "eb-theme"
_WIN_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def local_timezone() -> tzinfo:
    """Return the process-local timezone as a ``tzinfo``."""
    return datetime.now().astimezone().tzinfo  # type: ignore[return-value]


def sun_times(latitude: float, longitude: float, day, tzinfo: tzinfo) -> tuple[datetime, datetime]:
    """Return ``(sunrise, sunset)`` for ``day`` at the given location.

    Raises ``ValueError`` on polar day/night (astral's message names which:
    "Sun is always above/below the horizon").
    """
    from astral import Observer
    from astral.sun import sunrise, sunset

    observer = Observer(latitude=latitude, longitude=longitude)
    return (
        sunrise(observer, date=day, tzinfo=tzinfo),
        sunset(observer, date=day, tzinfo=tzinfo),
    )


def is_dark(now: datetime, latitude: float, longitude: float, tzinfo: tzinfo) -> bool:
    """Whether ``now`` falls outside the sunrise..sunset span (i.e. dark).

    Polar day (sun never sets) is always light; polar night (sun never rises)
    is always dark.
    """
    try:
        sunrise, sunset = sun_times(latitude, longitude, now.date(), tzinfo)
    except ValueError as err:
        # astral 3.x polar-day/night messages; keep the legacy matchers for
        # other astral versions.
        message = str(err)
        if "always above" in message or "never sets" in message:
            return False
        if "always below" in message or "never rises" in message:
            return True
        raise
    return not (sunrise <= now < sunset)


def _apply_windows(dark: bool, target: str) -> None:
    import ctypes
    import winreg

    value = 0 if dark else 1
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, WINREG_KEY, 0, winreg.KEY_SET_VALUE) as key:
        for name in _WINREG_NAMES[target]:
            winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, value)

    # Broadcast so running applications pick the change up without re-login.
    ctypes.windll.user32.SendMessageTimeoutW(
        _HWND_BROADCAST,
        _WM_SETTINGCHANGE,
        0,
        "ImmersiveColorSet",
        _SMTO_ABORTIFHUNG,
        _BROADCAST_TIMEOUT_MS,
        None,
    )


def _current_dark_windows(target: str) -> bool:
    import winreg

    name = _WINREG_NAMES[target][0]
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, WINREG_KEY) as key:
        value, _ = winreg.QueryValueEx(key, name)
    return not value


def _apply_linux(dark: bool, target: str) -> None:
    scheme = "prefer-dark" if dark else "prefer-light"
    subprocess.run(
        ["gsettings", "set", "org.gnome.desktop.interface", "color-scheme", scheme],
        check=True,
    )


def _current_dark_linux(target: str) -> bool:
    out = subprocess.run(
        ["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return "dark" in out


def _apply_macos(dark: bool, target: str) -> None:
    script = (
        'tell app "System Events" to tell appearance preferences '
        f"to set dark mode to {str(dark).lower()}"
    )
    subprocess.run(["osascript", "-e", script], check=True)


def _current_dark_macos(target: str) -> bool:
    # Errors (key missing) mean the default light appearance.
    out = subprocess.run(
        ["defaults", "read", "-g", "AppleInterfaceStyle"], capture_output=True, text=True
    ).stdout
    return out.strip() == "Dark"


def platform_applier():
    """Return the default applier for the current platform."""
    if sys.platform == "win32":
        return _apply_windows
    if sys.platform == "darwin":
        return _apply_macos
    return _apply_linux


def current_dark_getter():
    """Return the default current-theme getter for the current platform."""
    if sys.platform == "win32":
        return _current_dark_windows
    if sys.platform == "darwin":
        return _current_dark_macos
    return _current_dark_linux


def install_autostart(command: list[str]) -> str:
    """Register ``command`` to run at logon; return a human-readable location.

    - Windows: an ``HKCU ... \\Run`` value (no admin rights required).
    - Linux: a systemd user unit, enabled with ``systemctl --user``.
    - macOS: a launchd agent loaded into ``~/Library/LaunchAgents``.
    """
    if sys.platform == "win32":
        return _install_windows(command)
    if sys.platform == "darwin":
        return _install_macos(command)
    return _install_linux(command)


def autostart_command(config: Settings) -> list[str]:
    """Build the command line that reproduces ``config`` after re-login.

    Every option is written out explicitly so the entry does not depend on
    the working directory (and thus on any ``.env`` file) at logon time.
    On Windows the windowed ``pythonw.exe`` interpreter (sitting next to
    ``sys.executable``) is preferred so the loop runs silently, with no
    console window or taskbar presence.
    """
    if sys.platform == "win32":
        windowed = Path(sys.executable).with_name("pythonw.exe")
        python = str(windowed) if windowed.exists() else sys.executable
        exe = [python, "-m", "eb_personal_kit.cli", "theme"]
    else:
        script = shutil.which("eb")
        if script:
            exe = [script, "theme"]
        else:
            exe = [sys.executable, "-m", "eb_personal_kit.cli", "theme"]
    command = [
        *exe,
        "--latitude",
        str(config.latitude),
        "--longitude",
        str(config.longitude),
        "--target",
        config.target,
        "--check-interval",
        str(config.check_interval),
        "--log-level",
        config.log_level.upper(),
    ]
    if config.mode == "auto":
        command.insert(len(exe), "--auto")
    if config.loop:
        command.insert(len(exe) + (1 if config.mode == "auto" else 0), "--loop")
    return command


def uninstall_autostart() -> str | None:
    """Remove the autostart entry; return the removed location, or None if absent."""
    if sys.platform == "win32":
        return _uninstall_windows()
    if sys.platform == "darwin":
        return _uninstall_macos()
    return _uninstall_linux()


def _uninstall_windows() -> str | None:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _WIN_RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, AUTOSTART_TASK_NAME)
    except FileNotFoundError:
        return None
    return f"HKCU\\{_WIN_RUN_KEY}\\{AUTOSTART_TASK_NAME}"


def _uninstall_linux() -> str | None:
    unit = Path.home() / ".config" / "systemd" / "user" / f"{AUTOSTART_TASK_NAME}.service"
    if not unit.exists():
        return None
    subprocess.run(
        ["systemctl", "--user", "disable", "--now", f"{AUTOSTART_TASK_NAME}.service"],
        check=False,
    )
    unit.unlink()
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    return str(unit)


def _uninstall_macos() -> str | None:
    label = f"cn.eleksbai.{AUTOSTART_TASK_NAME}"
    plist = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
    if not plist.exists():
        return None
    subprocess.run(["launchctl", "unload", str(plist)], check=False)
    plist.unlink()
    return str(plist)


def _install_windows(command: list[str]) -> str:
    import winreg

    value = subprocess.list2cmdline(command)
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _WIN_RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, AUTOSTART_TASK_NAME, 0, winreg.REG_SZ, value)
    return f"HKCU\\{_WIN_RUN_KEY}\\{AUTOSTART_TASK_NAME}"


def _install_linux(command: list[str]) -> str:
    unit_dir = Path.home() / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True, exist_ok=True)
    unit = unit_dir / f"{AUTOSTART_TASK_NAME}.service"
    unit.write_text(
        "[Unit]\n"
        "Description=eb-personal-kit theme switcher\n"
        "\n"
        "[Service]\n"
        "Type=simple\n"
        f"ExecStart={' '.join(shlex.quote(part) for part in command)}\n"
        "Restart=on-failure\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n",
        encoding="utf-8",
    )
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(
        ["systemctl", "--user", "enable", "--now", f"{AUTOSTART_TASK_NAME}.service"],
        check=True,
    )
    return str(unit)


def _install_macos(command: list[str]) -> str:
    label = f"cn.eleksbai.{AUTOSTART_TASK_NAME}"
    plist = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
    plist.parent.mkdir(parents=True, exist_ok=True)
    arguments = "".join(f"        <string>{part}</string>\n" for part in command)
    plist.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n<dict>\n'
        f"    <key>Label</key><string>{label}</string>\n"
        "    <key>ProgramArguments</key>\n    <array>\n"
        f"{arguments}    </array>\n"
        "    <key>RunAtLoad</key><true/>\n"
        "</dict>\n</plist>\n",
        encoding="utf-8",
    )
    subprocess.run(["launchctl", "load", str(plist)], check=True)
    return str(plist)


class ThemeSwitcher:
    """Switch the OS colour scheme between light and dark.

    ``config.mode`` selects the action (``toggle`` or ``auto``) and
    ``config.loop`` whether it is repeated on the check cadence.
    """

    def __init__(self, config: Settings, applier=None, current_dark=None):
        self.config = config
        self._apply = applier or platform_applier()
        self._current_dark = current_dark or current_dark_getter()
        # None until the first auto check, so the loop always applies at start.
        self._last: bool | None = None

    def run(self) -> None:
        """Run the selected action once, or in a loop with ``loop``.

        With ``dry_run`` the decision (and today's sun times, at DEBUG) is
        logged but nothing is written to the system.
        """
        logger.info(
            "start... mode=%s%s  lat=%s  lon=%s  target=%s%s",
            self.config.mode,
            " loop" if self.config.loop else "",
            self.config.latitude,
            self.config.longitude,
            self.config.target,
            "  [dry-run]" if self.config.dry_run else "",
        )
        action = self._toggle_once if self.config.mode == "toggle" else self._auto_once
        if not self.config.loop:
            action()
            return
        while True:
            try:
                action()
            except Exception:  # noqa: BLE001 - keep the loop alive across failures
                logger.exception("theme check failed")
            time.sleep(self.config.check_interval)

    def _toggle_once(self) -> None:
        """Flip the current OS colour scheme."""
        dark = self._current_dark(self.config.target)
        self._switch(not dark)

    def _auto_once(self) -> None:
        """Apply the sun position at most once per state change."""
        tzinfo = local_timezone()
        now = datetime.now(tzinfo)
        if logger.isEnabledFor(logging.DEBUG):
            self._log_sun_times(now, tzinfo)
        dark = is_dark(now, self.config.latitude, self.config.longitude, tzinfo)
        if dark is not self._last:
            self._switch(dark)
            self._last = dark

    def _switch(self, dark: bool) -> None:
        if self.config.dry_run:
            logger.info("[dry-run] would switch to %s", "dark" if dark else "light")
            return
        logger.info("switch to %s", "dark" if dark else "light")
        self._apply(dark, self.config.target)

    def _log_sun_times(self, now: datetime, tzinfo: tzinfo) -> None:
        """Log today's sunrise/sunset for verification (skipped on polar days)."""
        try:
            sunrise, sunset = sun_times(
                self.config.latitude, self.config.longitude, now.date(), tzinfo
            )
        except ValueError:  # polar day/night: is_dark handles the decision
            return
        logger.debug(
            "sunrise=%s  sunset=%s", sunrise.strftime("%H:%M:%S"), sunset.strftime("%H:%M:%S")
        )


if __name__ == "__main__":
    from eb_personal_kit.theme.config import Settings

    ThemeSwitcher(Settings()).run()  # pragma: no cover - manual debugging helper
