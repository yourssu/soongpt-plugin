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
    # SPR-67: 강의시간표(course_schedule) 계열 도구가 동시에 송출할 수 있는
    # USAINT 포털 요청 수 상한. WebDynpro가 동일 SSO 세션의 동시 요청을 순차
    # 처리하므로, 한 번에 많이 쏘면 마지막 것이 ~30초 대기 후 타임아웃/세션
    # 끊김. 보수적 기본값 4 (3~5 권장). 실측으로 튜닝 가능.
    course_schedule_concurrency: int = _env_int(
        "SOONGPT_COURSE_SCHEDULE_CONCURRENCY", 4
    )
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
