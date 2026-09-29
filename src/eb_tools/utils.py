"""Shared helpers for eb-tools submodules."""


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
