# eb-tools

A collection of handy tools.

## Install

```bash
pip install eb-tools
```

## Development

This project uses [uv](https://docs.astral.sh/uv/) for dependency management,
[ruff](https://docs.astral.sh/ruff/) for linting and formatting, and
[pytest](https://docs.pytest.org/) for testing.

```bash
# Sync dev environment (creates .venv automatically)
uv sync

# Run tests
uv run pytest

# Lint and format
uv run ruff check
uv run ruff format .
```
