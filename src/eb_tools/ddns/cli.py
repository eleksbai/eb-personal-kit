"""Command-line interface for the DDNS client.

Installed as the ``eb-ddns`` console script after ``uv build`` (see
``[project.scripts]``). Heavy optional dependencies are imported lazily
inside :func:`main` so that importing this module does not require the
``ddns`` extra.
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
    context_settings={"help_option_names": ["-h", "--help"]},
)
@click.version_option(version=get_version(), prog_name="eb-ddns")
@click.option("--secret-id", default=None, help="Tencent Cloud secret id.")
@click.option("--secret-key", default=None, help="Tencent Cloud secret key.")
@click.option(
    "--ip-server", default=None, help="HTTP endpoint returning the current public IP as JSON."
)
@click.option("--domain", default=None, help="Domain whose root A record should be kept in sync.")
@click.option("--debug", is_flag=True, default=False, help="Enable debug logging.")
def main(
    secret_id: str | None,
    secret_key: str | None,
    ip_server: str | None,
    domain: str | None,
    debug: bool,
) -> None:
    """Keep a Tencent Cloud DNSPod A record in sync with the current public IP.

    Values are resolved in order: CLI options, ``EB_DDNS_*`` environment
    variables, a ``.env`` file in the working directory, then defaults.
    """
    from pydantic import ValidationError

    from eb_tools.ddns.client import DDNS
    from eb_tools.ddns.config import get_settings
    from eb_tools.utils import setup_logging

    # Init overrides have the highest priority in pydantic-settings; required
    # fields missing everywhere are reported as a ValidationError.
    overrides = {
        name: value
        for name, value in (
            ("tencentcloud_secret_id", secret_id),
            ("tencentcloud_secret_key", secret_key),
            ("ip_server", ip_server),
            ("domain", domain),
        )
        if value is not None
    }
    if debug:
        overrides["log_level"] = "DEBUG"

    try:
        config = get_settings(**overrides)
    except ValidationError as err:
        raise click.UsageError(f"Invalid configuration: {err}") from err

    logger = setup_logging("ddns", log_level=config.log_level, log_format=config.log_format)
    logger.info("start...")
    DDNS(config).run()


if __name__ == "__main__":
    main()
