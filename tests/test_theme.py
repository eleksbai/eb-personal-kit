import subprocess
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from click.testing import CliRunner
from pydantic import ValidationError

from eb_personal_kit.theme.cli import main
from eb_personal_kit.theme.client import ThemeSwitcher, is_dark, sun_times
from eb_personal_kit.theme.config import Settings, get_settings

# Beijing coordinates: well-defined sunrise/sunset all year round.
LAT, LON = 39.9, 116.4
TZ = ZoneInfo("Asia/Shanghai")
UTC = ZoneInfo("UTC")
UTC8 = timezone(timedelta(hours=8))

ENV_NAMES = ("LATITUDE", "LONGITUDE", "CHECK_INTERVAL", "TARGET", "MODE", "LOG_LEVEL")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path):
    for name in ENV_NAMES:
        monkeypatch.delenv(f"EB_THEME_{name}", raising=False)
    monkeypatch.chdir(tmp_path)  # isolate from any .env in the repo
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_settings_mode_defaults_to_toggle():
    settings = Settings()
    assert settings.mode == "toggle"
    assert settings.loop is False
    assert settings.check_interval == 60.0
    assert settings.target == "both"
    assert settings.dry_run is False
    assert settings.log_level == "INFO"


def test_settings_range_validation():
    with pytest.raises(ValidationError):
        Settings(latitude=91, longitude=LON)
    with pytest.raises(ValidationError):
        Settings(latitude=LAT, longitude=181)
    with pytest.raises(ValidationError):
        Settings(latitude=LAT, longitude=LON, check_interval=0)
    with pytest.raises(ValidationError):
        Settings(latitude=LAT, longitude=LON, target="nope")
    with pytest.raises(ValidationError):
        Settings(latitude=LAT, longitude=LON, mode="nope")


def test_loads_dot_env(monkeypatch):
    from pathlib import Path

    Path(".env").write_text(
        f"EB_THEME_LATITUDE={LAT}\nEB_THEME_LONGITUDE={LON}\nEB_THEME_MODE=auto\n",
        encoding="utf-8",
    )
    settings = Settings()
    assert settings.latitude == LAT
    assert settings.longitude == LON
    assert settings.mode == "auto"


def test_get_settings_is_cached():
    kwargs = {"latitude": LAT, "longitude": LON}
    assert get_settings(**kwargs) is get_settings(**kwargs)


def beijing(hour: int) -> datetime:
    return datetime(2026, 6, 15, hour, 0, tzinfo=UTC8)


def test_midday_is_light():
    assert not is_dark(beijing(12), LAT, LON, TZ)


def test_night_is_dark():
    assert is_dark(beijing(3), LAT, LON, TZ)  # before sunrise
    assert is_dark(beijing(23), LAT, LON, TZ)  # after sunset


def test_sun_times_ordered():
    sunrise, sunset = sun_times(LAT, LON, beijing(12).date(), TZ)
    assert sunrise < sunset
    assert sunrise.tzinfo is not None


def test_polar_day_is_light():
    # 80N at the June solstice: the sun never sets.
    assert not is_dark(datetime(2026, 6, 21, 12, tzinfo=UTC), 80, 0, UTC)


def test_polar_night_is_dark():
    # 80N at the December solstice: the sun never rises.
    assert is_dark(datetime(2026, 12, 21, 12, tzinfo=UTC), 80, 0, UTC)


def test_linux_applier(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "eb_personal_kit.theme.client.subprocess.run", lambda cmd, **kwargs: calls.append(cmd)
    )
    from eb_personal_kit.theme.client import _apply_linux, _current_dark_linux

    _apply_linux(True, "both")
    _apply_linux(False, "both")
    assert calls[0][-1] == "prefer-dark"
    assert calls[1][-1] == "prefer-light"

    monkeypatch.setattr(
        "eb_personal_kit.theme.client.subprocess.run",
        lambda cmd, **kwargs: type("R", (), {"stdout": "prefer-dark\n"})(),
    )
    assert _current_dark_linux("both") is True


