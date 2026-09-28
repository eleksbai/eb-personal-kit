"""Command-line interface for eb-tools."""

import sys
from importlib import metadata

import click


def get_version() -> str:
    """Return the installed distribution version."""
    try:
        return metadata.version("eb-tools")
    except metadata.PackageNotFoundError:  # pragma: no cover - running from source
        return "0.0.0+unknown"


@click.group(
    context_settings={"help_option_names": ["-h", "--help"]},
)
@click.version_option(version=get_version(), prog_name="eb")
def main() -> None:
    """eb-tools command line interface."""


if __name__ == "__main__":
    main()
    sys.exit(0)
