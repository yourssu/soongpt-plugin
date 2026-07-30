"""FastMCP server exposing the Soongsil uSaint snapshot tool."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

from mcp.server.fastmcp import FastMCP

from .graduation import (
    is_cache_fresh,
    load_graduation_cache,
    save_graduation_cache,
)
from .lectures_cache import (
    LectureGroupEntry,
    LecturesCache,
    is_lectures_cache_fresh,
    load_lectures_cache as _load_lectures_cache_file,
    save_lectures_cache as _save_lectures_cache_file,
)
from .interview import (
    SECTION_NAMES as INTERVIEW_SECTION_NAMES,
    InterviewResult,
    list_interview_files as _list_interview_files,
    load_interview,
    save_interview,
)
from .profile import (
    SUBMISSION_FIELDS,
    UserProfile,
    load_profile,
    save_profile,
)
from .services.exceptions import SSOTokenError
from .services.rusaint_service import RusaintService
from .session_manager import SessionError, get_session_manager

mcp = FastMCP("soongpt-mcp")


def _jsonify(obj: Any) -> Any:
    """Pydantic 모델/중첩 구조를 JSON 직렬화 가능한 형태로 변환."""
    if hasattr(obj, "model_dump"):
        return _jsonify(obj.model_dump(mode="json"))
    if isinstance(obj, dict):
        return {k: _jsonify(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonify(x) for x in obj]
    return obj


async def _run_with_session(
    service_call: Callable[[str], Awaitable[Any]],
) -> Any:
    """세션 확보 후 service_call 실행. SSOTokenError 시 1회 재로그인 후 재시도.

    흐름:
    1. 세션 로드/웹 로그인 → service_call(session_json)
    2. SSOTokenError → invalidate → 자동 웹 로그인 → service_call 재실행
    3. 재시도에서도 SSOTokenError → 포기
    """
    manager = get_session_manager()
    try:
        session_json = await manager.get_valid_session()
    except SessionError as exc:
        raise RuntimeError(f"로그인 자동 진행 실패: {exc}") from exc

    try:
        return await service_call(session_json)
    except SSOTokenError:
        pass

    manager.invalidate()
    try:
        session_json = await manager.get_valid_session()
    except SessionError as exc:
        raise RuntimeError(f"세션 만료 후 재로그인 실패: {exc}") from exc

    try:
        return await service_call(session_json)
    except SSOTokenError as exc:
        raise RuntimeError(
            "재로그인 후에도 세션이 유효하지 않습니다. 숭실대 uSaint 서버에 일시적 문제일 수 있습니다."
        ) from exc


@mcp.tool()
async def get_usaint_snapshot() -> dict:
    """숭실대 USAINT에서 학적/수강/성적 데이터를 가져옵니다 (가공 전 raw).

    반환: basicInfo, takenCourses, lowGradeSubjectCodes, flags, warnings.
    졸업사정표는 별도 도구 get_graduation_status를 사용하세요.

    최초 호출 시 세션이 없으면 자동으로 브라우저가 열려 로그인 폼을 제공합니다.
    세션이 만료된 경우에도 동일하게 자동 재로그인이 진행됩니다.
    """
    service = RusaintService()
    snapshot = await _run_with_session(service.fetch_usaint_snapshot)
    if hasattr(snapshot, "model_dump"):
        return snapshot.model_dump(mode="json")
    return snapshot


@mcp.tool()
async def get_graduation_status(force_refresh: bool = False) -> dict:
    """숭실대 USAINT에서 졸업사정표 데이터를 가져옵니다 (약 5-6초).

    캐싱: 30일 TTL의 로컬 캐시 우선 사용. force_refresh=True이거나 캐시 만료/없음
    시 신규 fetch 후 캐시 갱신.

    반환: 개별 졸업 요건 상세(requirements) + 핵심 요약(graduationSummary) +
    메타(_cache: {source, cached_at, age_days}). source="cache"면 캐시 hit,
    "fresh"면 이번 호출에서 fetch.
    학적/수강 데이터는 get_usaint_snapshot을 사용하세요.

    최초 호출 시 세션이 없으면 자동으로 브라우저가 열려 로그인 폼을 제공합니다.
    세션이 만료된 경우에도 동일하게 자동 재로그인이 진행됩니다.
    """
    if not force_refresh:
        cached, cached_at = load_graduation_cache()
        if cached is not None and cached_at is not None and is_cache_fresh(cached_at):
            age_days = (datetime.now(timezone.utc) - cached_at).days
            return {**cached, "_cache": {
                "source": "cache",
                "cached_at": cached_at.isoformat(),
                "age_days": age_days,
            }}

    service = RusaintService()
    result = await _run_with_session(service.fetch_usaint_graduation_info)
    payload = _jsonify(result)
    if isinstance(payload, dict):
        save_graduation_cache(payload)
    now = datetime.now(timezone.utc)
    return {**payload, "_cache": {
        "source": "fresh",
        "cached_at": now.isoformat(),
        "age_days": 0,
    }}


@mcp.tool()
async def find_lectures(
    year: int,
    semester: str,
    category_type: str,
    collage: str | None = None,
    department: str | None = None,
    major: str | None = None,
    lecture_name: str | None = None,
    category: str | None = None,
    keyword: str | None = None,
    include_details: bool = False,
) -> dict:
    """숭실대 USAINT 강의시간표에서 특정 학기/카테고리 강의를 검색합니다.

    학기(semester): "1" | "2" | "summer" | "winter"

    category_type에 따라 필요한 파라미터:
    - "major" / "recognized_other_major": collage, department 필수, major 선택
    - "graduated": collage, department 필수
    - "required_elective" / "chapel": lecture_name 필수
    - "optional_elective": category 필수 (교양 분야명)
    - "connected_major" / "united_major": major 필수
    - "find_by_professor" / "find_by_lecture": keyword 필수
    - "education" / "cyber": 추가 파라미터 없음

    반환: { lectures: [...], count, fetchTime, includeDetails }
    include_details=True 시 강의계획서(syllabus)와 상세정보(detail) 포함 (느림).

    최초 호출 시 세션이 없으면 자동으로 브라우저가 열려 로그인 폼을 제공합니다.
    세션이 만료된 경우에도 동일하게 자동 재로그인이 진행됩니다.
    """
    service = RusaintService()

    async def call(session_json: str):
        return await service.find_lectures(
            session_json,
            year=year,
            semester=semester,
            category_type=category_type,
            collage=collage,
            department=department,
            major=major,
            lecture_name=lecture_name,
            category=category,
            keyword=keyword,
            include_details=include_details,
        )

    result = await _run_with_session(call)
    return _jsonify(result)


@mcp.tool()
async def list_optional_elective_categories(year: int, semester: str) -> dict:
    """숭실대 USAINT 강의시간표에서 교양선택 분야 목록을 가져옵니다.

    분야명은 학기/학번에 따라 다름 (예: "[‘23이후]과학·기술").
    해당 학기에 개설된 모든 교양선택 분야를 반환하므로, 사용자의 입학연도
    (profile.entered_year) 기준으로 '[‘NN이후]'/'[‘NN이전]' 필터링은
    호출자(스킬/LLM)가 처리해야 합니다.

    학기(semester): "1" | "2" | "summer" | "winter"

    반환: { categories: [str, ...], count, fetchTime }

    최초 호출 시 세션이 없으면 자동으로 브라우저가 열려 로그인 폼을 제공합니다.
    세션이 만료된 경우에도 동일하게 자동 재로그인이 진행됩니다.
    """
    service = RusaintService()

    async def call(session_json: str):
        return await service.find_optional_elective_categories(
            session_json, year=year, semester=semester
        )

    result = await _run_with_session(call)
    return _jsonify(result)


@mcp.tool()
async def load_lectures_cache(year: int, semester: str) -> dict:
    """저장된 강의 캐시 로드. 스킬 진입 시 가장 먼저 호출해 캐시 히트 여부 확인.

    응답의 `_cache.source`:
    - "cache": 캐시 hit (7일 이내). groups 사용 가능
    - "stale": 파일은 있으나 7일 경과. 스킬이 갱신 필요
    - "miss": 파일 없음. 스킬이 find_lectures로 채워야 함

    학기(semester): "1" | "2" | "summer" | "winter"

    반환: { year, semester, groups: {group_key: {category_type, params, lectures, count, error}},
            count, _cache: {source, cached_at, age_days} }
    """
    cache, cached_at = _load_lectures_cache_file(year, semester)
    now = datetime.now(timezone.utc)
    if cache is None or cached_at is None:
        return {
            "year": year,
            "semester": semester,
            "groups": {},
            "count": 0,
            "_cache": {"source": "miss", "cached_at": None, "age_days": None},
        }

    age_days = (now - cached_at).days
    source = "cache" if is_lectures_cache_fresh(cached_at, now) else "stale"
    return {
        "year": cache.year,
        "semester": cache.semester,
        "groups": _jsonify(cache.groups),
        "count": len(cache.groups),
        "_cache": {
            "source": source,
            "cached_at": cached_at.isoformat(),
            "age_days": age_days,
        },
    }


@mcp.tool()
async def save_lectures_cache(year: int, semester: str, groups: dict) -> dict:
    """강의 캐시 저장. 스킬이 find_lectures N회 결과를 group_key별로 취합해 전달.

    groups 형태 (각 값은 LectureGroupEntry 호환 dict):
    {
      "major_primary": {"category_type": "major", "params": {...}, "lectures": [...], "count": N, "error": null},
      "optional_elective_<분야명>": {"category_type": "optional_elective", "params": {...}, ...},
      ...
    }

    학기(semester): "1" | "2" | "summer" | "winter"

    반환: { year, semester, count, saved_at, path }
    """
    parsed: dict[str, LectureGroupEntry] = {}
    for key, entry in groups.items():
        if not isinstance(entry, dict):
            raise ValueError(
                f"groups[{key!r}]는 dict여야 함: {type(entry).__name__}"
            )
        parsed[key] = LectureGroupEntry.model_validate(entry)

    cache = LecturesCache(
        year=year,
        semester=semester,
        groups=parsed,
        cached_at=datetime.now(timezone.utc),
    )
    target = _save_lectures_cache_file(cache)
    return {
        "year": cache.year,
        "semester": cache.semester,
        "count": len(cache.groups),
        "saved_at": cache.cached_at.isoformat(),
        "path": str(target),
    }


def run() -> None:
    """Run the MCP server on stdio (default transport)."""
    mcp.run()


# User Profile Tools


async def _fetch_basic_info_via_session() -> tuple[Any, list[str]]:
    """학적 기본 정보만 가볍게 조회 (1세션, ~2-3초).

    refresh_user_profile이 전체 snapshot(9초) 대신 이 경로를 사용.
    세션 만료 시 자동 재로그인은 _run_with_session에서 처리됨.

    반환: (BasicInfo, warnings) — warnings는 NO_SEMESTER_INFO 등 데이터 누락 코드.
    """
    service = RusaintService()
    return await _run_with_session(service.fetch_basic_info)


@mcp.tool()
async def get_user_profile() -> dict:
    """저장된 사용자 프로필 반환.

    프로필이 없으면 profile=None과 함께 안내 메시지를 반환합니다.
    프로필을 처음 만들려면 refresh_user_profile을 호출해 USAINT에서 초기값을 가져오거나
    set_user_profile로 필드를 직접 입력하세요.
    """
    profile = load_profile()
    if profile is None:
        return {
            "profile": None,
            "guidance": (
                "저장된 프로필이 없습니다. refresh_user_profile을 호출해 USAINT에서 초기값을 "
                "가져오거나 set_user_profile로 학번/이름 등을 직접 설정하세요."
            ),
        }
    return {"profile": _jsonify(profile)}


@mcp.tool()
async def set_user_profile(field: str, value: Any) -> dict:
    """프로필의 단일 필드를 부분 업데이트 후 저장된 전체 프로필 반환.

    허용 필드: student_id, name, college, department, grade (1~6),
    track, entered_year, double_major, connected_major, minor. grade는 정수,
    나머지는 문자열. 빈 문자열/None은 필드를 None으로 설정합니다.
    updated_at은 자동 갱신됩니다.

    프로필이 없으면 빈 프로필을 생성한 뒤 필드를 채웁니다.
    """
    if field not in SUBMISSION_FIELDS:
        raise ValueError(
            f"알 수 없는 프로필 필드: {field}. 허용 필드: {sorted(SUBMISSION_FIELDS)}"
        )

    existing = load_profile() or UserProfile()
    updated = existing.apply_partial_update({field: value})
    save_profile(updated)
    return {"profile": _jsonify(updated)}


@mcp.tool()
async def refresh_user_profile(preserve_user_overrides: bool = True) -> dict:
    """USAINT에서 학적 기본 정보를 재추출해 프로필을 갱신 (~2-3초).

    preserve_user_overrides=True(기본)면 USAINT가 제공하는 6개 필드
    (department, grade, entered_year, double_major, connected_major, minor)를
    항상 USAINT 값으로 덮어쓰고, 나머지 필드(student_id, name, college, track)는
    기존 저장값을 보존합니다.

    False면 기존 프로필을 무시하고 USAINT 값만으로 새 프로필을 만듭니다
    (비-USAINT 필드는 모두 None으로 리셋).

    최초 호출 시 세션이 없으면 자동으로 브라우저가 열려 로그인 폼을 제공합니다.
    세션이 만료된 경우에도 동일하게 자동 재로그인이 진행됩니다.

    응답의 warnings는 USAINT 데이터 누락 코드 (예: NO_SEMESTER_INFO).
    """
    basic_info, warnings = await _fetch_basic_info_via_session()
    fresh = UserProfile.from_basic_info(basic_info)
    refreshed = ["department", "grade", "entered_year", "double_major", "connected_major", "minor"]

    if not preserve_user_overrides:
        save_profile(fresh)
        return {
            "profile": _jsonify(fresh),
            "refreshed_fields": refreshed,
            "reset_user_overrides": True,
            "warnings": warnings,
        }

    existing = load_profile() or UserProfile()
    merged = existing.model_copy(
        update={
            "department": fresh.department,
            "grade": fresh.grade,
            "entered_year": fresh.entered_year,
            "double_major": fresh.double_major,
            "connected_major": fresh.connected_major,
            "minor": fresh.minor,
            "updated_at": datetime.now(timezone.utc),
        }
    )
    save_profile(merged)
    return {
        "profile": _jsonify(merged),
        "refreshed_fields": refreshed,
        "reset_user_overrides": False,
        "warnings": warnings,
    }


# Interview Tools


@mcp.tool()
async def get_interview(year: int, semester: str) -> dict:
    """특정 학기 인터뷰 결과 조회.

    semester: "1" | "2"
    반환: { interview: {...} | null, completion: {section: bool}, guidance? }
    인터뷰가 없으면 interview=null과 안내 메시지.
    """
    interview = load_interview(year, semester)
    if interview is None:
        return {
            "interview": None,
            "completion": {
                name: False for name in INTERVIEW_SECTION_NAMES
            },
            "guidance": (
                f"저장된 인터뷰가 없습니다 ({year}-{semester}). "
                "set_interview로 섹션별로 채우세요."
            ),
        }
    return {
        "interview": _jsonify(interview),
        "completion": interview.completion_summary(),
    }


@mcp.tool()
async def set_interview(
    year: int,
    semester: str,
    section: str,
    content: str,
) -> dict:
    """특정 학기 인터뷰의 한 섹션을 텍스트로 저장.

    semester: "1" | "2"
    section: semester_strategy | time_preferences | subject_preferences
    content: 해당 섹션에 저장할 자연어 요약 텍스트. 기존 내용을 덮어씀.

    인터뷰가 없으면 새로 생성. updated_at 자동 갱신.
    """
    if semester not in ("1", "2"):
        raise ValueError(
            f"semester는 '1' 또는 '2'만 허용됩니다: {semester!r}"
        )
    if section not in INTERVIEW_SECTION_NAMES:
        raise ValueError(
            f"알 수 없는 인터뷰 섹션: {section}. "
            f"허용 섹션: {sorted(INTERVIEW_SECTION_NAMES)}"
        )

    existing = load_interview(year, semester) or InterviewResult(
        year=year, semester=semester
    )
    updated = existing.apply_section_update(section, content)
    save_interview(updated)
    return {
        "interview": _jsonify(updated),
        "completion": updated.completion_summary(),
    }


@mcp.tool()
async def list_interviews() -> dict:
    """저장된 모든 학기 인터뷰 목록 반환.

    반환: { interviews: [{year, semester, completion, updated_at}], count }
    """
    items = _list_interview_files()
    return {"interviews": items, "count": len(items)}


if __name__ == "__main__":
    run()
