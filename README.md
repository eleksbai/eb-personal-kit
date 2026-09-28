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
EB_DDNS_ENABLE_DEBUG=false
```

也可用 `python -m eb_tools.ddns` 运行。

### systemd 服务部署（eb service generate）

主 CLI 可为任意子模块生成 systemd 服务文件，环境变量依据该模块 `config.Settings` 的字段派生：必填项交互输入（密钥逐字符显示 `*` 掩码，支持退格删除），可选项取默认值：

```bash
# 位置参数为模块名，--name 决定服务文件名与可执行文件名（默认 eb-<module>）
eb service generate --name eb-ddns ddns

# 通过 --type 修改 systemd Type（默认 idle）
eb service generate --name eb-ddns --type simple ddns
```

生成 `<name>.service` 与 `<name>.env` 两个文件：配置写入 `<name>.env`（每行一项 `KEY=VALUE`），服务文件通过 `EnvironmentFile=` 引用它，`ExecStart` 指向当前用户的默认可执行目录（venv 内为 venv 的脚本目录，否则为 `~/.local/bin`，即 `pip install --user` 的路径；可用 `--exec-dir` 覆盖）：

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

安装（`<name>.env` 生成时即为 `600` 权限，`mv` 移动后保持不变；因生成者是普通用户而 `mv` 不改所有者，补一步 `chown` 交给 root；移动后源目录不残留密钥文件）：

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

### 开发调试（免安装直接运行）

开发时无需 `uv build` / `pip install`，通过 `python -m` 直接以模块方式运行各 CLI（等价于安装后的 `eb` / `eb-ddns` console script）：

```bash
# 主 CLI（含 service 子命令组），等价于安装后的 eb
uv run python -m eb_tools.cli --help
uv run python -m eb_tools.cli service --help
uv run python -m eb_tools.cli service generate --name eb-ddns ddns

# ddns CLI，等价于安装后的 eb-ddns
uv run python -m eb_tools.ddns.cli --help
uv run python -m eb_tools.ddns.cli --debug
```

注意：`python -m` 需指定到模块文件（如 `eb_tools.ddns.cli`）或带 `__main__.py` 的包（如 `eb_tools.ddns`），且各 CLI 模块内已有 `if __name__ == "__main__"` 入口守卫，直接执行即可生效。