@pytest.mark.skipif(sys.platform != "win32", reason="Windows registry")
def test_windows_applier(monkeypatch):
    import ctypes
    import winreg

    written = {}
    read_values = {"AppsUseLightTheme": 0, "SystemUsesLightTheme": 1}
    broadcasts = []

    class FakeKey:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(winreg, "OpenKey", lambda *args, **kwargs: FakeKey())
    monkeypatch.setattr(
        winreg, "SetValueEx", lambda key, name, res, typ, val: written.update({name: val})
    )
    monkeypatch.setattr(
        winreg, "QueryValueEx", lambda key, name: (read_values[name], winreg.REG_DWORD)
    )
    monkeypatch.setattr(
        ctypes,
        "windll",
        type(
            "FakeWindll",
            (),
            {
                "user32": type(
                    "FakeUser32",
                    (),
                    {"SendMessageTimeoutW": staticmethod(lambda *args: broadcasts.append(args))},
                )
            },
        ),
    )

    from eb_personal_kit.theme.client import _apply_windows, _current_dark_windows

    _apply_windows(True, "both")
    assert written == {"AppsUseLightTheme": 0, "SystemUsesLightTheme": 0}
    _apply_windows(False, "apps")
    # The second call only touches the ``apps`` value.
    assert written["AppsUseLightTheme"] == 1
    assert broadcasts  # WM_SETTINGCHANGE broadcast is sent

    assert _current_dark_windows("both") is True  # AppsUseLightTheme == 0


def _fake_theme(monkeypatch, current_dark=True):
    """Inject a fake applier/current getter; return the applied-switch list."""
    applied = []
    monkeypatch.setattr(
        "eb_personal_kit.theme.client.platform_applier",
        lambda: lambda dark, target: applied.append(("apply", dark, target)),
    )
    monkeypatch.setattr(
        "eb_personal_kit.theme.client.current_dark_getter",
        lambda: lambda target: current_dark,
    )
    return applied


def test_toggle_flips_current_theme(monkeypatch):
    applied = _fake_theme(monkeypatch, current_dark=True)
    ThemeSwitcher(Settings()).run()
    assert applied == [("apply", False, "both")]  # dark -> light


def test_toggle_dry_run_does_not_apply(monkeypatch):
    applied = _fake_theme(monkeypatch, current_dark=True)
    ThemeSwitcher(Settings(dry_run=True)).run()
    assert applied == []


def test_auto_applies_sun_position_once(monkeypatch):
    applied = _fake_theme(monkeypatch)
    ThemeSwitcher(Settings(latitude=LAT, longitude=LON, mode="auto")).run()
    assert len(applied) == 1
    assert isinstance(applied[0][1], bool)


def _stop_loop_after(count: int, sleeps: list):
    """Return a ``time.sleep`` replacement that aborts the loop after ``count`` calls."""

    def fake_sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) >= count:
            raise KeyboardInterrupt

    return fake_sleep


def test_loop_auto_reapplies_on_state_change(monkeypatch):
    applied = _fake_theme(monkeypatch)
    # Alternate the sun position on every check; stop the loop after 3 sleeps.
    checks = iter([True, False, True])
    monkeypatch.setattr("eb_personal_kit.theme.client.is_dark", lambda *args: next(checks))
    sleeps: list = []
    monkeypatch.setattr("eb_personal_kit.theme.client.time.sleep", _stop_loop_after(3, sleeps))
    with pytest.raises(KeyboardInterrupt):
        ThemeSwitcher(Settings(latitude=LAT, longitude=LON, mode="auto", loop=True)).run()
    assert [dark for _, dark, _ in applied] == [True, False, True]
    assert sleeps == [60.0, 60.0, 60.0]


