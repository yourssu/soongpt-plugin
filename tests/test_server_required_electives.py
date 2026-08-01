"""list_required_electives MCP 도구 테스트 (SPR-51).

RusaintService.find_required_electives + _run_with_session을 스텁해
도구가 (year, semester)를 그대로 전달하고 응답을 JSON 직렬화해 반환하는지
검증한다. USAINT fetch는 실제 호출하지 않는다.
"""
from __future__ import annotations

from typing import Any

import pytest

from soongpt_mcp import server


def _patch_service_and_session(
    monkeypatch: pytest.MonkeyPatch,
    payload: dict[str, Any],
) -> None:
    """RusaintService.find_required_electives + _run_with_session 스텁.

    fake_fetch는 bound method로 쓰이므로 self 인자 필요.
    """
    from soongpt_mcp.services.rusaint_service import RusaintService

    async def fake_fetch(
        self: Any, _session_json: str, year: int, semester: str
    ) -> dict[str, Any]:
        return {"...session_json": _session_json, "year": year, "semester": semester, **payload}

    monkeypatch.setattr(
        RusaintService, "find_required_electives", fake_fetch
    )

    async def fake_run(func: Any) -> Any:
        return await func("dummy-session")

    monkeypatch.setattr(server, "_run_with_session", fake_run)


@pytest.mark.asyncio
async def test_list_required_electives_returns_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """과목명 목록이 그대로 응답되고, 인자가 파사드로 전달됨."""
    payload = {"lecture_names": ["[SW와AI]AI개발과실전", "한반도평화와통일"], "count": 2}
    _patch_service_and_session(monkeypatch, payload)

    result = await server.list_required_electives(2026, "2")

    assert result["lecture_names"] == ["[SW와AI]AI개발과실전", "한반도평화와통일"]
    assert result["count"] == 2
    # (year, semester)가 그대로 파사드로 전달됨을 확인
    assert result["year"] == 2026
    assert result["semester"] == "2"
    # _run_with_session이 실제 세션 JSON을 넘겼음을 확인
    assert result["...session_json"] == "dummy-session"


@pytest.mark.asyncio
async def test_list_required_electives_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """빈 목록도 정상 직렬화 (lecture_names=[] + count=0)."""
    payload = {"lecture_names": [], "count": 0}
    _patch_service_and_session(monkeypatch, payload)

    result = await server.list_required_electives(2026, "1")

    assert result["lecture_names"] == []
    assert result["count"] == 0
