"""Generate systemd service files for eb-tools submodules.

Exposed through the main CLI as ``eb service generate MODULE``. By default
only required environment variables are asked for interactively (secrets are
masked with ``*`` as they are typed and backspace works); pass ``--prompt-all``
to also configure optional fields. Values are written to a ``<name>.env``
environment file that the unit references via ``EnvironmentFile=`` (deployed
root-only, keeping secrets out of the globally readable unit file). Only
``click`` and the standard library are imported at module level so the main
CLI does not pull in optional dependencies.
"""

from __future__ import annotations

import importlib
import os
import sys
import sysconfig
from collections.abc import Callable
from pathlib import Path
from typing import Any

import click

# Fallback directory of console scripts when the user script path is unavailable.
PIP_DEFAULT_EXEC_DIR = "/usr/local/bin"

UNIT_TEMPLATE = """[Unit]
Description={name}
After=network.target

[Service]
Type={service_type}
EnvironmentFile=/etc/eb/{name}.env
ExecStart={exec_dir}/{bin}{extra_args}
ExecReload=/bin/kill -s HUP $MAINPID
ExecStop=/bin/kill -s QUIT $MAINPID
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
"""


def default_exec_dir() -> str:
    """Return the directory of the pip-installed console scripts.

    Inside a virtualenv this is the venv script directory; otherwise the
    current user's default script directory (``~/.local/bin``, matching
    ``pip install --user eb-tools``) is used.
    """
    if sys.prefix != getattr(sys, "base_prefix", sys.prefix):
        return sysconfig.get_path("scripts") or PIP_DEFAULT_EXEC_DIR
    return sysconfig.get_path("scripts", "posix_user") or PIP_DEFAULT_EXEC_DIR


def _load_settings(module: str) -> Any:
    """Return the ``Settings`` class defined in ``eb_tools.<module>.config``."""
    try:
        config_module = importlib.import_module(f"eb_tools.{module}.config")
    except ImportError as err:
        raise click.BadParameter(f"unknown module {module!r}: {err}", param_hint="MODULE") from err
    settings = getattr(config_module, "Settings", None)
    if settings is None:
        raise click.BadParameter(f"eb_tools.{module}.config has no Settings", param_hint="MODULE")
    return settings


def _is_secret(field: Any) -> bool:
    """Whether a pydantic FieldInfo annotation hides sensitive input."""
    return "SecretStr" in str(field.annotation)


def _is_needed(field: Any) -> bool:
    """Whether a field has no usable default (required or optional ``None``)."""
    return field.is_required() or field.get_default() is None


def _to_env(value: Any) -> str:
    """Render a Settings field value for an environment file line."""
    if isinstance(value, bool):
        return "true" if value else "false"
    get_secret = getattr(value, "get_secret_value", None)
    return get_secret() if get_secret is not None else str(value)


def _read_secret_chars(read_char: Callable[[], str]) -> str:
    """Consume keystrokes until Enter, rendering ``*`` per char; backspace deletes."""
    chars: list[str] = []
    while True:
        ch = read_char()
        if ch in ("\r", "\n"):
            return "".join(chars)
        if ch in ("\x00", "\xe0"):  # Windows special-key prefix; swallow the scancode
            read_char()
        elif ch in ("\x08", "\x7f"):  # Backspace / Delete
            if chars:
                chars.pop()
                click.echo("\b \b", nl=False)
        elif ch == "\x03":  # Ctrl-C (raw mode does not raise SIGINT)
            raise KeyboardInterrupt
        elif ch == "\x04":  # Ctrl-D as EOF on an empty line
            if not chars:
                raise EOFError
            return "".join(chars)
        elif ch:
            chars.append(ch)
            click.echo("*", nl=False)


def _prompt_secret(env_name: str) -> str:
    """Prompt for a secret with a live ``*`` mask and working backspace."""
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        # Piped/redirected IO (tests, scripts): fall back to plain hidden prompt.
        value = click.prompt(env_name, hide_input=True)
        if value:
            click.echo("*" * len(value))
        return value
    click.echo(f"{env_name}: ", nl=False)
    try:
        if os.name == "nt":
            import msvcrt

            value = _read_secret_chars(msvcrt.getwch)
        else:
            import termios
            import tty

            fd = sys.stdin.fileno()
            saved = termios.tcgetattr(fd)
            try:
                tty.setraw(fd)
                value = _read_secret_chars(lambda: sys.stdin.read(1))
            finally:
                termios.tcsetattr(fd, termios.TCSADRAIN, saved)
    finally:
        click.echo()
    return value


