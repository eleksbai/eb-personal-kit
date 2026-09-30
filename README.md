# eb-personal-kit

A collection of handy tools.

## Install

```bash
pip install eb-personal-kit
```

Removing this GitHub trusted publisher will prevent automated package uploads from this source. You can re-add it later if needed

## Usage

### DDNS

DDNS client that keeps a domain's A record in sync with the current public IP via Tencent Cloud DNSPod.

```bash
# Quick Install Service
eb service generate ddns
sudo mkdir -p /etc/eb
sudo mv eb-ddns.env /etc/eb/eb-ddns.env
sudo mv eb-ddns.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now eb-ddns

# Reads config from .env (EB_DDNS_*) in the working directory
eb-ddns
```

### Monitor


```bash
# Quick Install Service
service generate --bin eb --extra-args "monitor --quiet --upload" monitor
sudo mkdir -p /etc/eb
sudo mv eb-monitor.env /etc/eb/eb-monitor.env
sudo mv eb-monitor.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now eb-monitor

# Reads config from .env (EB_DDNS_*) in the working directory
# Debug
eb monitor
```

### Theme

Switches the system colour scheme with the daylight, based on the offline-computed sunrise/sunset times (via `astral`) for the configured location (defaults to Xiamen).

```bash
# Flip the current theme once (dark -> light or light -> dark)
eb theme

# Switch per the current sun position instead of flipping
# (--dry-run logs the decision without touching the system)
eb theme --auto --dry-run --log-level DEBUG

# --loop repeats the action on the check cadence (pair with --auto to keep
# following the daylight; EB_THEME_CHECK_INTERVAL controls the cadence)
eb theme --auto --loop

# Install the current command as a logon autostart entry, then exit
# (Windows: HKCU ...\Run value; Linux: systemd user unit; macOS: LaunchAgent.
# On Windows the entry is launched via pythonw.exe, so it runs silently in
# the background with no console window or taskbar icon; without a console
# logs go to ~/.eb-personal-kit/logs/theme.log.
# NOTE: on Windows this must NOT be installed from inside a virtualenv —
# a venv's pythonw.exe cannot start truly silently in the background.
# Install eb-personal-kit into the system Python instead)
eb theme --auto --loop --install

# Remove the autostart entry again
eb theme --uninstall
```

Config resolves in order: CLI options, `EB_THEME_*` environment variables, a `.env` file, then defaults. `TARGET` selects which Windows colour-scheme values are switched (`apps`/`system`/`both`). On Linux it uses `gsettings color-scheme`; on macOS, AppleScript dark mode.

## Development

```bash
uv sync          # set up dev environment
uv run pytest    # run tests
uv build         # build

python -m eb_personal_kit.cli  service generate --bin eb --extra-args "monitor --quiet --upload" monitor
```

## Publish

Publishing is automated by GitHub Actions (`.github/workflows/publish.yml`): pushing a `v*` tag runs lint/tests, builds with `uv`, and uploads to PyPI via Trusted Publishing.

```bash
# 1. Commit and push changes to master first (the tag must point to master HEAD)
git push

# 2. Tag the release: v<major>.<minor>.<YYMMDD>, e.g. v0.1.260930
git tag v0.1.260930


# 3. Push the tag to trigger the publish workflow
git push origin v0.1.260930

# If the release was wrong: fix on master, then re-tag with a patch number
git tag v0.1.260930.1
git push origin v0.1.260930.1
```

Note: the tag must point to master HEAD and match `v<major>.<minor>.<YYMMDD>[.<n>]`; pushed version numbers cannot be reused or overwritten.
