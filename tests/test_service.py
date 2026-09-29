import subprocess
import sys
import sysconfig

from click.testing import CliRunner

from eb_tools.cli import main
from eb_tools.service import _read_secret_chars, default_exec_dir

INTERACTIVE_INPUT = "id-1\nkey-2\nexample.com\n\n\n"


def test_generate_ddns_service_matches_sample(tmp_path):
    result = CliRunner().invoke(
        main,
        [
            "service",
            "generate",
            "--name",
            "eb-ddns",
            "--exec-dir",
            "/usr/local/bin",
            "-o",
            str(tmp_path),
            "ddns",
        ],
        input=INTERACTIVE_INPUT,
    )
    assert result.exit_code == 0, result.output
    unit = (tmp_path / "eb-ddns.service").read_text(encoding="utf-8")
    assert unit == (
        "[Unit]\n"
        "Description=eb-ddns\n"
        "After=network.target\n"
        "\n"
        "[Service]\n"
        "Type=idle\n"
        "EnvironmentFile=/etc/eb/eb-ddns.env\n"
        "ExecStart=/usr/local/bin/eb-ddns\n"
        "ExecReload=/bin/kill -s HUP $MAINPID\n"
        "ExecStop=/bin/kill -s QUIT $MAINPID\n"
        "Restart=always\n"
        "RestartSec=30\n"
        "\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
    )
    # Only required fields are prompted; optional fields keep their defaults.
    env_file = (tmp_path / "eb-ddns.env").read_text(encoding="utf-8")
    assert env_file == (
        "EB_DDNS_TENCENTCLOUD_SECRET_ID=id-1\n"
        "EB_DDNS_TENCENTCLOUD_SECRET_KEY=key-2\n"
        "EB_DDNS_DOMAIN=example.com\n"
    )
    assert "sudo mv" in result.output  # install hint moves files, nothing kept
    assert "sudo chown root:root /etc/eb/eb-ddns.env" in result.output  # mv keeps file owner
    assert "/etc/eb/eb-ddns.env" in result.output


def test_generate_prompt_all_includes_optional_fields(tmp_path):
    result = CliRunner().invoke(
        main,
        [
            "service",
            "generate",
            "--name",
            "eb-ddns",
            "--exec-dir",
            "/usr/local/bin",
            "--prompt-all",
            "-o",
            str(tmp_path),
            "ddns",
        ],
        input=INTERACTIVE_INPUT,
    )
    assert result.exit_code == 0, result.output
    env_file = (tmp_path / "eb-ddns.env").read_text(encoding="utf-8")
    # Optional fields fall back to their Settings defaults on empty input.
    assert env_file == (
        "EB_DDNS_TENCENTCLOUD_SECRET_ID=id-1\n"
        "EB_DDNS_TENCENTCLOUD_SECRET_KEY=key-2\n"
        "EB_DDNS_DOMAIN=example.com\n"
        "EB_DDNS_IP_SERVER=https://eleksbai.cn/tools/ip\n"
        "EB_DDNS_ENABLE_DEBUG=false\n"
    )


def test_bin_option(tmp_path):
    # --bin decouples the ExecStart binary from the service name.
    result = CliRunner().invoke(
        main,
        [
            "service",
            "generate",
            "--name",
            "eb-monitor",
            "--bin",
            "eb-monitor-bin",
            "--exec-dir",
            "/usr/local/bin",
            "-o",
            str(tmp_path),
            "monitor",
        ],
        input=INTERACTIVE_INPUT,
    )
    assert result.exit_code == 0, result.output
    unit = (tmp_path / "eb-monitor.service").read_text(encoding="utf-8")
    assert "ExecStart=/usr/local/bin/eb-monitor-bin" in unit
    assert "EnvironmentFile=/etc/eb/eb-monitor.env" in unit


def test_read_secret_chars_supports_backspace(monkeypatch):
    monkeypatch.setattr("click.echo", lambda *a, **k: None)  # keep test output clean
    keys = iter("abc\x08\x7fd\r")
    assert _read_secret_chars(lambda: next(keys)) == "ad"


def test_secret_key_is_hidden_while_prompting(tmp_path):
    result = CliRunner().invoke(
        main,
        ["service", "generate", "--name", "eb-ddns", "-o", str(tmp_path), "ddns"],
        input=INTERACTIVE_INPUT,
    )
    assert result.exit_code == 0, result.output
    assert "key-2" not in result.output  # hide_input masks the secret
    assert "*" * 5 in result.output  # mask echoes back with the secret length


def test_default_name_and_type_option(tmp_path):
    result = CliRunner().invoke(
        main,
        ["service", "generate", "--type", "simple", "-o", str(tmp_path), "ddns"],
        input=INTERACTIVE_INPUT,
    )
    assert result.exit_code == 0, result.output
    unit = (tmp_path / "eb-ddns.service").read_text(encoding="utf-8")  # default name is eb-<module>
    assert "Type=simple" in unit
    assert "Description=eb-ddns" in unit


def test_unknown_module_reports_error():
    result = CliRunner().invoke(main, ["service", "generate", "nope"])
    assert result.exit_code != 0
    assert "unknown module" in result.output


def test_default_exec_dir_inside_venv(monkeypatch):
    monkeypatch.setattr(sys, "prefix", "/opt/venv")
    monkeypatch.setattr(sys, "base_prefix", "/usr")
    monkeypatch.setattr(sysconfig, "get_path", lambda name, scheme=None: "/opt/venv/bin")
    assert default_exec_dir() == "/opt/venv/bin"


def test_default_exec_dir_outside_venv(monkeypatch):
    monkeypatch.setattr(sys, "base_prefix", sys.prefix)  # simulate no virtualenv
    monkeypatch.setattr(sysconfig, "get_path", lambda name, scheme=None: "/home/user/.local/bin")
    assert default_exec_dir() == "/home/user/.local/bin"


def test_service_importable_without_optional_deps():
    # Loading the main CLI must not require the ddns extra (pydantic-settings etc.).
    code = (
        "import sys; import eb_tools.cli; import eb_tools.service; "
        "assert 'pydantic_settings' not in sys.modules; "
        "assert 'requests' not in sys.modules; assert 'tencentcloud' not in sys.modules"
    )
    subprocess.run([sys.executable, "-c", code], check=True)
