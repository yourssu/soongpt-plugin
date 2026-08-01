"""시간표 후보(composer 산출물) 영속화 (SPR-52).

컴포저(soongpt-timetable-composer 스킬)가 조합한 후보를 학기별 스냅샷으로 저장.
인터뷰(interview.py) / 강의 캐시(lectures_cache.py)와 동일한 영속화 패턴을 따른다.

저장 경로:
- ${CLAUDE_PLUGIN_DATA}/timetable_{year}_{semester}.json (플러그인 구성 시)
- ~/.local/share/soongpt-mcp/timetable_{year}_{semester}.json (폴백)

TTL 없음 — clear_timetable_candidates 도구로만 무효화. 캐시 자체가 사용자의
산출물이므로 손상/스키마 위반 시에도 파일을 삭제하지 않고 보존한 채 경고만 남긴다.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)


def _cache_root() -> Path:
    base = os.environ.get("CLAUDE_PLUGIN_DATA")
    if base:
        return Path(base)
    xdg = os.environ.get("XDG_DATA_HOME")
    return (Path(xdg) if xdg else Path.home() / ".local" / "share") / "soongpt-mcp"


def resolve_timetable_path(year: int, semester: str) -> Path:
    """후보 캐시 파일 경로. 학기별로 분리."""
    return _cache_root() / f"timetable_{year}_{semester}.json"


class TimetableCandidate(BaseModel):
    """후보 1건. 컴포저가 조합해 사용자에게 제시/확정 받는 단위."""

    model_config = ConfigDict(extra="forbid")

    name: str  # 후보 이름 (예: "안 A — 15학점")
    lecture_codes: list[str]  # 선택 강의 code 목록 (분반 포함 10자리)
    total_credits: float  # 학점 합계
    has_blocking_conflict: bool  # 마지막 충돌 검사 결과 — 재개 근거
    conflicts_summary: str  # 자유 텍스트 (충돌/불확정/empty warnings 요약 — check 도구 warnings 포함 필수)
    notes: str = ""
    confirmed: bool = False  # 사용자가 확정한 후보
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )  # 서버/모델 기본값 (LLM 타임스탬프 부담 제거)


class TimetableCache(BaseModel):
    """학기 단위 후보 캐시 루트 스키마."""

    model_config = ConfigDict(extra="forbid")

    year: int
    semester: str
    candidates: list[TimetableCandidate] = Field(default_factory=list)
    generation_params: dict[str, Any] = Field(
        default_factory=dict
    )  # {"interview_updated_at": iso, "lectures_cached_at": iso} — 재개 mismatch 판정용
    cached_at: datetime


def load_timetable_cache(
    year: int, semester: str, path: Path | None = None
) -> TimetableCache | None:
    """후보 캐시 로드. 파일 없으면 None.

    손상/스키마 위반 시에도 None + 경고만 남기고 파일은 보존한다 — 캐시가 사용자
    산출물이므로 사라진 것처럼 보이지 않게 한다 (clear 도구로만 명시 삭제).
    """
    target = path or resolve_timetable_path(year, semester)
    if not target.exists():
        return None
    try:
        raw = target.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("시간표 후보 캐시 파싱 실패 (%s): %s — 파일은 보존됨", target, exc)
        return None

    try:
        cache = TimetableCache.model_validate(data)
    except ValueError as exc:
        logger.warning("시간표 후보 캐시 스키마 위반 (%s): %s — 파일은 보존됨", target, exc)
        return None

    return cache


def save_timetable_cache(cache: TimetableCache, path: Path | None = None) -> Path:
    """후보 캐시 저장. atomic write (tmp → os.replace). 부모 디렉토리 자동 생성."""
    target = path or resolve_timetable_path(cache.year, cache.semester)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = cache.model_dump(mode="json")
    serialized = json.dumps(payload, ensure_ascii=False, indent=2)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(serialized, encoding="utf-8")
    os.replace(tmp, target)
    return target


def add_candidate(
    cache: TimetableCache | None,
    candidate: TimetableCandidate,
    year: int | None = None,
    semester: str | None = None,
) -> tuple[TimetableCache, bool]:
    """후보 1건 추가/교체. 반환: (업데이트된 캐시, 교체 여부).

    같은 name의 기존 후보가 있으면 교체(replaced=True)하고, 없으면 append —
    컴포저가 같은 후보를 수정 반복할 때 폐기 후보가 축적되지 않게 한다.
    cache가 None이면 year/semester로 새 캐시를 만들어 candidate를 담는다.
    """
    now = datetime.now(timezone.utc)
    if cache is None:
        if year is None or semester is None:
            raise ValueError("cache가 None이면 year/semester가 필요합니다.")
        cache = TimetableCache(
            year=year,
            semester=semester,
            candidates=[],
            generation_params={},
            cached_at=now,
        )

    replaced = False
    new_candidates: list[TimetableCandidate] = []
    for existing in cache.candidates:
        if existing.name == candidate.name:
            new_candidates.append(candidate)
            replaced = True
        else:
            new_candidates.append(existing)
    if not replaced:
        new_candidates.append(candidate)

    updated = cache.model_copy(
        update={"candidates": new_candidates, "cached_at": now}
    )
    return updated, replaced


def clear_timetable_cache(year: int, semester: str, path: Path | None = None) -> bool:
    """후보 캐시 파일 삭제. 파일이 없으면 False, 삭제했으면 True."""
    target = path or resolve_timetable_path(year, semester)
    if not target.exists():
        return False
    target.unlink()
    return True
