"""FastMCP server exposing the Soongsil uSaint snapshot tool."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

from mcp.server.fastmcp import FastMCP

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
    """숭실대 SSAINT에서 학적/수강/성적 데이터를 가져옵니다 (가공 전 raw).

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
async def get_graduation_status() -> dict:
    """숭실대 SSAINT에서 졸업사정표 데이터를 가져옵니다 (약 5-6초).

    반환: 개별 졸업 요건 상세(requirements) + 핵심 요약(graduationSummary).
    학적/수강 데이터는 get_usaint_snapshot을 사용하세요.

    최초 호출 시 세션이 없으면 자동으로 브라우저가 열려 로그인 폼을 제공합니다.
    세션이 만료된 경우에도 동일하게 자동 재로그인이 진행됩니다.
    """
    service = RusaintService()
    result = await _run_with_session(service.fetch_usaint_graduation_info)
    return _jsonify(result)


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
    """숭실대 SSAINT 강의시간표에서 특정 학기/카테고리 강의를 검색합니다.

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


def run() -> None:
    """Run the MCP server on stdio (default transport)."""
    mcp.run()


# User Profile Tools


async def _fetch_basic_info() -> Any:
    """학적 기본 정보만 가볍게 조회 (1세션, ~2-3초).

    refresh_user_profile이 전체 snapshot(9초) 대신 이 경로를 사용.
    세션 만료 시 자동 재로그인은 _run_with_session에서 처리됨.
    """
    service = RusaintService()
    return await _run_with_session(service.fetch_basic_info)


@mcp.tool()
async def get_user_profile() -> dict:
    """저장된 사용자 프로필 반환.

    프로필이 없으면 profile=None과 함께 안내 메시지를 반환합니다.
    프로필을 처음 만들려면 refresh_user_profile을 호출해 SSAINT에서 초기값을 가져오거나
    set_user_profile로 필드를 직접 입력하세요.
    """
    profile = load_profile()
    if profile is None:
        return {
            "profile": None,
            "guidance": (
                "저장된 프로필이 없습니다. refresh_user_profile을 호출해 SSAINT에서 초기값을 "
                "가져오거나 set_user_profile로 학번/이름 등을 직접 설정하세요."
            ),
        }
    return {"profile": _jsonify(profile)}


@mcp.tool()
async def set_user_profile(field: str, value: Any) -> dict:
    """프로필의 단일 필드를 부분 업데이트 후 저장된 전체 프로필 반환.

    허용 필드: student_id, name, college, department, grade (1~6),
    track, entered_year. grade는 정수, 나머지는 문자열. 빈 문자열/None은 필드를
    None으로 설정합니다. updated_at은 자동 갱신됩니다.

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
    """SSAINT에서 학적 기본 정보를 재추출해 프로필을 갱신 (~2-3초).

    preserve_user_overrides=True(기본)면 SSAINT가 제공하는 3개 필드
    (department, grade, entered_year)를 항상 SSAINT 값으로 덮어쓰고,
    나머지 필드(student_id, name, college, track)는 기존 저장값을 보존합니다.

    False면 기존 프로필을 무시하고 SSAINT 값만으로 새 프로필을 만듭니다
    (비-SSAINT 필드는 모두 None으로 리셋).

    최초 호출 시 세션이 없으면 자동으로 브라우저가 열려 로그인 폼을 제공합니다.
    세션이 만료된 경우에도 동일하게 자동 재로그인이 진행됩니다.
    """
    basic_info = await _fetch_basic_info()
    fresh = UserProfile.from_basic_info(basic_info)
    refreshed = ["department", "grade", "entered_year"]

    if not preserve_user_overrides:
        save_profile(fresh)
        return {
            "profile": _jsonify(fresh),
            "refreshed_fields": refreshed,
            "reset_user_overrides": True,
        }

    existing = load_profile() or UserProfile()
    merged = existing.model_copy(
        update={
            "department": fresh.department,
            "grade": fresh.grade,
            "entered_year": fresh.entered_year,
            "updated_at": datetime.now(timezone.utc),
        }
    )
    save_profile(merged)
    return {
        "profile": _jsonify(merged),
        "refreshed_fields": refreshed,
        "reset_user_overrides": False,
    }


if __name__ == "__main__":
    run()