def test_loop_toggles_every_cycle(monkeypatch):
    applied = _fake_theme(monkeypatch)
    # The reported current state alternates: without ``--auto`` the loop
    # flips the theme on every cycle (documented oscillation).
    states = iter([True, False, True])
    monkeypatch.setattr(
        "eb_personal_kit.theme.client.current_dark_getter",
        lambda: lambda target: next(states),
    )
    sleeps: list = []
    monkeypatch.setattr("eb_personal_kit.theme.client.time.sleep", _stop_loop_after(3, sleeps))
    with pytest.raises(KeyboardInterrupt):
        ThemeSwitcher(Settings(loop=True)).run()
    assert [dark for _, dark, _ in applied] == [False, True, False]


def test_main_auto_and_loop_combine(monkeypatch):
    applied = _fake_theme(monkeypatch)
    monkeypatch.setattr(
        "eb_personal_kit.theme.client.time.sleep",
        _stop_loop_after(1, []),
    )
    result = CliRunner().invoke(
        main,
        ["--latitude", str(LAT), "--longitude", str(LON), "--auto", "--loop"],
    )
    # Click converts the loop's KeyboardInterrupt abort into exit code 1.
    assert result.exit_code == 1
    assert "Aborted!" in result.output
    assert len(applied) == 1  # one auto check ran before the abort


def test_autostart_command_prefers_console_script(monkeypatch):
    from eb_personal_kit.theme.client import autostart_command

    monkeypatch.setattr("eb_personal_kit.theme.client.sys.platform", "linux")
    monkeypatch.setattr(
        "eb_personal_kit.theme.client.shutil.which", lambda name: "/usr/local/bin/eb"
    )
    cmd = autostart_command(
        Settings(
            mode="auto",
            loop=True,
            latitude=1.5,
            longitude=2.5,
            check_interval=300,
            target="apps",
            log_level="debug",
        )
    )
    assert cmd[:2] == ["/usr/local/bin/eb", "theme"]
    assert cmd[2:4] == ["--auto", "--loop"]
    assert cmd[cmd.index("--latitude") + 1] == "1.5"
    assert cmd[cmd.index("--check-interval") + 1] == "300.0"
    assert cmd[cmd.index("--log-level") + 1] == "DEBUG"


def test_autostart_command_falls_back_to_module(monkeypatch):
    from eb_personal_kit.theme.client import autostart_command

    monkeypatch.setattr("eb_personal_kit.theme.client.sys.platform", "linux")
    monkeypatch.setattr("eb_personal_kit.theme.client.shutil.which", lambda name: None)
    cmd = autostart_command(Settings())
    assert cmd[:4] == [sys.executable, "-m", "eb_personal_kit.cli", "theme"]
    assert "--auto" not in cmd and "--loop" not in cmd  # default toggle mode


def test_autostart_command_windows_prefers_pythonw(monkeypatch, tmp_path):
    from eb_personal_kit.theme.client import autostart_command

    monkeypatch.setattr("eb_personal_kit.theme.client.sys.platform", "win32")
    pythonw = tmp_path / "pythonw.exe"
    pythonw.touch()
    monkeypatch.setattr("eb_personal_kit.theme.client.sys.executable", str(tmp_path / "python.exe"))
    cmd = autostart_command(Settings(mode="auto", loop=True))
    assert cmd[:4] == [str(pythonw), "-m", "eb_personal_kit.cli", "theme"]
    assert cmd[4:6] == ["--auto", "--loop"]


def test_autostart_command_windows_falls_back_without_pythonw(monkeypatch, tmp_path):
    from eb_personal_kit.theme.client import autostart_command

    monkeypatch.setattr("eb_personal_kit.theme.client.sys.platform", "win32")
    # An isolated dir with no pythonw.exe sibling: keep the plain interpreter.
    console = tmp_path / "python.exe"
    monkeypatch.setattr("eb_personal_kit.theme.client.sys.executable", str(console))
    cmd = autostart_command(Settings())
    assert cmd[:4] == [str(console), "-m", "eb_personal_kit.cli", "theme"]
    assert "--auto" not in cmd and "--loop" not in cmd  # default toggle mode


