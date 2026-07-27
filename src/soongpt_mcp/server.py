"""FastMCP server exposing the Soongsil uSaint snapshot tool."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from mcp.server.fastmcp import FastMCP

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


if __name__ == "__main__":
    run()
