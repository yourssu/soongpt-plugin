"""FastMCP server exposing the Soongsil uSaint snapshot tool."""
from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from .auth import load_session
from .services.exceptions import SSOTokenError
from .services.rusaint_service import RusaintService

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


async def _require_session() -> str:
    """공통: 세션 JSON 로드, 없으면 로그인 가이드 에러 발생."""
    session_json = load_session()
    if not session_json:
        raise RuntimeError(
            "로그인이 필요합니다. 터미널에서 'soongpt-mcp-login'을 실행하세요."
        )
    return session_json


@mcp.tool()
async def get_usaint_snapshot() -> dict:
    """숭실대 SSAINT에서 학적/수강/성적 데이터를 가져옵니다 (가공 전 raw).

    반환: basicInfo, takenCourses, lowGradeSubjectCodes, flags, warnings.
    졸업사정표는 별도 도구 get_graduation_status를 사용하세요.

    최초 사용 전 터미널에서 'soongpt-mcp-login'을 실행하여 로그인해야 합니다.
    세션이 만료된 경우에도 재로그인이 필요합니다.
    """
    session_json = await _require_session()
    service = RusaintService()
    try:
        snapshot = await service.fetch_usaint_snapshot(session_json)
    except SSOTokenError as exc:
        raise RuntimeError(
            "세션이 만료되었습니다. 터미널에서 'soongpt-mcp-login'을 다시 실행하세요."
        ) from exc
    if hasattr(snapshot, "model_dump"):
        return snapshot.model_dump(mode="json")
    return snapshot


@mcp.tool()
async def get_graduation_status() -> dict:
    """숭실대 SSAINT에서 졸업사정표 데이터를 가져옵니다 (약 5-6초).

    반환: 개별 졸업 요건 상세(requirements) + 핵심 요약(graduationSummary).
    학적/수강 데이터는 get_usaint_snapshot을 사용하세요.

    최초 사용 전 터미널에서 'soongpt-mcp-login'을 실행하여 로그인해야 합니다.
    세션이 만료된 경우에도 재로그인이 필요합니다.
    """
    session_json = await _require_session()
    service = RusaintService()
    try:
        result = await service.fetch_usaint_graduation_info(session_json)
    except SSOTokenError as exc:
        raise RuntimeError(
            "세션이 만료되었습니다. 터미널에서 'soongpt-mcp-login'을 다시 실행하세요."
        ) from exc
    return _jsonify(result)


def run() -> None:
    """Run the MCP server on stdio (default transport)."""
    mcp.run()


if __name__ == "__main__":
    run()
