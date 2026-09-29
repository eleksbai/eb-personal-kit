"""Command-line interface for eb-personal-kit."""

import sys

import click

import eb_personal_kit
from eb_personal_kit.monitor.cli import main as monitor
from eb_personal_kit.service import service


@click.group(
    context_settings={"help_option_names": ["-h", "--help"]},
)
@click.version_option(version=eb_personal_kit.__version__, prog_name="eb")
def main() -> None:
    """eb-personal-kit command line interface."""


main.add_command(service)
main.add_command(monitor)


if __name__ == "__main__":
    main()
    sys.exit(0)
