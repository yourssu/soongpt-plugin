"""들을 수 있는 과목 로컬 캐시.

find_lectures가 서버 측에서 즉시 그룹별로 병합 저장한다 (SPR-75). 스킬은
"취합 → save"를 할 필요가 없고, 그룹 키 규칙도 서버가 생성한다
(`group_key_for` 참고). 학기별 스냅샷(`lectures_{year}_{semester}.json`)으로
관리. TTL 7일(학기 중 강의 시간/교실 변경 가능성 대비).

저장 경로:
- ${CLAUDE_PLUGIN_DATA}/lectures_{year}_{semester}.json (플러그인 구성 시)
- ~/.local/share/soongpt-mcp/lectures_{year}_{semester}.json (폴백)

캐시 무효화 조건:
1. 캐시 파일 없음
2. cached_at으로부터 7일 경과
3. 사용자 명시 요청 (find_lectures 재실행이 병합 저장이므로, 전체를 비우고
   다시 채우려면 clear 없이 stale 판정 후 재fetch — 각 그룹은 같은 조회를
   재fetch하면 대체된다)
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
    xdg = os.environ.get("XDG_DATA_HOME")
    return (Path(xdg) if xdg else Path.home() / ".local" / "share") / "soongpt-mcp"


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


def _save_lectures_cache(
    cache: LecturesCache, path: Path | None = None
) -> Path:
    """캐시 저장. atomic write (tmp → os.replace). 부모 디렉토리 자동 생성.

    **내부 전용**: SPR-75에서 같은 이름의 MCP 도구(``save_lectures_cache``)가
    제거됐다. 이 함수는 find_lectures 자동 저장(``save_lectures_group``)과
    테스트가 쓰는 내부 쓰기 API로, MCP 도구로 재노출하지 않는다.
    """
    target = path or resolve_lectures_cache_path(cache.year, cache.semester)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = cache.model_dump(mode="json")
    serialized = json.dumps(payload, ensure_ascii=False, indent=2)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(serialized, encoding="utf-8")
    os.replace(tmp, target)
    return target


def total_lectures_count(cache: LecturesCache) -> int:
    """전 그룹 강의 수 합 (load_lectures_cache 응답 `total_lectures`용).

    각 그룹의 ``count``(해당 조회의 강의 수)를 합산한다. error 그룹은
    ``count=0``이라 자연히 제외된다. ``count``(그룹 수)와 구분해서 쓰라
    (SPR-78).
    """
    return sum(entry.count for entry in cache.groups.values())


def group_key_for(
    category_type: str,
    *,
    collage: str | None = None,
    department: str | None = None,
    major: str | None = None,
    lecture_name: str | None = None,
    category: str | None = None,
    keyword: str | None = None,
) -> str:
    """find_lectures 결과를 캐시에 저장할 그룹 키 생성 (SPR-75).

    서버가 ``category_type + 주요 파라미터``로 키를 자동 생성한다. 스킬은
    더 이상 키를 정하지 않는다. 키 규칙은 단위 테스트로 고정한다.

    기존 스킬 키와의 관계:
    - ``optional_elective`` + category="전체" → ``optional_elective_all`` (동일)
    - ``optional_elective`` + 그 외 분야 → ``optional_elective_<분야명>`` (동일)
    - ``major`` → ``major_<collage>_<department>`` (기존 ``major_primary``/
      ``major_double``/``major_minor`` 대체 — 병합 시 콘텐츠 기반 대체로 자동 이전)
    - ``recognized_other_major`` → ``recognized_other_major_<collage>_<department>``
      (기존 ``recognized_other_major_primary`` 대체)
    - ``required_elective`` → ``required_elective_<과목명>`` (동일)
    - ``chapel``/``connected_major``/``united_major``/``education``/``cyber`` → 그대로
    """
    if category_type == "optional_elective":
        if category == "전체":
            return "optional_elective_all"
        return f"optional_elective_{category}"
    if category_type in ("major", "recognized_other_major"):
        # major(세부전공 필터)가 주어지면 키에 포함해 같은 collage+department의
        # 다른 major 조회가 서로 덮어쓰지 않게 한다 (critic MAJOR-2 반영).
        base = f"{category_type}_{collage}_{department}"
        return f"{base}_{major}" if major else base
    if category_type == "graduated":
        return f"graduated_{collage}_{department}"
    if category_type == "required_elective":
        return f"required_elective_{lecture_name}"
    if category_type in (
        "chapel",
        "connected_major",
        "united_major",
        "education",
        "cyber",
    ):
        return category_type
    if category_type in ("find_by_professor", "find_by_lecture"):
        return f"{category_type}_{keyword}"
    raise ValueError(f"그룹 키 미지원 category_type: {category_type}")


def _identifying_params(entry: LectureGroupEntry) -> tuple[str, ...]:
    """그룹의 '동일 조회' 판별 키.

    같은 (category_type, 필수 파라미터)면 같은 조회로 보고, 병합 시 신규로
    대체한다. 레거시 키(``major_primary`` 등)도 params가 같으면 canonical
    키 fetch에 의해 자연 대체된다 — 키 이름이 아니라 내용으로 판별한다.
    """
    if entry.category_type == "optional_elective":
        return (entry.category_type, entry.params.get("category"))
    if entry.category_type in ("major", "recognized_other_major"):
        # major(세부전공 필터)까지 식별에 포함 — group_key_for와 일치.
        return (
            entry.category_type,
            entry.params.get("collage"),
            entry.params.get("department"),
            entry.params.get("major"),
        )
    if entry.category_type == "graduated":
        return (
            entry.category_type,
            entry.params.get("collage"),
            entry.params.get("department"),
        )
    if entry.category_type == "required_elective":
        return (entry.category_type, entry.params.get("lecture_name"))
    if entry.category_type == "chapel":
        # 채플은 grade 기반 단일 종류만 캐시에 담는 불변이므로, lecture_name과
        # 무관하게 신규 fetch가 기존 chapel 그룹을 대체한다.
        return (entry.category_type,)
    if entry.category_type in ("connected_major", "united_major"):
        # 프로필당 단일 값 + 고정 키(connected_major/united_major) — 식별도
        # 고정으로 맞춰 키-식별 불일치를 없앤다 (critic MINOR-4 반영).
        return (entry.category_type,)
    if entry.category_type in ("find_by_professor", "find_by_lecture"):
        return (entry.category_type, entry.params.get("keyword"))
    return (entry.category_type,)


def merge_lectures_groups(
    existing: LecturesCache | None,
    year: int,
    semester: str,
    new_groups: dict[str, LectureGroupEntry],
) -> LecturesCache:
    """기존 그룹 보존 + 신규 그룹 병합 (덮어쓰기 제거, SPR-75).

    같은 조회(식별 파라미터)를 담은 기존 그룹만 신규 그룹으로 대체하고, 관련
    없는 기존 그룹은 그대로 보존한다. 콘텐츠 기반 대체 덕에 레거시 키
    (``major_primary``/``major_double``/``major_minor``/
    ``recognized_other_major_primary``)는 canonical 키 fetch 시 자동으로
    사라진다.

    **error 그룹은 기존 성공 그룹을 대체하지 않는다** (critic MAJOR-3 반영):
    부분 실패로 신규 fetch가 error 그룹이면, 같은 조회의 기존 성공 그룹이
    있으면 그대로 보존하고 error 그룹은 저장하지 않는다 (실패는 예외로 이미
    호출자에게 전달됨). 기존에 성공 그룹이 없었으면 error 그룹을 남겨
    ``load_lectures_cache``에서 실패를 확인할 수 있게 한다.

    find_lectures 자동 저장이 그룹별로 독립 실행되므로, 몇 번을 fetch하든
    캐시에는 "지금까지 성공한 fetch 전부"가 남는다.
    """
    incoming_by_id: dict[tuple[str, ...], str] = {}
    for key, entry in new_groups.items():
        incoming_by_id.setdefault(_identifying_params(entry), key)

    suppressed_error_keys: set[str] = set()
    merged: dict[str, LectureGroupEntry] = {}
    if existing is not None:
        for key, entry in existing.groups.items():
            incoming_key = incoming_by_id.get(_identifying_params(entry))
            if incoming_key is not None:
                incoming = new_groups[incoming_key]
                if incoming.error is not None and entry.error is None:
                    # 신규 error가 기존 성공을 대체하지 못하게 보존 + 신규 error 억제.
                    merged[key] = entry
                    suppressed_error_keys.add(incoming_key)
                    continue
                # 그 외: 신규 fetch가 같은 조회를 대체 → 기존은 버린다.
                continue
            merged[key] = entry

    for key, entry in new_groups.items():
        if key in suppressed_error_keys:
            continue
        merged[key] = entry
    return LecturesCache(
        year=year,
        semester=semester,
        groups=merged,
        cached_at=datetime.now(timezone.utc),
    )


def save_lectures_group(
    year: int,
    semester: str,
    key: str,
    entry: LectureGroupEntry,
    path: Path | None = None,
) -> tuple[LecturesCache, Path]:
    """그룹 1건을 기존 캐시에 병합 저장 (find_lectures 자동 저장용).

    기존 그룹을 보존하고 ``key`` 그룹을 병합/대체한다. 캐시가 없으면 새로 만든다.
    반환: (병합된 캐시, 저장 경로).
    """
    existing, _ = load_lectures_cache(year, semester, path=path)
    merged = merge_lectures_groups(existing, year, semester, {key: entry})
    target = _save_lectures_cache(merged, path=path)
    return merged, target
