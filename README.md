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
