import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner
from pydantic import ValidationError

from eb_personal_kit.monitor.cli import main
from eb_personal_kit.monitor.config import Settings, get_settings

ENV_NAMES = ("PUBLIC_KEY_PATH", "SERVER_URL", "INTERVAL", "DEBUG", "UPLOAD", "QUIET")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path):
    for name in ENV_NAMES:
        monkeypatch.delenv(f"EB_MONITOR_{name}", raising=False)
    monkeypatch.chdir(tmp_path)  # isolate from any .env in the repo
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_settings_valid_without_upload_config():
    # Upload-related fields have no defaults but are optional unless enabled.
    settings = Settings()
    assert settings.public_key_path is None
    assert settings.server_url is None
    assert settings.upload is False
    assert settings.quiet is False


def test_upload_requires_public_key_and_server_url():
    with pytest.raises(ValidationError) as excinfo:
        Settings(upload=True)
    message = str(excinfo.value)
    assert "public_key_path" in message
    assert "server_url" in message

    settings = Settings(upload=True, public_key_path="/key.pem", server_url="http://s:8000")
    assert settings.upload is True


def test_loads_dot_env():
    Path(".env").write_text(
        "EB_MONITOR_PUBLIC_KEY_PATH=/etc/eb/monitor_rsa_public.pem\n"
        "EB_MONITOR_SERVER_URL=http://10.0.0.1:8000\n"
        "EB_MONITOR_INTERVAL=15\n",
        encoding="utf-8",
    )
    settings = Settings()
    assert settings.public_key_path == "/etc/eb/monitor_rsa_public.pem"
    assert settings.server_url == "http://10.0.0.1:8000"
    assert settings.interval == 15.0
    # Optional fields keep their defaults.
    assert settings.log_level == "INFO"


def test_env_overrides_dot_env(monkeypatch):
    Path(".env").write_text(
        "EB_MONITOR_PUBLIC_KEY_PATH=/from-file.pem\nEB_MONITOR_SERVER_URL=http://from-file:8000\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("EB_MONITOR_SERVER_URL", "http://from-env:8000")
    settings = Settings()
    assert settings.public_key_path == "/from-file.pem"
    assert settings.server_url == "http://from-env:8000"


def test_empty_env_var_falls_back_to_dot_env(monkeypatch):
    Path(".env").write_text(
        "EB_MONITOR_PUBLIC_KEY_PATH=/from-file.pem\nEB_MONITOR_SERVER_URL=http://from-file:8000\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("EB_MONITOR_PUBLIC_KEY_PATH", "")
    assert Settings().public_key_path == "/from-file.pem"


def test_get_settings_is_cached():
    kwargs = {"public_key_path": "/key.pem"}
    assert get_settings(**kwargs) is get_settings(**kwargs)


def test_get_settings_init_overrides_win(monkeypatch):
    monkeypatch.setenv("EB_MONITOR_SERVER_URL", "http://from-env:8000")
    settings = get_settings(
        public_key_path="/cli.pem",
        server_url="http://from-cli:8000",
        interval=30,
        log_level="DEBUG",
    )
    assert settings.public_key_path == "/cli.pem"
    assert settings.server_url == "http://from-cli:8000"
    assert settings.interval == 30.0
    assert settings.log_level == "DEBUG"


def test_defaults():
    settings = get_settings(public_key_path="/key.pem")
    assert settings.server_url is None
    assert settings.interval == 5.0


def test_main_reports_missing_upload_config():
    # Upload mode requires public_key_path and server_url.
    result = CliRunner().invoke(main, ["--upload"])
    assert result.exit_code != 0
    assert "public_key_path" in result.output
    assert "server_url" in result.output


def test_entry_point_importable_without_optional_deps():
    # The monitor dependencies (httpx / psutil / cryptography) must not be
    # needed to load the CLI.
    code = (
        "import sys; import eb_personal_kit.monitor.cli; import eb_personal_kit.monitor.__main__; "
        "assert 'pydantic_settings' not in sys.modules; "
        "assert 'httpx' not in sys.modules; "
        "assert 'psutil' not in sys.modules; "
        "assert 'cryptography' not in sys.modules"
    )
    subprocess.run([sys.executable, "-c", code], check=True)
