"""Command-line interface for the monitor sampling client.

Exposed as the ``eb monitor`` subcommand of the main CLI (and runnable as
``python -m eb_tools.monitor``). Heavy optional dependencies are imported
lazily inside :func:`main` so that importing this module does not require
the ``monitor`` dependencies.
"""

from __future__ import annotations

from importlib import metadata

import click


def get_version() -> str:
    """Return the installed distribution version."""
    try:
        return metadata.version("eb-tools")
    except metadata.PackageNotFoundError:  # pragma: no cover - running from source
        return "0.0.0+unknown"


@click.command(
    name="monitor",
    context_settings={"help_option_names": ["-h", "--help"]},
)
@click.version_option(version=get_version(), prog_name="eb-monitor")
@click.option(
    "--public-key-path",
    default=None,
    help="Path to the server RSA public key PEM file.",
)
@click.option(
    "--server-url",
    default=None,
    help="Base URL of the monitor server accepting sample uploads.",
)
@click.option("--interval", type=float, default=None, help="Sampling interval in seconds.")
@click.option(
    "--upload",
    is_flag=True,
    default=False,
    help="Enable uploading samples to the server (requires public-key-path and server-url).",
)
@click.option("--quiet", is_flag=True, default=False, help="Disable the per-sample status print.")
@click.option("--debug", is_flag=True, default=False, help="Enable debug logging.")
def main(
    public_key_path: str | None,
    server_url: str | None,
    interval: float | None,
    upload: bool,
    quiet: bool,
    debug: bool,
) -> None:
    """Sample system metrics and upload them encrypted to the monitor server.

    Values are resolved in order: CLI options, ``EB_MONITOR_*`` environment
    variables, a ``.env`` file in the working directory, then defaults.
    Without ``--upload`` the client only samples and prints status lines.
    """
    from pydantic import ValidationError

    from eb_tools.monitor.client import Monitor
    from eb_tools.monitor.config import get_settings, setup_logging

    # Init overrides have the highest priority in pydantic-settings; required
    # fields missing everywhere are reported as a ValidationError.
    overrides = {
        name: value
        for name, value in (
            ("public_key_path", public_key_path),
            ("server_url", server_url),
            ("interval", interval),
            ("upload", upload),
            ("quiet", quiet),
        )
        if value
    }
    if debug:
        overrides["enable_debug"] = True

    try:
        config = get_settings(**overrides)
    except ValidationError as err:
        raise click.UsageError(f"Invalid configuration: {err}") from err

    setup_logging(config)
    Monitor(config).run()


if __name__ == "__main__":
    main()
