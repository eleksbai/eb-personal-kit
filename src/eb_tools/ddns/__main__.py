"""``python -m eb_tools.ddns`` entry point (thin shell delegating to the CLI)."""

from eb_tools.ddns.cli import main

if __name__ == "__main__":
    main()
