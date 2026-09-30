"""Command-line interface for the sunrise/sunset theme switcher.

Exposed as the ``eb theme`` subcommand of the main CLI (and runnable as
``python -m eb_personal_kit.theme``). ``astral`` is imported lazily inside
:func:`main` so that importing this module does not require it.
"""

from __future__ import annotations

import click


@click.command(
    name="theme",
    context_settings={"help_option_names": ["-h", "--help"]},
)
@click.option(
    "--latitude",
    type=float,
    default=None,
    help="Location latitude in degrees [-90, 90] [default: Xiamen 24.4798].",
)
@click.option(
    "--longitude",
    type=float,
    default=None,
    help="Location longitude in degrees [-180, 180] [default: Xiamen 118.0894].",
)
@click.option(
    "--check-interval",
    type=float,
    default=None,
    help="Seconds between daylight checks in --loop mode [default: 60].",
)
@click.option(
    "--target",
    type=click.Choice(("apps", "system", "both")),
    default=None,
    help="Which Windows colour-scheme registry values to switch [default: both].",
)
@click.option(
    "--auto",
    is_flag=True,
    default=False,
    help="Switch once according to the current sun position, then exit.",
)
@click.option("--loop", is_flag=True, default=False, help="Repeat the action on the check cadence.")
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Log the decision without switching the system theme.",
)
@click.option(
    "--install",
    is_flag=True,
    default=False,
    help="Register this command to run hidden at logon (no console window), then exit.",
)
@click.option(
    "--uninstall",
    is_flag=True,
    default=False,
    help="Remove the logon autostart entry, then exit.",
)
@click.option(
    "--log-level",
    type=click.Choice(("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"), case_sensitive=False),
    default=None,
    help="Logging verbosity [default: INFO].",
)
def main(
    latitude: float | None,
    longitude: float | None,
    check_interval: float | None,
    target: str | None,
    auto: bool,
    loop: bool,
    dry_run: bool,
    install: bool,
    uninstall: bool,
    log_level: str | None,
) -> None:
    """Switch the system colour scheme.

    By default the current theme is flipped once (dark -> light or
    light -> dark). With ``--auto`` the action switches per the current sun
    position instead; ``--loop`` repeats the action on the check cadence.
    Values are resolved in order: CLI options, ``EB_THEME_*`` environment
    variables, a ``.env`` file in the working directory, then defaults
    (Xiamen).
    """
    from pydantic import ValidationError

    from eb_personal_kit.theme.client import ThemeSwitcher
    from eb_personal_kit.theme.config import get_settings
    from eb_personal_kit.utils import setup_logging

    if install and uninstall:
        raise click.UsageError("--install and --uninstall are mutually exclusive")

    # Init overrides have the highest priority in pydantic-settings; numeric
    # options use ``is not None`` so the valid value 0 is not dropped.
    overrides = {
        name: value
        for name, value in (
            ("latitude", latitude),
            ("longitude", longitude),
            ("check_interval", check_interval),
        )
        if value is not None
    }
    if target:
        overrides["target"] = target
    overrides["mode"] = "auto" if auto else "toggle"
    if loop:
        overrides["loop"] = True
    if dry_run:
        overrides["dry_run"] = True
    if log_level:
        overrides["log_level"] = log_level.upper()

    try:
        config = get_settings(**overrides)
    except ValidationError as err:
        raise click.UsageError(f"Invalid configuration: {err}") from err

    if install:
        import subprocess

        from eb_personal_kit.theme.client import autostart_command, install_autostart
        from eb_personal_kit.utils import LOG_DIR

        command = autostart_command(config)
        location = install_autostart(command)
        click.echo(f"Installed autostart entry: {location}")
        click.echo(f"Command: {subprocess.list2cmdline(command)}")
        if command[0].lower().endswith("pythonw.exe"):
            click.echo(f"Log file (no console): {LOG_DIR / 'theme.log'}")
        return

    if uninstall:
        from eb_personal_kit.theme.client import uninstall_autostart

        location = uninstall_autostart()
        if location is None:
            click.echo("No autostart entry found.")
        else:
            click.echo(f"Removed autostart entry: {location}")
        return

    setup_logging("theme", log_level=config.log_level, log_format=config.log_format)
    ThemeSwitcher(config).run()


if __name__ == "__main__":
    main()
