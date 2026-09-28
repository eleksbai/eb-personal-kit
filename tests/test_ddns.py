import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner
from pydantic import SecretStr, ValidationError

from eb_tools.ddns.cli import main
from eb_tools.ddns.config import DEFAULT_IP_SERVER, Settings, get_settings

ENV_NAMES = ("TENCENTCLOUD_SECRET_ID", "TENCENTCLOUD_SECRET_KEY", "IP_SERVER", "DOMAIN", "DEBUG")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path):
    for name in ENV_NAMES:
        monkeypatch.delenv(f"EB_DDNS_{name}", raising=False)
    monkeypatch.chdir(tmp_path)  # isolate from any .env in the repo
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_required_fields_raise_when_missing():
    # Fields without defaults are required; pydantic reports them by name.
    with pytest.raises(ValidationError) as excinfo:
        Settings()
    message = str(excinfo.value)
    assert "tencentcloud_secret_id" in message
    assert "tencentcloud_secret_key" in message
    assert "domain" in message


def test_loads_dot_env():
    Path(".env").write_text(
        "EB_DDNS_TENCENTCLOUD_SECRET_ID=secret-id-value-123\n"
        "EB_DDNS_TENCENTCLOUD_SECRET_KEY=secret-key-value-456\n"
        "EB_DDNS_DOMAIN=example.com\n"
        "EB_DDNS_ENABLE_DEBUG=True\n",
        encoding="utf-8",
    )
    settings = Settings()
    assert settings.tencentcloud_secret_id.get_secret_value() == "secret-id-value-123"
    assert settings.tencentcloud_secret_key.get_secret_value() == "secret-key-value-456"
    assert settings.domain == "example.com"
    assert settings.enable_debug is True
    # Secrets must not leak through repr/logs.
    assert "secret-id-value-123" not in repr(settings)
    assert "secret-key-value-456" not in repr(settings)
    # Optional fields keep their defaults.
    assert settings.ip_server == DEFAULT_IP_SERVER


def test_env_overrides_dot_env(monkeypatch):
    Path(".env").write_text(
        "EB_DDNS_DOMAIN=file.example.com\n"
        "EB_DDNS_TENCENTCLOUD_SECRET_ID=file-id\n"
        "EB_DDNS_TENCENTCLOUD_SECRET_KEY=file-key\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("EB_DDNS_DOMAIN", "env.example.com")
    settings = Settings()
    assert settings.domain == "env.example.com"
    assert settings.tencentcloud_secret_id.get_secret_value() == "file-id"


def test_empty_env_var_falls_back_to_dot_env(monkeypatch):
    Path(".env").write_text(
        "EB_DDNS_TENCENTCLOUD_SECRET_ID=file-id\n"
        "EB_DDNS_TENCENTCLOUD_SECRET_KEY=file-key\n"
        "EB_DDNS_DOMAIN=example.com\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("EB_DDNS_TENCENTCLOUD_SECRET_ID", "")
    assert Settings().tencentcloud_secret_id.get_secret_value() == "file-id"


def test_get_settings_is_cached():
    kwargs = {
        "tencentcloud_secret_id": "id",
        "tencentcloud_secret_key": "key",
        "domain": "example.com",
    }
    assert get_settings(**kwargs) is get_settings(**kwargs)


def test_get_settings_init_overrides_win(monkeypatch):
    monkeypatch.setenv("EB_DDNS_DOMAIN", "env.example.com")
    settings = get_settings(
        tencentcloud_secret_id="cli-id",
        tencentcloud_secret_key="cli-key",
        domain="cli.example.com",
        enable_debug=True,
    )
    assert settings.tencentcloud_secret_id == SecretStr("cli-id")
    assert settings.domain == "cli.example.com"
    assert settings.enable_debug is True


def test_main_reports_missing_settings():
    result = CliRunner().invoke(main, [])
    assert result.exit_code != 0
    assert "tencentcloud_secret_id" in result.output
    assert "domain" in result.output


def test_entry_point_importable_without_optional_deps():
    # The ddns extra (pydantic-settings / requests / tencentcloud-sdk) must
    # not be needed to load the CLI.
    code = (
        "import sys; import eb_tools.ddns.cli; import eb_tools.ddns.__main__; "
        "assert 'pydantic_settings' not in sys.modules; "
        "assert 'requests' not in sys.modules; assert 'tencentcloud' not in sys.modules"
    )
    subprocess.run([sys.executable, "-c", code], check=True)