@pytest.mark.skipif(sys.platform != "win32", reason="Windows registry")
def test_windows_install_writes_run_key(monkeypatch):
    import winreg

    written = {}

    class FakeKey:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(winreg, "OpenKey", lambda *args, **kwargs: FakeKey())
    monkeypatch.setattr(
        winreg, "SetValueEx", lambda key, name, res, typ, val: written.update({name: val})
    )

    from eb_personal_kit.theme.client import _install_windows

    location = _install_windows([r"C:\bin\eb-theme.exe", "--auto"])
    assert written["eb-theme"] == "C:\\bin\\eb-theme.exe --auto"
    assert "Run" in location


def test_linux_install_writes_user_unit(monkeypatch, tmp_path):
    from eb_personal_kit.theme.client import _install_linux

    calls = []
    monkeypatch.setattr(
        "eb_personal_kit.theme.client.subprocess.run",
        lambda cmd, **kwargs: calls.append(cmd),
    )
    monkeypatch.setattr("eb_personal_kit.theme.client.Path.home", lambda: tmp_path)

    location = _install_linux(["/usr/bin/eb-theme", "--auto"])
    unit = tmp_path / ".config" / "systemd" / "user" / "eb-theme.service"
    assert unit.exists()
    assert "ExecStart=/usr/bin/eb-theme --auto" in unit.read_text(encoding="utf-8")
    assert location == str(unit)
    assert calls[0] == ["systemctl", "--user", "daemon-reload"]
    assert calls[1][-1] == "eb-theme.service"


def test_main_install_registers_autostart(monkeypatch):
    applied = _fake_theme(monkeypatch)
    captured = {}
    monkeypatch.setattr(
        "eb_personal_kit.theme.client.install_autostart",
        lambda cmd: captured.update(cmd=cmd) or "LOCATION",
    )
    monkeypatch.setattr("eb_personal_kit.theme.client.sys.platform", "linux")
    monkeypatch.setattr("eb_personal_kit.theme.client.shutil.which", lambda name: "/x/eb")
    result = CliRunner().invoke(main, ["--install", "--auto", "--loop"])
    assert result.exit_code == 0, result.output
    assert "LOCATION" in result.output
    # The report repeats the registered command (and the no-console log path
    # when it resolves to pythonw.exe).
    assert captured["cmd"][:2] == ["/x/eb", "theme"]
    assert captured["cmd"][2:4] == ["--auto", "--loop"]
    assert "Command:" in result.output
    assert applied == []


def test_main_install_reports_console_log_for_pythonw(monkeypatch, tmp_path):
    import eb_personal_kit.utils

    _fake_theme(monkeypatch)
    monkeypatch.setattr("eb_personal_kit.theme.client.install_autostart", lambda cmd: "LOCATION")
    monkeypatch.setattr("eb_personal_kit.theme.client.sys.platform", "win32")
    monkeypatch.setattr("eb_personal_kit.theme.client.shutil.which", lambda name: None)
    pythonw = tmp_path / "pythonw.exe"
    pythonw.touch()
    monkeypatch.setattr("eb_personal_kit.theme.client.sys.executable", str(tmp_path / "python.exe"))
    log_dir = tmp_path / "logs"
    monkeypatch.setattr(eb_personal_kit.utils, "LOG_DIR", log_dir)
    result = CliRunner().invoke(main, ["--install", "--auto", "--loop"])
    assert result.exit_code == 0, result.output
    assert "Command:" in result.output
    assert str(pythonw) in result.output
    assert str(log_dir / "theme.log") in result.output


def test_main_uninstall_reports_missing_entry(monkeypatch):
    monkeypatch.setattr("eb_personal_kit.theme.client.uninstall_autostart", lambda: None)
    result = CliRunner().invoke(main, ["--uninstall"])
    assert result.exit_code == 0, result.output
    assert "No autostart entry found" in result.output


