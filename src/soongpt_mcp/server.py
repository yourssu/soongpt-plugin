"""MCPServer exposing the Soongsil uSaint snapshot tool."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

from mcp.server.mcpserver import MCPServer

from .config import get_config
from .department_map import (
    DepartmentMap,
    is_department_map_fresh,
    load_bundled_department_map,
)
from .department_map import (
    load_department_map as load_dept_map_cache,
)
from .department_map import (
    save_department_map as save_dept_map_cache,
)
from .graduation import (
    is_cache_fresh,
    load_graduation_cache,
    save_graduation_cache,
)
from .interview import (
    SECTION_NAMES as INTERVIEW_SECTION_NAMES,
)
from .interview import (
    InterviewResult,
    load_interview,
    save_interview,
)
from .interview import (
    list_interview_files as _list_interview_files,
)
from .lectures_cache import (
    LectureGroupEntry,
    LecturesCache,
    is_lectures_cache_fresh,
)
from .lectures_cache import (
    load_lectures_cache as _load_lectures_cache_file,
)
from .lectures_cache import (
    save_lectures_cache as _save_lectures_cache_file,
)
from .profile import (
    SUBMISSION_FIELDS,
    UserProfile,
)
from .semester import current_academic_period
from .services.exceptions import (
    RusaintInternalError,
    SSOTokenError,
    is_session_expiry_error,
)
from .services.rusaint_service import RusaintService
from .session_manager import SessionError, get_session_manager
from .snapshot_cache import (
    SnapshotCache,
    cleanup_legacy_profiles,
    is_snapshot_cache_fresh,
    load_profile,
    load_snapshot_cache,
    save_profile,
    save_snapshot_cache,
)
from .timetable_cache import (
    TimetableCandidate,
    add_candidate,
    backup_corrupt_timetable_cache,
    clear_timetable_cache,
    load_timetable_cache,
    save_timetable_cache,
)
from .timetable_parsing import (
    ParsedLecture,
    build_subject_groups,
    find_conflicts,
    parse_lectures,
)

mcp = MCPServer("soongpt-mcp")


def _jsonify(obj: Any) -> Any:
    """Pydantic 모델/중첩 구조를 JSON 직렬화 가능한 형태로 변환."""
    if hasattr(obj, "model_dump"):
        return _jsonify(obj.model_dump(mode="json"))
    if isinstance(obj, dict):
        return {k: _jsonify(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonify(x) for x in obj]
    return obj


# SPR-67: 강의시간표(course_schedule) 계열 도구의 동시성 상한 세마포어.
#
# USAINT WebDynpro 포털은 동일 SSO 세션의 동시 요청을 서버 쪽에서 순차 처리한다
# (세션 객체를 N개 만들어도 포털 입장에선 같은 사용자 → 직렬화). 그래서
# find_lectures 18개를 한 번에 병렬로 쏘면 응답이 2.3초 → 3.3초 → … → 30.4초로
# 계단형 도착하고, 마지막 것은 HTTP 타임아웃·WebDynpro 에러·SSO 세션 끊김 위험에
# 노출된다. 강의시간표 계열 도구가 이 세마포어를 공유해 동시 송출을 상한으로
# 묶으면 각 호출이 안전한 시간(~8초) 내에 끝나 위험이 사라진다.
#
# **목표는 "빠르게"가 아니라 "안전하게"** — 총 조회 시간은 포털 직렬화 때문에
# 비슷하지만 타임아웃/에러/세션끊김 방지가 실익이다.
#
# 스코프: find_lectures·list_required_electives·list_optional_elective_categories가
# 공유 (soongpt-available-lectures 스킬이 한 메시지에 이 셋을 섞어 병렬로 쏘므로,
# 도구별 세마포어를 두면 합산 12(=4×3)개가 동시에 쏴 의도가 무의미해진다).
# get_usaint_snapshot·get_graduation_status·refresh_user_profile·load_department_map은
# 이 세마포어를 타지 않는다 (강의시간표 전용).
_course_schedule_semaphore: asyncio.Semaphore | None = None


def _get_course_schedule_semaphore() -> asyncio.Semaphore:
    """강의시간표 계열 도구가 공유하는 동시성 상한 세마포어 (lazy 생성).

    asyncio.Semaphore는 생성 시점이 아니라 await(acquire/release) 시점의
    running loop에 묶이므로(Python 3.10+), 모듈 임포트 시점이 아닌 첫 도구
    호출 시점에 만들어도 안전하다. lazy 생성이 테스트에서 config(상한값)를
    주입하고 모듈 글로벌을 리셋하기도 쉽게 한다.
    """
    global _course_schedule_semaphore
    if _course_schedule_semaphore is None:
        _course_schedule_semaphore = asyncio.Semaphore(
            get_config().course_schedule_concurrency
        )
    return _course_schedule_semaphore


async def _run_with_session(
    service_call: Callable[[str], Awaitable[Any]],
) -> Any:
    """세션 확보 후 service_call 실행. 세션 만료 시 1회 재로그인 후 재시도.

    세션 만료 신호는 SSOTokenError 외에도 로그인 페이지 파싱 실패
    ("Cannot find SSR Client form" → RusaintInternalError)가 해당된다.
    저장된 세션이 만료된 채 첫 요청을 보내면 유세인트가 로그인 페이지를
    반환하기 때문이다. 그 외 RusaintInternalError는 그대로 전파한다.

    흐름:
    1. 세션 로드/웹 로그인 → service_call(session_json)
    2. 세션 만료 신호(SSOTokenError 또는 파싱 실패) → invalidate
       → 자동 웹 로그인 → service_call 재실행
    3. 재시도에서도 세션 만료 신호 → 포기
    """
    manager = get_session_manager()
    try:
        session_json = await manager.get_valid_session()
    except SessionError as exc:
        raise RuntimeError(f"로그인 자동 진행 실패: {exc}") from exc

    try:
        return await service_call(session_json)
    except (SSOTokenError, RusaintInternalError) as exc:
        if isinstance(exc, RusaintInternalError) and not is_session_expiry_error(exc):
            raise

    manager.invalidate()
    try:
        session_json = await manager.get_valid_session()
    except SessionError as exc:
        raise RuntimeError(f"세션 만료 후 재로그인 실패: {exc}") from exc

    try:
        return await service_call(session_json)
    except (SSOTokenError, RusaintInternalError) as exc:
        if isinstance(exc, RusaintInternalError) and not is_session_expiry_error(exc):
            raise
        raise RuntimeError(
            "재로그인 후에도 세션이 유효하지 않습니다. 숭실대 uSaint 서버에 일시적 문제일 수 있습니다."
        ) from exc


@mcp.tool()
async def get_usaint_snapshot(force_refresh: bool = False) -> dict:
    """숭실대 USAINT에서 학적/수강/성적 데이터를 가져와 로컬에 저장합니다.

    캐싱: 학기별 스냅샷(snapshot_{year}_{semester}.json)을 단일 SoT로 사용.
    force_refresh=False(기본)면 수강이력 캐시가 30일 이내일 때 USAINT 재호출 없이
    저장된 데이터를 반환하고, 미스/만료 시 fetch 후 프로필·수강이력을 저장합니다.
    프로필(profile)도 함께 갱신하므로, 이 도구 호출 하나로 프로필 + 수강이력이
    준비됩니다. 이후 시간표 단계는 get_user_profile/get_usaint_snapshot의 캐시를
    재사용하면 됩니다.

    반환: basicInfo, takenCourses, lowGradeSubjectCodes, subjectNames, flags,
    warnings + _cache: {source: "cache"|"fresh", fetched_at, age_days}.
    takenCourses는 학기별 수강 과목을 코드+강의명(subjects) 인라인으로 제공.
    subjectNames는 이 subjects로부터 자동 파생된 {코드: 강의명} read-only 매핑 —
    실제 수강한 과목만 포함하며, 재수강 대체과목 추천 코드처럼 수강 이력이 없는
    코드는 미포함(코드를 그대로 폴백으로 사용). 졸업사정표는 별도 도구
    get_graduation_status를 사용하세요.

    최초 호출 시 세션이 없으면 자동으로 브라우저가 열려 로그인 폼을 제공합니다.
    세션이 만료된 경우에도 동일하게 자동 재로그인이 진행됩니다.
    """
    year, semester = current_academic_period()
    now = datetime.now(timezone.utc)

    if not force_refresh:
        cached, fetched_at = load_snapshot_cache(year, semester)
        if (
            cached is not None
            and fetched_at is not None
            and is_snapshot_cache_fresh(fetched_at, now=now)
        ):
            return _format_snapshot_response(
                cached, source="cache", fetched_at=fetched_at, now=now
            )

    service = RusaintService()
    snapshot = await _run_with_session(service.fetch_usaint_snapshot)
    profile = _merge_profile_from_basic_info(snapshot.basicInfo)
    cache = SnapshotCache(
        year=year,
        semester=semester,
        profile=profile,
        basicInfo=snapshot.basicInfo,
        takenCourses=snapshot.takenCourses,
        lowGradeSubjectCodes=snapshot.lowGradeSubjectCodes,
        flags=snapshot.flags,
        warnings=snapshot.warnings,
        fetched_at=now,
    )
    target = save_snapshot_cache(cache)
    cleanup_legacy_profiles(year, semester, target)
    return _format_snapshot_response(cache, source="fresh", fetched_at=now, now=now)


def _derive_subject_names(cache: SnapshotCache) -> dict[str, str]:
    """takenCourses.subjects로부터 {코드: 강의명}을 파생하는 응답 read-only 필드.

    쓰기 진실 소스는 subjects 하나 — subjectNames는 매번 subjects에서 재파생되며
    별도로 저장되지 않는다. name이 None/빈 과목은 제외. lowGrade 대체과목
    추천 코드 등 실제 수강 이력이 없는 코드는 subjects에 없으므로 여기에도 없음 —
    소비측(interview 스킬)이 subjectNames.get(code, code)로 코드 자체를 폴백.
    """
    names: dict[str, str] = {}
    for course in cache.takenCourses:
        for subject in course.subjects:
            if subject.name:
                names[subject.code] = subject.name
    return names


def _format_snapshot_response(
    cache: SnapshotCache,
    source: str,
    fetched_at: datetime,
    now: datetime,
) -> dict:
    """get_usaint_snapshot 응답 포맷팅 (cache/fresh 공통)."""
    age_days = (now - fetched_at).days if fetched_at is not None else None
    return {
        "basicInfo": _jsonify(cache.basicInfo),
        "takenCourses": _jsonify(cache.takenCourses),
        "lowGradeSubjectCodes": _jsonify(cache.lowGradeSubjectCodes),
        "subjectNames": _derive_subject_names(cache),
        "flags": _jsonify(cache.flags),
        "warnings": cache.warnings,
        "_cache": {
            "source": source,
            "fetched_at": fetched_at.isoformat() if fetched_at is not None else None,
            "age_days": age_days,
        },
    }


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

    async with _get_course_schedule_semaphore():
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

    async with _get_course_schedule_semaphore():
        result = await _run_with_session(call)
    return _jsonify(result)


@mcp.tool()
async def list_required_electives(year: int, semester: str) -> dict:
    """숭실대 USAINT 강의시간표에서 교양필수 과목명 목록을 가져옵니다.

    과목명은 학기/학번에 따라 다름 (예: "[SW와AI]AI개발과실전", "한반도평화와통일").
    해당 학기에 개설된 모든 교양필수 과목명을 반환하므로, 각 과목명을 그대로
    ``find_lectures(category_type="required_elective", lecture_name=<과목명>)`` 에
    넘겨 해당 과목의 강의 목록을 조회하면 됩니다. 입학연도 필터링은 필요하지
    않습니다 (optional_elective의 '[‘NN이후]' 학번 태그와 달리 과목명에 연도 태그
    없음).

    학기(semester): "1" | "2" | "summer" | "winter"

    반환: { lecture_names: [str, ...], count, fetchTime }

    최초 호출 시 세션이 없으면 자동으로 브라우저가 열려 로그인 폼을 제공합니다.
    세션이 만료된 경우에도 동일하게 자동 재로그인이 진행됩니다.
    """
    service = RusaintService()

    async def call(session_json: str):
        return await service.find_required_electives(
            session_json, year=year, semester=semester
        )

    async with _get_course_schedule_semaphore():
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


@mcp.tool()
async def parse_lectures_cache(year: int, semester: str) -> dict:
    """저장된 강의 캐시를 시간표 파싱 결과로 변환합니다.

    소스 = load_lectures_cache() 원본(schedule_room·target·field 포함)을
    파싱합니다. 별도 파싱 캐시는 없습니다 (저비용, 매번 재계산).

    응답의 `_cache.source` (load_lectures_cache 관례와 동일):
    - "cache": 캐시 hit (7일 이내)
    - "stale": 파일은 있으나 7일 경과 — 데이터는 그대로 반환하고 source만 표시
      (+ guidance로 새로고침 안내)
    - "miss": 파일 없음 — parsed 비움 + guidance (스킬이 find_lectures로 채워야 함)

    반환: { year, semester, parsed: [ParsedLecture], subject_groups,
            stats: {total, parsed_ok, uncertain, empty}, _cache, guidance? }
    parsed[i]는 code/name/subject_key/credits/slots/parse_status/parse_warnings와
    LLM 판단용 pass-through(target/field/professor/division/department/category/
    sub_category — 이수구분 판단은 category: "교필"/"전기-"/"전필-"/"전선-"/
    "교선"/"교직")를 담습니다.
    subject_groups = dedup 후 parsed 기준 {subject_key(code[:-2]): [code 목록]}
    인덱스. 컴포저는 이 인덱스로 분반 그룹을 잡고, 각 code로 parsed에서 조회하세요.

    학기(semester): "1" | "2" | "summer" | "winter"
    """
    cache, cached_at = _load_lectures_cache_file(year, semester)
    now = datetime.now(timezone.utc)

    if cache is None or cached_at is None:
        return {
            "year": year,
            "semester": semester,
            "parsed": [],
            "subject_groups": {},
            "stats": {"total": 0, "parsed_ok": 0, "uncertain": 0, "empty": 0},
            "_cache": {"source": "miss", "cached_at": None, "age_days": None},
            "guidance": (
                "저장된 강의 캐시가 없습니다. save_lectures_cache로 먼저 채워주세요 "
                "(soongpt-available-lectures 스킬이 find_lectures 결과를 취합해 저장)."
            ),
        }

    source = "cache" if is_lectures_cache_fresh(cached_at, now) else "stale"
    all_lectures: list[dict] = []
    for group in cache.groups.values():
        all_lectures.extend(group.lectures)
    parsed = parse_lectures(all_lectures)
    response = {
        "year": year,
        "semester": semester,
        "parsed": _jsonify(parsed),
        "subject_groups": build_subject_groups(parsed),
        "stats": {
            "total": len(parsed),
            "parsed_ok": sum(1 for p in parsed if p.parse_status == "ok"),
            "uncertain": sum(1 for p in parsed if p.parse_status == "uncertain"),
            "empty": sum(1 for p in parsed if p.parse_status == "empty"),
        },
        "_cache": {
            "source": source,
            "cached_at": cached_at.isoformat(),
            "age_days": (now - cached_at).days,
        },
    }
    if source == "stale":
        response["guidance"] = "강의 데이터가 7일 지났어요. 새로고침할까요?"
    return response


@mcp.tool()
async def check_timetable_conflicts(lectures: list[dict]) -> dict:
    """단일 후보 강의 리스트의 시간 충돌을 검사합니다.

    입력: parse_lectures_cache의 parsed 항목(ParsedLecture dict) 리스트.
    단일 후보(6~10과목)만 전달하세요. 30개 초과 시 ValueError를 반환합니다
    (전수 비교/O(N²) 우회 및 의미론 혼란 방지).

    반환: { conflicts: [Conflict], has_blocking_conflict: bool, warnings: [str] }
    Conflict는 겹치는 요일(days)과 구간(start_min/end_min) + 원본 슬롯 문자열을
    담습니다. uncertain/empty 슬롯은 충돌 검사에서 건너뛰고 warnings로 보고합니다.
    """
    if len(lectures) > 30:
        raise ValueError(
            f"lectures는 단일 후보 강의 리스트만 허용합니다 (30개 이하). "
            f"전달: {len(lectures)}개. 전수 비교 금지 — 1회 1후보만 전달하세요."
        )

    parsed = [ParsedLecture.model_validate(item) for item in lectures]
    skipped = [p.code for p in parsed if p.parse_status != "ok"]
    warnings: list[str] = []
    if skipped:
        warnings.append(
            f"불확정 강의 {len(skipped)}개 (uncertain/empty): "
            f"{', '.join(skipped)} — 충돌 검사에서 제외"
        )
    conflicts = find_conflicts(parsed)
    return {
        "conflicts": _jsonify(conflicts),
        "has_blocking_conflict": bool(conflicts),
        "warnings": warnings,
    }


@mcp.tool()
async def load_timetable_candidates(year: int, semester: str) -> dict:
    """저장된 시간표 후보 목록 로드.

    컴포저(soongpt-timetable-composer)가 save_timetable_candidate로 저장한 후보를
    로드합니다. 재개 시 후보 확인 + generation_params(인터뷰/강의 캐시 스냅샷)로
    mismatch 판정에 사용합니다. TTL은 없습니다 — clear로만 무효화.

    학기(semester): "1" | "2" | "summer" | "winter"

    반환: { year, semester, candidates: [TimetableCandidate], generation_params,
            _cache: {source: "hit"|"miss", saved_at} } — miss 시 candidates:[] +
            guidance (새로 조합 안내)
    """
    cache = load_timetable_cache(year, semester)
    if cache is None:
        return {
            "year": year,
            "semester": semester,
            "candidates": [],
            "generation_params": {},
            "_cache": {"source": "miss", "saved_at": None},
            "guidance": (
                "저장된 후보가 없습니다. soongpt-timetable-composer로 새로 조합하세요 "
                "(또는 builder가 '시간표 짜줘'로 진입하면 5단계에서 자동 위임)."
            ),
        }
    return {
        "year": cache.year,
        "semester": cache.semester,
        "candidates": _jsonify(cache.candidates),
        "generation_params": cache.generation_params,
        "_cache": {
            "source": "hit",
            "saved_at": cache.cached_at.isoformat(),
        },
    }


@mcp.tool()
async def save_timetable_candidate(
    year: int,
    semester: str,
    candidate: dict,
    generation_params: dict | None = None,
) -> dict:
    """시간표 후보 1건 저장.

    후보의 `lecture_codes`가 강의 캐시(load_lectures_cache)에 존재하는 code인지
    검증합니다 — LLM이 code를 전사하며 생기는 오류를 여기서 차단합니다.
    같은 `name`의 기존 후보가 있으면 교체(replace)하고, 없으면 append합니다
    (수정 반복 시 폐기 후보가 축적되지 않게).

    학기(semester): "1" | "2" | "summer" | "winter"

    candidate 필드: name(str, 후보 이름), lecture_codes(list[str]),
    total_credits(float), has_blocking_conflict(bool), conflicts_summary(str,
    check_timetable_conflicts의 warnings 포함 필수), notes(str=""), confirmed(bool,
    사용자 확정 시 True), created_at(선택 — 기본값 서버 시각)

    generation_params (선택): 재개 시 mismatch 판정용 생성 스냅샷 —
    {"interview_updated_at": get_interview().interview.updated_at,
    "lectures_cached_at": load_lectures_cache()._cache.cached_at}.
    기존 값과 merge되어 캐시에 저장됩니다 (재개 분기용).

    반환: { saved: true, replaced: bool, count: int, path } — 기존 파일이 손상돼
    백업으로 옮겨졌으면 corrupt_replaced(백업 경로)가 추가됩니다 (보존 약속 유지).
    """
    parsed = TimetableCandidate.model_validate(candidate)

    # code 존재 검증 (중요①) — parse_lectures_cache의 code를 그대로 썼는지 확인
    lectures_cache, _ = _load_lectures_cache_file(year, semester)
    known_codes: set[str] = set()
    if lectures_cache is not None:
        for group in lectures_cache.groups.values():
            for lecture in group.lectures:
                code = lecture.get("code")
                if code:
                    known_codes.add(str(code))
    missing = [code for code in parsed.lecture_codes if code not in known_codes]
    if missing:
        raise ValueError(
            f"후보 lecture_codes 중 강의 캐시에 없는 code가 있습니다: "
            f"{', '.join(missing)}. parse_lectures_cache의 parsed[].code를 "
            f"그대로 사용하세요 (save_lectures_cache로 먼저 캐시를 채워야 합니다)."
        )

    existing = load_timetable_cache(year, semester)
    # 손상 파일은 지우지 않고 .corrupt-<ts>로 백업한 뒤 새 캐시로 교체 —
    # "파일은 보존됨" 약속이 다음 저장에서 깨지지 않게 한다.
    corrupt_backup = None
    if existing is None:
        corrupt_backup = backup_corrupt_timetable_cache(year, semester)
    updated, replaced = add_candidate(
        existing, parsed, year=year, semester=semester
    )
    if generation_params:
        merged = dict(updated.generation_params)
        merged.update(generation_params)
        updated = updated.model_copy(update={"generation_params": merged})
    target = save_timetable_cache(updated)
    result = {
        "saved": True,
        "replaced": replaced,
        "count": len(updated.candidates),
        "path": str(target),
    }
    if corrupt_backup is not None:
        result["corrupt_replaced"] = str(corrupt_backup)
    return result


@mcp.tool()
async def clear_timetable_candidates(year: int, semester: str) -> dict:
    """저장된 시간표 후보를 삭제합니다.

    "다시 짜자"처럼 후보를 처음부터 다시 조합할 때 호출합니다. 파일이 없어도
    오류 없이 cleared: false를 반환합니다.

    학기(semester): "1" | "2" | "summer" | "winter"

    반환: { cleared: bool }
    """
    cleared = clear_timetable_cache(year, semester)
    return {"cleared": cleared}


def run() -> None:
    """Run the MCP server on stdio (default transport)."""
    mcp.run()


@mcp.tool()
async def load_department_map(year: int, force_refresh: bool = False) -> dict:
    """학과-단과대 매핑 캐시. 복수/부전공 학과의 단과대를 자동으로 찾기 위해 사용.

    3-tier 로딩 순서 (force_refresh=False일 때):
    1. 로컬 캐시 — 이전 호출에서 빌드해 둔 파일 (즉시)
    2. 번들 seed — 패키지에 커밋된 정적 파일 (즉시, 메인테이너가 연 1회 갱신)
    3. 자동 빌드 — USAINT에서 실시간 fetch (10~20초, 로컬 캐시에 저장)

    학과 신설/통폐합이 의심되면 force_refresh=True로 강제 재빌드.
    semester는 오늘 날짜 기준 현재 학기(1학기/2학기)를 자동 사용.

    반환: {year, semester, mapping, count, _cache: {source, built_at, age_days}}.
    source="cache" | "bundled" | "fresh" 로 데이터 출처 표시.

    최초 호출 시 세션이 없으면 자동으로 브라우저가 열려 로그인 폼을 제공합니다.
    세션이 만료된 경우에도 동일하게 자동 재로그인이 진행됩니다.
    """
    now = datetime.now(timezone.utc)

    if not force_refresh:
        cached, built_at = load_dept_map_cache(year)
        if (
            cached is not None
            and built_at is not None
            and is_department_map_fresh(built_at, now=now)
        ):
            return _format_dept_map_response(cached, "cache", built_at, now)

        bundled = load_bundled_department_map(year)
        if bundled is not None and is_department_map_fresh(
            bundled.built_at, now=now
        ):
            return _format_dept_map_response(bundled, "bundled", bundled.built_at, now)

    _, semester = current_academic_period()
    service = RusaintService()

    async def call(session_json: str):
        return await service.build_department_map(
            session_json, year=year, semester=semester
        )

    built = await _run_with_session(call)
    dm = DepartmentMap(
        year=year,
        semester=semester,
        mapping=built["mapping"],
        built_at=now,
    )
    save_dept_map_cache(dm)
    return _format_dept_map_response(dm, "fresh", dm.built_at, now)


def _format_dept_map_response(
    dm: DepartmentMap,
    source: str,
    built_at: datetime,
    now: datetime,
) -> dict:
    """load_department_map 응답 포맷팅 (cache/bundled/fresh 공통)."""
    age_days = (now - built_at).days
    return {
        "year": dm.year,
        "semester": dm.semester,
        "mapping": dm.mapping,
        "count": len(dm.mapping),
        "_cache": {
            "source": source,
            "built_at": built_at.isoformat(),
            "age_days": age_days,
        },
    }


# User Profile Tools


async def _fetch_basic_info_via_session() -> tuple[Any, list[str]]:
    """학적 기본 정보만 가볍게 조회 (1세션, ~2-3초).

    refresh_user_profile이 전체 snapshot(9초) 대신 이 경로를 사용.
    세션 만료 시 자동 재로그인은 _run_with_session에서 처리됨.

    반환: (BasicInfo, warnings) — warnings는 NO_SEMESTER_INFO 등 데이터 누락 코드.
    """
    service = RusaintService()
    return await _run_with_session(service.fetch_basic_info)


def _merge_profile_from_basic_info(basic_info: Any) -> UserProfile:
    """USAINT basicInfo를 프로필에 병합 (USAINT 9필드만 덮어쓰기).

    기존 프로필의 학번/이름/트랙 등 사용자 입력 필드는 보존하고,
    USAINT가 제공하는 9개 필드(department, college, grade, entered_year,
    double_major, connected_major, minor, teaching_certification,
    teaching_major)만 갱신. get_usaint_snapshot과 refresh_user_profile이
    공용으로 사용한다.

    college 병합 정책: 다른 USAINT 필드와 동일하게 **USAINT 값을 우선
    덮어쓴다**. SPR-55 이전엔 USAINT collage를 추출하지 않아 college가 항상
    비어 사용자 수동 입력에 의존했지만, 이제 USAINT가 단과대를 제공하므로
    USAINT를 진실 소스로 삼는다. fresh.college가 None(이론상만)이면 기존
    수동 입력도 None으로 정리 — 다른 필드와 일관된 동작.
    """
    fresh = UserProfile.from_basic_info(basic_info)
    existing = load_profile() or UserProfile()
    merged = existing.model_copy(
        update={
            "department": fresh.department,
            "college": fresh.college,
            "grade": fresh.grade,
            "entered_year": fresh.entered_year,
            "double_major": fresh.double_major,
            "connected_major": fresh.connected_major,
            "minor": fresh.minor,
            "teaching_certification": fresh.teaching_certification,
            "teaching_major": fresh.teaching_major,
            "updated_at": datetime.now(timezone.utc),
        }
    )
    return merged


@mcp.tool()
async def get_user_profile() -> dict:
    """저장된 사용자 프로필 반환.

    프로필이 없으면 profile=None과 함께 안내 메시지를 반환합니다.
    프로필은 get_usaint_snapshot() 호출 시 USAINT 학적정보로 자동 채워집니다.
    사용자가 직접 수정하려면 set_user_profile을 사용하세요.
    """
    profile = load_profile()
    if profile is None:
        return {
            "profile": None,
            "guidance": (
                "저장된 프로필이 없습니다. get_usaint_snapshot()을 호출하면 USAINT "
                "학적정보로 프로필이 자동 채워집니다."
            ),
        }
    return {"profile": _jsonify(profile)}


@mcp.tool()
async def set_user_profile(field: str, value: Any) -> dict:
    """프로필의 단일 필드를 부분 업데이트 후 저장된 전체 프로필 반환.

    허용 필드: student_id, name, college, department, grade (1~6),
    track, entered_year, double_major, connected_major, minor,
    teaching_certification (bool), teaching_major. grade는 정수,
    teaching_certification은 bool, 나머지는 문자열. 빈 문자열/None은 필드를
    None으로(또는 bool 필드의 경우 False로) 설정합니다.
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

    preserve_user_overrides=True(기본)면 USAINT가 제공하는 9개 필드
    (department, college, grade, entered_year, double_major, connected_major,
    minor, teaching_certification, teaching_major)를 항상 USAINT 값으로
    덮어쓰고, 나머지 필드(student_id, name, track)는 기존 저장값을 보존합니다.

    college도 USAINT에서 추출 가능하므로(SPR-55) 기존 사용자 수동 입력
    college가 있어도 USAINT 값을 우선 덮어씁니다.

    False면 기존 프로필을 무시하고 USAINT 값만으로 새 프로필을 만듭니다
    (비-USAINT 필드는 모두 None으로 리셋).

    최초 호출 시 세션이 없으면 자동으로 브라우저가 열려 로그인 폼을 제공합니다.
    세션이 만료된 경우에도 동일하게 자동 재로그인이 진행됩니다.

    응답의 warnings는 USAINT 데이터 누락 코드 (예: NO_SEMESTER_INFO).
    """
    basic_info, warnings = await _fetch_basic_info_via_session()
    fresh = UserProfile.from_basic_info(basic_info)
    refreshed = [
        "department",
        "college",
        "grade",
        "entered_year",
        "double_major",
        "connected_major",
        "minor",
        "teaching_certification",
        "teaching_major",
    ]

    if not preserve_user_overrides:
        save_profile(fresh)
        return {
            "profile": _jsonify(fresh),
            "refreshed_fields": refreshed,
            "reset_user_overrides": True,
            "warnings": warnings,
        }

    merged = _merge_profile_from_basic_info(basic_info)
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
