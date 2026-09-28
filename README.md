# eb-tools

A collection of handy tools.

## Install

```bash
pip install eb-tools
```

## Tools

### eb-ddns (DDNS 客户端)

作为独立脚本分发，通过腾讯云 DNSPod API 保持域名 A 记录与当前公网 IP 同步。

```bash
# 安装（ddns 为可选依赖组）
pip install "eb-tools[ddns]"

# 配置：在工作目录创建 .env 并填入密钥，然后直接运行
eb-ddns

# 或直接通过选项/环境变量（EB_DDNS_*）传参
eb-ddns --secret-id <id> --secret-key <key> --domain example.com
```

.env 示例：

```dotenv
EB_DDNS_TENCENTCLOUD_SECRET_ID=your-tencent-cloud-secret-id
EB_DDNS_TENCENTCLOUD_SECRET_KEY=your-tencent-cloud-secret-key
EB_DDNS_IP_SERVER=https://eleksbai.cn/tools/ip
EB_DDNS_DOMAIN=your-domain
EB_DDNS_DEBUG=false
```

也可用 `python -m eb_tools.ddns` 运行。

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
