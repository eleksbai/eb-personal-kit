"""Theme switcher settings, following the FastAPI settings pattern (pydantic-settings).

Values resolve in order: environment variables prefixed with ``EB_THEME_``,
a ``.env`` file in the working directory, then field defaults.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from eb_personal_kit.utils import DEFAULT_LOG_FORMAT

TARGETS = ("apps", "system", "both")
MODES = ("toggle", "auto", "loop")


class Settings(BaseSettings):
    """Settings for the sunrise/sunset driven system theme switcher.

    ``latitude`` and ``longitude`` default to Xiamen; pydantic raises a
    ``ValidationError`` when a given value is out of range. ``target`` selects
    which Windows colour-scheme registry values are switched (ignored on other
    platforms, where the whole colour scheme is toggled). ``mode`` selects the
    action (``toggle`` flips the current theme, ``auto`` switches per the sun
    position) and ``loop`` repeats it on the check cadence.
    """

    model_config = SettingsConfigDict(
        env_prefix="EB_THEME_",
        env_file=".env",
        env_ignore_empty=True,
        extra="ignore",
    )

    # Optional: built-in defaults (Xiamen).
    latitude: float = 24.4798
    longitude: float = 118.0894

    check_interval: float = 60
    target: str = "both"
    mode: str = "toggle"
    loop: bool = False
    dry_run: bool = False
    log_level: str = "INFO"
    log_format: str = DEFAULT_LOG_FORMAT

    @model_validator(mode="after")
    def _validate(self) -> Settings:
        if not -90 <= self.latitude <= 90:
            raise ValueError("latitude must be within [-90, 90]")
        if not -180 <= self.longitude <= 180:
            raise ValueError("longitude must be within [-180, 180]")
        if self.target not in TARGETS:
            raise ValueError(f"target must be one of: {', '.join(TARGETS)}")
        if self.mode not in MODES:
            raise ValueError(f"mode must be one of: {', '.join(MODES)}")
        if self.check_interval <= 0:
            raise ValueError("check_interval must be positive")
        return self


@lru_cache
def get_settings(**overrides: Any) -> Settings:
    """Return the cached settings, with init overrides taking highest priority."""
    return Settings(**overrides)
