"""Configuration for soongpt-mcp.

Reads from environment variables with sensible defaults. No secrets are
stored here - session JSON lives in the OS keyring (see auth.py).
"""
from __future__ import annotations

import os
from dataclasses import dataclass


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class Config:
    rusaint_timeout: int = _env_int("SOONGPT_RUSAINT_TIMEOUT", 30)
    keyring_service_name: str = os.environ.get(
        "SOONGPT_KEYRING_SERVICE", "soongpt-mcp"
    )
    keyring_session_key: str = os.environ.get(
        "SOONGPT_KEYRING_SESSION_KEY", "usaint_session_json"
    )

    @property
    def env_session_json(self) -> str | None:
        value = os.environ.get("SOONGPT_SESSION_JSON")
        return value if value else None


def get_config() -> Config:
    return Config()
