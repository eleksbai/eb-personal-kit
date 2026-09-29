"""Shared helpers for eb-personal-kit submodules."""

from __future__ import annotations

import logging

DEFAULT_LOG_FORMAT = "%(asctime)s %(levelname)s: %(message)s"


def setup_logging(
    logger_name: str,
    log_level: str = "INFO",
    log_format: str = DEFAULT_LOG_FORMAT,
) -> logging.Logger:
    """Configure a named logger with a terminal stream handler.

    ``log_level`` accepts standard level names (DEBUG, INFO, WARNING, ERROR,
    CRITICAL), case-insensitively; unknown names raise ``ValueError``.
    """
    levels = logging.getLevelNamesMapping()
    level = levels.get(log_level.upper())
    if level is None:
        valid = ", ".join(sorted(levels))
        raise ValueError(f"invalid log level {log_level!r}; expected one of: {valid}")
    logger = logging.getLogger(logger_name)
    logger.setLevel(level)
    terminal = logging.StreamHandler()
    terminal.setFormatter(logging.Formatter(log_format))
    logger.handlers.clear()
    logger.addHandler(terminal)
    return logger


def human_bytes(byte_count: int) -> str:
    """Format a byte count with an auto-scaled unit (B, KB, MB, GB)."""
    if byte_count < 1024:
        return f"{byte_count}B"
    elif byte_count < 1024 * 1024:
        return f"{byte_count / 1024:.1f}K"
    elif byte_count < 1024 * 1024 * 1024:
        return f"{byte_count / 1024 / 1024:.1f}M"
    else:
        return f"{byte_count / 1024 / 1024 / 1024:.1f}G"