def _prompt_field(env_name: str, field: Any) -> str:
    """Resolve one env var interactively; optional fields default to Settings values."""
    if field.is_required():
        if _is_secret(field):
            return _prompt_secret(env_name)
        return click.prompt(env_name)
    default = field.get_default()
    if isinstance(default, bool):
        return "true" if click.confirm(env_name, default=default) else "false"
    if default is None:
        # Field without a default value: empty input keeps it unset
        # (``env_ignore_empty`` treats an empty line as unset).
        return click.prompt(env_name, default="", show_default=False)
    return click.prompt(env_name, default=_to_env(default))


@click.group(name="service")
def service() -> None:
    """Manage systemd service files for eb-tools modules.

    \b
    Examples:
      eb service generate ddns
      eb service generate --bin eb --extra-args "--quiet --upload" monitor
    """


@service.command()
@click.argument("module")
@click.option("--name", default=None, help="Service and executable name [default: eb-<module>].")
@click.option("--type", "service_type", default="idle", show_default=True, help="systemd type.")
@click.option(
    "--exec-dir",
    default=None,
    help="Directory of the installed console script [default: auto-detected].",
)
@click.option(
    "--bin",
    default=None,
    help="Executable file name referenced by ExecStart [default: same as --name].",
)
@click.option(
    "-o",
    "--output-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=Path.cwd,
    help="Directory where the service file is written [default: current directory].",
)
@click.option(
    "--extra-args",
    default="",
    help='Extra arguments appended to ExecStart, e.g. "--quiet --upload".',
)
@click.option(
    "--prompt-all",
    is_flag=True,
    default=False,
    help="Prompt for every Settings field; by default only required ones are asked.",
)
def generate(
    module: str,
    name: str | None,
    service_type: str,
    exec_dir: str | None,
    bin: str | None,
    output_dir: Path,
    extra_args: str,
    prompt_all: bool,
) -> None:
    """Generate a systemd unit for MODULE.

    \b
    Example:
      eb service generate --name eb-ddns ddns
      eb service generate --bin eb --extra-args "--quiet --upload" monitor

    By default only required environment variables are prompted for; optional
    fields keep their built-in defaults. Pass ``--prompt-all`` to configure
    every field. Values are written to a ``<name>.env`` environment file that
    the unit references via ``EnvironmentFile=``; the ExecStart points to the
    pip-installed console script named after ``--name``.
    """
    settings = _load_settings(module)
    env_prefix = settings.model_config.get("env_prefix", "")
    name = name or f"eb-{module}"
    bin_name = bin or name
    exec_dir = exec_dir or default_exec_dir()
    output_dir.mkdir(parents=True, exist_ok=True)
    extra = f" {extra_args.strip()}" if extra_args.strip() else ""

    lines = []
    for field_name, field in settings.model_fields.items():
        if not prompt_all and not _is_needed(field):
            # Optional fields keep their built-in defaults; keep the env file
            # minimal unless ``--prompt-all`` is requested.
            continue
        env_name = f"{env_prefix}{field_name.upper()}"
        lines.append(f"{env_name}={_prompt_field(env_name, field)}")

    env_file = output_dir / f"{name}.env"
    env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    # Owner-only from the start so that deploying via ``mv`` preserves it.
    env_file.chmod(0o600)

    unit_file = output_dir / f"{name}.service"
    unit_file.write_text(
        UNIT_TEMPLATE.format(
            name=name,
            service_type=service_type,
            exec_dir=exec_dir,
            bin=bin_name,
            extra_args=extra,
        ),
        encoding="utf-8",
    )

    click.echo(f"Generated {unit_file}")
    click.echo(f"Generated {env_file}")
    click.echo("Install with (generated files are moved, not kept):")
    click.echo("  sudo mkdir -p /etc/eb")
    click.echo(f"  sudo mv {env_file} /etc/eb/{name}.env")
    click.echo(f"  sudo chown root:root /etc/eb/{name}.env")
    click.echo(f"  sudo mv {unit_file} /etc/systemd/system/")
    click.echo("  sudo systemctl daemon-reload")
    click.echo(f"  sudo systemctl enable --now {name}")


if __name__ == "__main__":
    service()
