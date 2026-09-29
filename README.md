# eb-tools

A collection of handy tools.

## Install

```bash
pip install eb-tools
```

## Tools

### eb-ddns (DDNS client)

Distributed as a standalone script; keeps a domain's A record in sync with the current public IP through the Tencent Cloud DNSPod API.

```bash
# Configure: create a .env in the working directory, fill in the credentials, then run directly
eb-ddns

# Or pass options / environment variables (EB_DDNS_*) directly
eb-ddns --secret-id <id> --secret-key <key> --domain example.com
```

Example .env:

```dotenv
EB_DDNS_TENCENTCLOUD_SECRET_ID=your-tencent-cloud-secret-id
EB_DDNS_TENCENTCLOUD_SECRET_KEY=your-tencent-cloud-secret-key
EB_DDNS_IP_SERVER=https://eleksbai.cn/tools/ip
EB_DDNS_DOMAIN=your-domain
EB_DDNS_LOG_LEVEL=INFO
```

It can also be run with `python -m eb_tools.ddns`.

### systemd service deployment (eb service generate)

The main CLI can generate systemd service files for any submodule. Environment variables are derived from that module's `config.Settings` fields: required fields are prompted interactively (secrets are masked with `*` per keystroke, backspace supported), optional fields take their defaults:

```bash
# Positional argument is the module name; --name decides the service file and executable names (default eb-<module>)
eb service generate --name eb-ddns ddns

# Change the systemd Type via --type (default idle)
eb service generate --name eb-ddns --type simple ddns
```

Generates two files, `<name>.service` and `<name>.env`: configuration is written to `<name>.env` (one `KEY=VALUE` per line), the service file references it via `EnvironmentFile=`, and `ExecStart` points to the current user's default executable directory (the venv script directory when inside a venv, otherwise `~/.local/bin`, i.e. the `pip install --user` path; override with `--exec-dir`):

```ini
[Unit]
Description=eb-ddns
After=network.target

[Service]
Type=idle
EnvironmentFile=/etc/eb/eb-ddns.env
ExecStart=/home/user/.local/bin/eb-ddns
Restart=always
RestartSec=30
...
```

Installation (the generated `<name>.env` already has `600` permissions and `mv` preserves them; since the generator runs as a regular user and `mv` does not change the owner, an extra `chown` hands it to root; no secret files are left behind in the source directory after moving):

```bash
sudo mkdir -p /etc/eb
sudo mv eb-ddns.env /etc/eb/eb-ddns.env
sudo chown root:root /etc/eb/eb-ddns.env
sudo mv eb-ddns.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now eb-ddns
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

# Build
uv build
```

### Development debugging (run without installing)

During development there is no need for `uv build` / `pip install`; run each CLI directly as a module via `python -m` (equivalent to the installed `eb` / `eb-ddns` console scripts):

```bash
# Main CLI (with the service command group), equivalent to the installed eb
uv run python -m eb_tools.cli --help
uv run python -m eb_tools.cli service --help
uv run python -m eb_tools.cli service generate --name eb-ddns ddns

# ddns CLI, equivalent to the installed eb-ddns
uv run python -m eb_tools.ddns.cli --help
uv run python -m eb_tools.ddns.cli --debug
```

Note: `python -m` must point to a module file (e.g. `eb_tools.ddns.cli`) or a package with a `__main__.py` (e.g. `eb_tools.ddns`); each CLI module already contains an `if __name__ == "__main__"` entry guard, so running them directly works out of the box.
