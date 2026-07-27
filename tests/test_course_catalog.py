"""course_catalog.fetch_available_lectures 테스트."""
from __future__ import annotations

from typing import Any

import pytest

from soongpt_mcp.services.course_catalog import (
    LectureCategoryRequest,
    fetch_available_lectures,
)


class _FakeService:
    """find_lectures 호출을 녹화하고 카테고리별 응답/예외를 반환."""

    def __init__(self, handler) -> None:
        self._handler = handler
        self.calls: list[dict[str, Any]] = []

    async def find_lectures(
        self,
        session_json: str,
        *,
        year: int,
        semester: str,
        category_type: str,
        include_details: bool = False,
        **params: Any,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "session": session_json,
                "year": year,
                "semester": semester,
                "category_type": category_type,
                "include_details": include_details,
                "params": params,
            }
        )
        return self._handler(category_type, params)


def _lecture(code: str, name: str) -> dict[str, Any]:
    return {"code": code, "name": name}


@pytest.mark.asyncio
async def test_empty_requests_returns_empty_groups() -> None:
    service = _FakeService(lambda ct, p: {"lectures": [], "count": 0})

    result = await fetch_available_lectures(
        "session-json", year=2026, semester="1", requests=[],
        service=service,
    )

    assert result["year"] == 2026
    assert result["semester"] == "1"
    assert result["groups"] == {}
    assert result["totalCount"] == 0
    assert result["requestedCategories"] == []
    assert service.calls == []


@pytest.mark.asyncio
async def test_single_category_groups_response() -> None:
    def handler(category_type, params):
        if category_type == "chapel":
            return {
                "lectures": [_lecture("CH01", "채플")],
                "count": 1,
                "fetchTime": "0.50s",
                "includeDetails": False,
            }
        raise AssertionError(f"unexpected category_type: {category_type}")

    service = _FakeService(handler)
    requests = [
        LectureCategoryRequest(
            category_type="chapel", parameters={"lecture_name": "채플"}
        )
    ]

    result = await fetch_available_lectures(
        "session-json", year=2026, semester="1", requests=requests,
        service=service,
    )

    assert result["year"] == 2026
    assert result["semester"] == "1"
    assert result["totalCount"] == 1
    assert result["requestedCategories"] == ["chapel"]
    assert result["groups"]["chapel"] == {
        "lectures": [_lecture("CH01", "채플")],
        "count": 1,
        "error": None,
    }
    assert service.calls[0]["category_type"] == "chapel"
    assert service.calls[0]["params"] == {"lecture_name": "채플"}
    assert service.calls[0]["session"] == "session-json"
    assert service.calls[0]["include_details"] is False


@pytest.mark.asyncio
async def test_multiple_categories_grouped() -> None:
    def handler(category_type, params):
        if category_type == "chapel":
            return {
                "lectures": [_lecture("CH01", "채플")],
                "count": 1,
            }
        if category_type == "education":
            return {
                "lectures": [_lecture("ED01", "교직")],
                "count": 1,
            }
        raise AssertionError(f"unexpected: {category_type}")

    service = _FakeService(handler)
    requests = [
        LectureCategoryRequest(category_type="chapel", parameters={"lecture_name": "채플"}),
        LectureCategoryRequest(category_type="education", parameters={}),
    ]

    result = await fetch_available_lectures(
        "session-json", year=2026, semester="winter", requests=requests,
        service=service,
    )

    assert set(result["groups"].keys()) == {"chapel", "education"}
    assert result["totalCount"] == 2
    assert result["requestedCategories"] == ["chapel", "education"]
    assert result["groups"]["chapel"]["count"] == 1
    assert result["groups"]["education"]["count"] == 1
    assert result["groups"]["chapel"]["error"] is None
    assert result["groups"]["education"]["error"] is None


@pytest.mark.asyncio
async def test_partial_failure_isolates_error() -> None:
    def handler(category_type, params):
        if category_type == "chapel":
            return {"lectures": [_lecture("CH01", "채플")], "count": 1}
        if category_type == "major":
            raise ValueError(
                "category_type 'major'에 필요한 파라미터 누락: collage, department"
            )
        raise AssertionError(f"unexpected: {category_type}")

    service = _FakeService(handler)
    requests = [
        LectureCategoryRequest(category_type="major", parameters={}),
        LectureCategoryRequest(category_type="chapel", parameters={"lecture_name": "채플"}),
    ]

    result = await fetch_available_lectures(
        "session-json", year=2026, semester="1", requests=requests,
        service=service,
    )

    assert result["totalCount"] == 1
    assert result["groups"]["chapel"]["count"] == 1
    assert result["groups"]["chapel"]["error"] is None
    assert result["groups"]["major"]["count"] == 0
    assert result["groups"]["major"]["lectures"] == []
    assert "ValueError" in result["groups"]["major"]["error"]
    assert "collage, department" in result["groups"]["major"]["error"]


@pytest.mark.asyncio
async def test_include_details_propagated() -> None:
    service = _FakeService(
        lambda ct, p: {"lectures": [], "count": 0, "includeDetails": True}
    )
    requests = [LectureCategoryRequest(category_type="cyber", parameters={})]

    await fetch_available_lectures(
        "session-json", year=2026, semester="summer", requests=requests,
        service=service, include_details=True,
    )

    assert service.calls[0]["include_details"] is True


@pytest.mark.asyncio
async def test_same_category_type_last_wins() -> None:
    """동일 category_type이 여러 요청에 들어오면 마지막 결과로 덮어씀 (뼈대 정책)."""

    def handler(category_type, params):
        size = params.get("size", 0)
        return {"lectures": [_lecture(f"C{size}", f"과목{size}")] * size, "count": size}

    service = _FakeService(handler)
    requests = [
        LectureCategoryRequest(category_type="cyber", parameters={"size": 2}),
        LectureCategoryRequest(category_type="cyber", parameters={"size": 3}),
    ]

    result = await fetch_available_lectures(
        "session-json", year=2026, semester="1", requests=requests,
        service=service,
    )

    assert len(result["groups"]) == 1
    assert result["groups"]["cyber"]["count"] == 3
    assert result["totalCount"] == 3
