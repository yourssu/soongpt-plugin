"""학기별 스냅샷 로컬 캐시 — 프로필 + 수강이력 단일 SoT (SPR-46).

"시간표 짜자" 진입 시 get_usaint_snapshot()이 USAINT fetch 결과를 이 파일에
저장한다. 프로필과 수강이력을 한 파일에 두어 SoT(진실의 원천)를 단일화 —
"프로필은 있는데 수강이력 캐시는 없는" 동기화 불일치를 원천 차단한다.

학기별 스냅샷(snapshot_{year}_{semester}.json). 수강이력은 학기 중 거의
불변하므로 30일 TTL (graduation과 동일). 프로필은 TTL 없이 항상 유효하며,
fetched_at이 수강이력 신선도 판단 기준이다 (None이면 수강이력 미확보 —
get_usaint_snapshot()은 이 경우 캐시 miss로 보고 fetch).

이전 저장 방식(profile.py의 profile_{year}_{semester}.json / 레거시
profile.json)은 스냅샷 파일이 없을 때 읽기 폴백으로만 사용하며, 다음 저장
시점에 스냅샷 파일로 이전된다.

저장 경로:
- ${CLAUDE_PLUGIN_DATA}/snapshot_{year}_{semester}.json (플러그인 구성 시)
- ~/.local/share/soongpt-mcp/snapshot_{year}_{semester}.json (폴백)
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from .profile import UserProfile
from .schemas.usaint_schemas import BasicInfo, Flags, TakenCourse
from .semester import current_academic_period

logger = logging.getLogger(__name__)


CACHE_TTL_DAYS = 30

LEGACY_PROFILE_FILENAME = "profile.json"


def _cache_root() -> Path:
    """캐시 저장 디렉토리 루트. CLAUDE_PLUGIN_DATA 우선, 없으면 XDG 데이터 경로."""
    base = os.environ.get("CLAUDE_PLUGIN_DATA")
    if base:
        return Path(base)
    xdg = os.environ.get("XDG_DATA_HOME")
    return (Path(xdg) if xdg else Path.home() / ".local" / "share") / "soongpt-mcp"


def resolve_snapshot_path(
    year: int | None = None, semester: str | None = None
) -> Path:
    """스냅샷 캐시 파일 경로. year/semester 생략 시 현재 학기."""
    if year is None or semester is None:
        year, semester = current_academic_period()
    return _cache_root() / f"snapshot_{year}_{semester}.json"


def is_snapshot_cache_fresh(
    fetched_at: datetime, now: datetime | None = None
) -> bool:
    """fetched_at이 now 기준 30일 이내인지 (수강이력 신선도)."""
    now = now or datetime.now(timezone.utc)
    return (now - fetched_at) < timedelta(days=CACHE_TTL_DAYS)


class SnapshotCache(BaseModel):
    """학기 단위 스냅샷 (프로필 + 수강이력 단일 SoT).

    profile은 항상 존재 — get_user_profile/set_user_profile/refresh_user_profile
    이 이 파일을 읽고 쓴다. basicInfo/takenCourses 등 수강이력은
    get_usaint_snapshot() fetch 시 채워진다. fetched_at은 수강이력 마지막 fetch
    시각 — None이면 아직 USAINT에서 수강이력을 한 번도 가져오지 않은 상태.
    """

    model_config = ConfigDict(extra="forbid")

    year: int = Field(..., description="학기 기준 연도")
    semester: str = Field(..., description="학기 ('1' | '2')")
    profile: UserProfile = Field(..., description="사용자 학적 프로필")
    basicInfo: BasicInfo | None = Field(
        None, description="USAINT 학적 기본 정보 (수강이력 fetch 시 채워짐)"
    )
    takenCourses: list[TakenCourse] = Field(
        default_factory=list, description="학기별 수강 과목 코드 목록"
    )
    lowGradeSubjectCodes: list[str] = Field(
        default_factory=list, description="C 이하 성적 과목 코드 리스트"
    )
    subjectNames: dict[str, str] = Field(
        default_factory=dict, description="과목 코드 → 강의명 매핑"
    )
    flags: Flags = Field(default_factory=Flags, description="복수전공/부전공/교직 정보")
    warnings: list[str] = Field(default_factory=list, description="빈 데이터 경고 코드")
    fetched_at: datetime | None = Field(
        None, description="수강이력 마지막 fetch 시각 (None = 미확보)"
    )


def load_snapshot_cache(
    year: int, semester: str, path: Path | None = None
) -> tuple[SnapshotCache | None, datetime | None]:
    """스냅샷 캐시 로드. (cache, fetched_at) 튜플 반환.

    파일 없으면 (None, None). JSON 손상/스키마 위반/fetched_at 타임존 누락 시에도
    (None, None)과 경고 로그. fetched_at은 수강이력 신선도 판단에 쓰임.
    """
    target = path or resolve_snapshot_path(year, semester)
    if not target.exists():
        return None, None
    try:
        raw = target.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("스냅샷 캐시 파싱 실패 (%s): %s", target, exc)
        return None, None

    try:
        cache = SnapshotCache.model_validate(data)
    except ValueError as exc:
        logger.warning("스냅샷 캐시 스키마 위반 (%s): %s", target, exc)
        return None, None

    if cache.fetched_at is not None and cache.fetched_at.tzinfo is None:
        logger.warning(
            "스냅샷 캐시 fetched_at에 타임존 정보 없음 (%s): %s",
            target,
            cache.fetched_at,
        )
        return None, None

    return cache, cache.fetched_at


def save_snapshot_cache(
    cache: SnapshotCache, path: Path | None = None
) -> Path:
    """스냅샷 캐시 저장. atomic write (tmp → os.replace). 부모 디렉토리 자동 생성."""
    target = path or resolve_snapshot_path(cache.year, cache.semester)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = cache.model_dump(mode="json")
    serialized = json.dumps(payload, ensure_ascii=False, indent=2)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(serialized, encoding="utf-8")
    os.replace(tmp, target)
    return target


def load_profile(
    year: int | None = None,
    semester: str | None = None,
    path: Path | None = None,
) -> UserProfile | None:
    """스냅샷 파일에서 프로필 부분 로드. 파일 없으면 None.

    path 생략 시 현재 학기 스냅샷 파일을 읽고, 없으면 이전 저장 방식
    (profile_{year}_{semester}.json, 레거시 profile.json)에서 폴백한다.
    """
    if year is None or semester is None:
        year, semester = current_academic_period()

    cache, _ = load_snapshot_cache(year, semester, path=path)
    if cache is not None:
        return cache.profile

    if path is not None:
        return None

    legacy = _legacy_profile_path(year, semester)
    if legacy.exists():
        return _load_legacy_profile(legacy)
    legacy_root = _cache_root() / LEGACY_PROFILE_FILENAME
    if legacy_root.exists():
        return _load_legacy_profile(legacy_root)
    return None


def save_profile(
    profile: UserProfile,
    year: int | None = None,
    semester: str | None = None,
    path: Path | None = None,
) -> Path:
    """스냅샷 파일의 프로필 부분만 갱신 후 저장. 수강이력은 그대로 유지.

    수강이력 신선도(fetched_at)는 건드리지 않는다 — 프로필 수정
    (set_user_profile/refresh_user_profile)이 수강이력을 "방금 fetch됨"으로
    만들지 않도록. path 생략 시 현재 학기 스냅샷 파일에 저장하고, 이전 저장
    방식 파일(profile_{year}_{semester}.json, 레거시 profile.json)은
    best-effort로 제거한다.
    """
    if year is None or semester is None:
        year, semester = current_academic_period()

    cache, _ = load_snapshot_cache(year, semester, path=path)
    if cache is None:
        cache = SnapshotCache(year=year, semester=semester, profile=profile)
    else:
        cache = cache.model_copy(update={"profile": profile})

    target = save_snapshot_cache(cache, path=path)
    if path is None:
        cleanup_legacy_profiles(year, semester, target)
    return target


def _legacy_profile_path(year: int, semester: str) -> Path:
    """이전(SPR-33~45) 학기별 프로필 파일 경로. 읽기 폴백용."""
    return _cache_root() / f"profile_{year}_{semester}.json"


def _load_legacy_profile(path: Path) -> UserProfile | None:
    """이전 프로필 파일(profile_{year}_{semester}.json / profile.json) 로드."""
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
        return UserProfile.model_validate(data)
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning("이전 프로필 파일 파싱 실패 (%s): %s", path, exc)
        return None
    except OSError as exc:
        logger.warning("이전 프로필 파일 읽기 실패 (%s): %s", path, exc)
        return None


def cleanup_legacy_profiles(year: int, semester: str, target: Path) -> None:
    """스냅샷 저장 후 이전 프로필 파일 제거 (마이그레이션 완료 표시).

    save_profile(프로필 수정)과 get_usaint_snapshot(fetch) 양쪽에서 호출한다.
    """
    for legacy in (
        _legacy_profile_path(year, semester),
        _cache_root() / LEGACY_PROFILE_FILENAME,
    ):
        if legacy.exists() and legacy != target:
            try:
                legacy.unlink()
            except OSError as exc:
                logger.warning("이전 프로필 파일 제거 실패 (%s): %s", legacy, exc)