def test_main_uninstall_removes_entry(monkeypatch):
    removed = []

    def fake_uninstall():
        removed.append(1)
        return "LOCATION"

    monkeypatch.setattr("eb_personal_kit.theme.client.uninstall_autostart", fake_uninstall)
    result = CliRunner().invoke(main, ["--uninstall"])
    assert result.exit_code == 0, result.output
    assert "Removed autostart entry: LOCATION" in result.output


def test_main_rejects_install_and_uninstall_together():
    result = CliRunner().invoke(main, ["--install", "--uninstall"])
    assert result.exit_code != 0
    assert "mutually exclusive" in result.output


@pytest.mark.skipif(sys.platform != "win32", reason="Windows registry")
def test_windows_uninstall_deletes_run_value(monkeypatch):
    import winreg

    deleted = []

    class FakeKey:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(winreg, "OpenKey", lambda *args, **kwargs: FakeKey())
    existing = {"eb-theme"}

    def fake_delete(key, name):
        if name in existing:
            existing.remove(name)
            deleted.append(name)
        else:
            raise FileNotFoundError

    monkeypatch.setattr(winreg, "DeleteValue", fake_delete)

    from eb_personal_kit.theme.client import _uninstall_windows

    assert _uninstall_windows() is not None
    assert _uninstall_windows() is None  # already removed
    assert deleted == ["eb-theme"]


def test_linux_uninstall_removes_user_unit(monkeypatch, tmp_path):
    from eb_personal_kit.theme.client import _uninstall_linux

    calls = []
    monkeypatch.setattr(
        "eb_personal_kit.theme.client.subprocess.run",
        lambda cmd, **kwargs: calls.append(cmd),
    )
    monkeypatch.setattr("eb_personal_kit.theme.client.Path.home", lambda: tmp_path)

    assert _uninstall_linux() is None  # nothing installed

    unit = tmp_path / ".config" / "systemd" / "user" / "eb-theme.service"
    unit.parent.mkdir(parents=True)
    unit.write_text("[Unit]\n", encoding="utf-8")
    location = _uninstall_linux()
    assert location == str(unit)
    assert not unit.exists()
    assert calls[0][-1] == "eb-theme.service"
    assert calls[1] == ["systemctl", "--user", "daemon-reload"]


def test_main_reports_invalid_location():
    result = CliRunner().invoke(main, ["--auto", "--latitude", "91"])
    assert result.exit_code != 0
    assert "latitude" in result.output


def test_main_rejects_unknown_target():
    result = CliRunner().invoke(main, ["--auto", "--target", "nope"])
    assert result.exit_code != 0


def test_main_default_toggles(monkeypatch):
    applied = _fake_theme(monkeypatch, current_dark=True)
    result = CliRunner().invoke(main, [])
    assert result.exit_code == 0, result.output
    assert applied == [("apply", False, "both")]


def test_main_auto_dry_run(monkeypatch):
    applied = _fake_theme(monkeypatch)
    result = CliRunner().invoke(
        main, ["--latitude", str(LAT), "--longitude", str(LON), "--auto", "--dry-run"]
    )
    assert result.exit_code == 0, result.output
    assert applied == []


def test_main_log_level_option(monkeypatch):
    _fake_theme(monkeypatch, current_dark=True)
    result = CliRunner().invoke(main, ["--log-level", "debug"])
    assert result.exit_code == 0, result.output


def test_main_has_no_version_option():
    # Only the main ``eb`` tool carries a version flag.
    result = CliRunner().invoke(main, ["--version"])
    assert result.exit_code != 0


def test_entry_point_importable_without_astral():
    # astral must not be needed to load the CLI.
    code = (
        "import sys; import eb_personal_kit.theme.cli; import eb_personal_kit.theme.__main__; "
        "assert 'astral' not in sys.modules"
    )
    subprocess.run([sys.executable, "-c", code], check=True)
