"""들을 수 있는 과목 로컬 캐시.

스킬이 find_lectures N회 + list_optional_elective_categories 결과를 취합해 저장.
학기별 스냅샷(`lectures_{year}_{semester}.json`)으로 관리. TTL 7일(학기 중
강의 시간/교실 변경 가능성 대비). 사용자 명시 새로고침 시 덮어쓰기.

저장 경로:
- ${CLAUDE_PLUGIN_DATA}/lectures_{year}_{semester}.json (플러그인 구성 시)
- ~/.claude/state/soongpt-planner/lectures_{year}_{semester}.json (폴백)

캐시 무효화 조건:
1. 캐시 파일 없음
2. cached_at으로부터 7일 경과
3. 사용자 명시 요청 (스킬이 save 없이 find_lectures 재실행 후 덮어쓰기)
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)


CACHE_TTL_DAYS = 7


def _cache_root() -> Path:
    base = os.environ.get("CLAUDE_PLUGIN_DATA")
    if base:
        return Path(base)
    return Path.home() / ".claude" / "state" / "soongpt-planner"


def resolve_lectures_cache_path(year: int, semester: str) -> Path:
    """강의 캐시 파일 경로. 학기별로 분리."""
    return _cache_root() / f"lectures_{year}_{semester}.json"


def is_lectures_cache_fresh(
    cached_at: datetime, now: datetime | None = None
) -> bool:
    """cached_at이 now 기준 7일 이내인지."""
    now = now or datetime.now(timezone.utc)
    return (now - cached_at) < timedelta(days=CACHE_TTL_DAYS)


class LectureGroupEntry(BaseModel):
    """단일 카테고리 조회 결과 스냅샷.

    params는 find_lectures에 전달된 category_type 외 파라미터 스냅샷
    (collage/department/major/category/lecture_name/keyword 등).
    """

    model_config = ConfigDict(extra="forbid")

    category_type: str
    params: dict[str, Any] = Field(default_factory=dict)
    lectures: list[dict[str, Any]] = Field(default_factory=list)
    count: int = 0
    error: str | None = None


class LecturesCache(BaseModel):
    """학기 단위 강의 캐시 루트 스키마."""

    model_config = ConfigDict(extra="forbid")

    year: int
    semester: str
    groups: dict[str, LectureGroupEntry] = Field(default_factory=dict)
    cached_at: datetime


def load_lectures_cache(
    year: int, semester: str, path: Path | None = None
) -> tuple[LecturesCache | None, datetime | None]:
    """캐시 로드. (cache, cached_at) 튜플 반환.

    파일 없으면 (None, None). 파일 손상/스키마 위반 시에도 (None, None)과 경고 로그.
    cached_at은 별도로 뽑아 반환하여 호출자가 TTL 판단에 쓸 수 있게 함.
    """
    target = path or resolve_lectures_cache_path(year, semester)
    if not target.exists():
        return None, None
    try:
        raw = target.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("강의 캐시 파싱 실패 (%s): %s", target, exc)
        return None, None

    try:
        cache = LecturesCache.model_validate(data)
    except ValueError as exc:
        logger.warning("강의 캐시 스키마 위반 (%s): %s", target, exc)
        return None, None

    return cache, cache.cached_at


def save_lectures_cache(
    cache: LecturesCache, path: Path | None = None
) -> Path:
    """캐시 저장. atomic write (tmp → os.replace). 부모 디렉토리 자동 생성."""
    target = path or resolve_lectures_cache_path(cache.year, cache.semester)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = cache.model_dump(mode="json")
    serialized = json.dumps(payload, ensure_ascii=False, indent=2)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(serialized, encoding="utf-8")
    os.replace(tmp, target)
    return target
