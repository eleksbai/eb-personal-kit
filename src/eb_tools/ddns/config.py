"""DDNS client settings, following the FastAPI settings pattern (pydantic-settings).

Values resolve in order: environment variables prefixed with ``EB_DDNS_``,
a ``.env`` file in the working directory, then field defaults.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

LOG_FORMAT = "%(asctime)s %(levelname)s: %(message)s"

DEFAULT_IP_SERVER = "https://eleksbai.cn/tools/ip"


def setup_logging(enable_debug: bool) -> logging.Logger:
    """Configure the ``ddns`` logger with a stream handler."""
    level = logging.DEBUG if enable_debug else logging.INFO
    formatter = logging.Formatter(LOG_FORMAT)
    logger = logging.getLogger("ddns")
    logger.setLevel(level)
    terminal = logging.StreamHandler()
    terminal.setFormatter(formatter)
    logger.handlers.clear()
    logger.addHandler(terminal)
    return logger


class Settings(BaseSettings):
    """Settings for the Tencent Cloud DNSPod DDNS client.

    Fields without a default (``tencentcloud_secret_id``,
    ``tencentcloud_secret_key``, ``domain``) are required; pydantic raises a
    ``ValidationError`` listing them when missing.
    """

    model_config = SettingsConfigDict(
        env_prefix="EB_DDNS_",
        env_file=".env",
        env_ignore_empty=True,
        extra="ignore",
    )

    # Required: no defaults.
    tencentcloud_secret_id: SecretStr
    tencentcloud_secret_key: SecretStr
    domain: str

    # Optional: built-in defaults.
    ip_server: str = DEFAULT_IP_SERVER
    enable_debug: bool = False


@lru_cache
def get_settings(**overrides: Any) -> Settings:
    """Return the cached settings, with init overrides taking highest priority."""
    return Settings(**overrides)
