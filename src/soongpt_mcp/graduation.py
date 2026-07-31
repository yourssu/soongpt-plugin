"""졸업사정표 로컬 캐시.

USAINT fetch가 5-6초 소요되므로 1개월 캐싱. 단일 파일로 현재 시점 스냅샷 유지
(학기 키 없음 — 졸업사정표는 수강/성적이 계속 누적되므로 학기별 분리 의미 없음).

캐시 무효화 조건:
1. 캐시 파일 없음
2. cached_at으로부터 30일 경과
3. 사용자 명시 요청 (force_refresh=True)

저장 경로:
- ${CLAUDE_PLUGIN_DATA}/graduation.json (플러그인 구성 시)
- ~/.local/share/soongpt-mcp/graduation.json (폴백)
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


CACHE_TTL_DAYS = 30

CACHE_FILENAME = "graduation.json"


def _cache_root() -> Path:
    base = os.environ.get("CLAUDE_PLUGIN_DATA")
    if base:
        return Path(base)
    xdg = os.environ.get("XDG_DATA_HOME")
    return (Path(xdg) if xdg else Path.home() / ".local" / "share") / "soongpt-mcp"


def resolve_graduation_cache_path() -> Path:
    """졸업사정표 캐시 파일 경로."""
    return _cache_root() / CACHE_FILENAME


def is_cache_fresh(cached_at: datetime, now: datetime | None = None) -> bool:
    """cached_at이 now 기준 30일 이내인지."""
    now = now or datetime.now(timezone.utc)
    return (now - cached_at) < timedelta(days=CACHE_TTL_DAYS)


def load_graduation_cache(
    path: Path | None = None,
) -> tuple[dict[str, Any] | None, datetime | None]:
    """캐시 로드. (data, cached_at) 튜플 반환.

    파일 없으면 (None, None). 파일 손상/스키마 위반 시에도 (None, None)과 경고 로그.
    """
    target = path or resolve_graduation_cache_path()
    if not target.exists():
        return None, None
    try:
        raw = target.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("졸업사정표 캐시 파싱 실패 (%s): %s", target, exc)
        return None, None

    cached_at_str = data.get("cached_at") if isinstance(data, dict) else None
    if not cached_at_str:
        return None, None
    try:
        cached_at = datetime.fromisoformat(cached_at_str)
    except ValueError as exc:
        logger.warning("졸업사정표 캐시 cached_at 파싱 실패: %s", exc)
        return None, None

    payload = data.get("payload") if isinstance(data, dict) else None
    if payload is None:
        return None, None
    return payload, cached_at


def save_graduation_cache(
    payload: dict[str, Any], path: Path | None = None
) -> Path:
    """캐시 저장. atomic write + cached_at 타임스탬프."""
    target = path or resolve_graduation_cache_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    envelope = {
        "cached_at": datetime.now(timezone.utc).isoformat(),
        "payload": payload,
    }
    serialized = json.dumps(envelope, ensure_ascii=False, indent=2)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(serialized, encoding="utf-8")
    os.replace(tmp, target)
    return target
