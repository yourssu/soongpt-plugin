"""get_available_lectures MCP 툴 테스트."""
from __future__ import annotations

from typing import Any

import pytest

from soongpt_mcp import server
from soongpt_mcp.profile import UserProfile
from soongpt_mcp.services.course_catalog import LectureCategoryRequest


class _FakeManager:
    def __init__(self) -> None:
        self.calls = 0
        self.invalidated = 0

    async def get_valid_session(self, *, force_relogin: bool = False) -> str:
        self.calls += 1
        return "session-json"

    def invalidate(self) -> None:
        self.invalidated += 1


@pytest.mark.asyncio
async def test_no_profile_raises_with_guidance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "soongpt_mcp.server.load_profile", lambda: None
    )

    with pytest.raises(RuntimeError, match="set_user_profile 또는 refresh_user_profile"):
        await server.get_available_lectures(year=2026, semester="1")


@pytest.mark.asyncio
async def test_empty_category_requests_returns_empty_groups(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "soongpt_mcp.server.load_profile",
        lambda: UserProfile(department="컴퓨터학부", grade=3, entered_year=2023),
    )
    # server.py는 이미 import한 build_category_requests 심볼을 사용하므로
    # server 모듈의 참조만 패치하면 됨.
    monkeypatch.setattr(
        "soongpt_mcp.server.build_category_requests",
        lambda profile: [],
    )

    result = await server.get_available_lectures(year=2026, semester="1")

    assert result == {
        "year": 2026,
        "semester": "1",
        "groups": {},
        "totalCount": 0,
        "fetchTime": "0.00s",
        "requestedCategories": [],
    }


@pytest.mark.asyncio
async def test_end_to_end_with_mock_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """profile + 요청 1개 → _run_with_session → service.get_available_lectures 흐름 검증."""
    profile = UserProfile(department="컴퓨터학부", grade=3, entered_year=2023)
    requests = [
        LectureCategoryRequest(
            category_type="chapel", parameters={"lecture_name": "채플"}
        )
    ]
    monkeypatch.setattr("soongpt_mcp.server.load_profile", lambda: profile)
    monkeypatch.setattr(
        "soongpt_mcp.server.build_category_requests", lambda p: requests
    )

    mgr = _FakeManager()
    monkeypatch.setattr("soongpt_mcp.server.get_session_manager", lambda: mgr)

    captured: dict[str, Any] = {}

    class _FakeRusaintService:
        async def get_available_lectures(
            self, session_json, *, year, semester, requests, include_details=False
        ):
            captured["session"] = session_json
            captured["year"] = year
            captured["semester"] = semester
            captured["requests"] = requests
            return {
                "year": year,
                "semester": semester,
                "groups": {
                    "chapel": {
                        "lectures": [{"code": "CH01", "name": "채플"}],
                        "count": 1,
                        "error": None,
                    }
                },
                "totalCount": 1,
                "fetchTime": "0.50s",
                "requestedCategories": ["chapel"],
            }

    monkeypatch.setattr(
        "soongpt_mcp.server.RusaintService", lambda: _FakeRusaintService()
    )

    result = await server.get_available_lectures(year=2026, semester="1")

    assert captured["session"] == "session-json"
    assert captured["year"] == 2026
    assert captured["semester"] == "1"
    assert len(captured["requests"]) == 1
    assert result["totalCount"] == 1
    assert result["groups"]["chapel"]["count"] == 1
    assert result["requestedCategories"] == ["chapel"]
    assert mgr.calls == 1
    assert mgr.invalidated == 0
