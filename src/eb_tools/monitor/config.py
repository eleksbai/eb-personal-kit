"""Monitor client settings, following the FastAPI settings pattern (pydantic-settings).

Values resolve in order: environment variables prefixed with ``EB_MONITOR_``,
a ``.env`` file in the working directory, then field defaults.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from eb_tools.utils import DEFAULT_LOG_FORMAT


class Settings(BaseSettings):
    """Settings for the monitor sampling client.

    ``public_key_path`` and ``server_url`` default to ``None``; they are only
    required when ``upload`` is enabled, which the model validator enforces.
    """

    model_config = SettingsConfigDict(
        env_prefix="EB_MONITOR_",
        env_file=".env",
        env_ignore_empty=True,
        extra="ignore",
    )

    # Default to ``None``; required only when ``upload`` is enabled.
    public_key_path: str | None = None
    server_url: str | None = None

    # Optional: built-in defaults.
    interval: float = 5
    log_level: str = "INFO"
    log_format: str = DEFAULT_LOG_FORMAT
    upload: bool = False
    quiet: bool = False

    @model_validator(mode="after")
    def _check_upload_config(self) -> Settings:
        if self.upload:
            missing = [
                name for name in ("public_key_path", "server_url") if not getattr(self, name)
            ]
            if missing:
                raise ValueError(f"{', '.join(missing)} required when upload is enabled")
        return self


@lru_cache
def get_settings(**overrides: Any) -> Settings:
    """Return the cached settings, with init overrides taking highest priority."""
    return Settings(**overrides)
