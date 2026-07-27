"""get/set/refresh_user_profile MCP 툴 엔드투엔드 테스트.

SSAINT 세션/네트워크는 mock 처리. 파일 경로는 tmp_path로 격리.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from soongpt_mcp import server
from soongpt_mcp.profile import UserProfile
from soongpt_mcp.schemas.usaint_schemas import BasicInfo


@pytest.fixture
def isolated_profile_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """profile 모듈과 server 모듈 양쪽 경로를 tmp_path로 통일."""
    target = tmp_path / "profile.json"
    monkeypatch.setattr(
        "soongpt_mcp.profile.resolve_profile_path", lambda: target
    )
    return target


@pytest.fixture
def stub_basic_info(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """_fetch_basic_info_via_session이 고정된 (BasicInfo, warnings) 반환하도록 stub."""
    basic = BasicInfo(year=2023, grade=3, semester=5, department="컴퓨터학부")

    async def fake_fetch() -> tuple[BasicInfo, list[str]]:
        return basic, []

    monkeypatch.setattr(server, "_fetch_basic_info_via_session", fake_fetch)
    return {"year": 2023, "grade": 3, "department": "컴퓨터학부"}


@pytest.fixture
def stub_basic_info_with_warnings(
    monkeypatch: pytest.MonkeyPatch,
) -> list[str]:
    """NO_SEMESTER_INFO 경고를 함께 반환하는 stub."""
    basic = BasicInfo(year=2023, grade=1, semester=1, department="컴퓨터학부")
    warnings = ["NO_SEMESTER_INFO"]

    async def fake_fetch() -> tuple[BasicInfo, list[str]]:
        return basic, warnings

    monkeypatch.setattr(server, "_fetch_basic_info_via_session", fake_fetch)
    return warnings


@pytest.mark.asyncio
async def test_get_profile_when_missing(
    isolated_profile_path: Path,
) -> None:
    result = await server.get_user_profile()
    assert result["profile"] is None
    assert "refresh_user_profile" in result["guidance"]


@pytest.mark.asyncio
async def test_get_profile_returns_saved(
    isolated_profile_path: Path,
) -> None:
    saved = UserProfile(
        student_id="20240001",
        name="홍길동",
        college="IT대학",
        department="컴퓨터학부",
        grade=2,
        entered_year=2024,
    )
    server.save_profile(saved)

    result = await server.get_user_profile()
    payload = result["profile"]
    assert payload["student_id"] == "20240001"
    assert payload["name"] == "홍길동"
    assert payload["department"] == "컴퓨터학부"
    assert payload["grade"] == 2


@pytest.mark.asyncio
async def test_set_profile_creates_new_profile(
    isolated_profile_path: Path,
) -> None:
    result = await server.set_user_profile("student_id", "20240001")
    assert result["profile"]["student_id"] == "20240001"

    reloaded = await server.get_user_profile()
    assert reloaded["profile"]["student_id"] == "20240001"


@pytest.mark.asyncio
async def test_set_profile_preserves_other_fields(
    isolated_profile_path: Path,
) -> None:
    await server.set_user_profile("name", "홍길동")
    await server.set_user_profile("grade", 3)

    result = await server.get_user_profile()
    profile = result["profile"]
    assert profile["name"] == "홍길동"
    assert profile["grade"] == 3


@pytest.mark.asyncio
async def test_set_profile_unknown_field_raises(
    isolated_profile_path: Path,
) -> None:
    with pytest.raises(ValueError, match="알 수 없는 프로필 필드"):
        await server.set_user_profile("unknown_field", "x")


@pytest.mark.asyncio
async def test_set_profile_invalid_grade_raises(
    isolated_profile_path: Path,
) -> None:
    with pytest.raises(ValidationError):
        await server.set_user_profile("grade", 99)


@pytest.mark.asyncio
async def test_set_profile_coerces_numeric_string_grade(
    isolated_profile_path: Path,
) -> None:
    """LLM이 grade를 "3" 문자열로 줘도 정수 3으로 저장."""
    result = await server.set_user_profile("grade", "3")
    assert result["profile"]["grade"] == 3


@pytest.mark.asyncio
async def test_set_profile_coerces_numeric_string_entered_year(
    isolated_profile_path: Path,
) -> None:
    result = await server.set_user_profile("entered_year", "2024")
    assert result["profile"]["entered_year"] == 2024


@pytest.mark.asyncio
async def test_set_profile_empty_string_clears_field(
    isolated_profile_path: Path,
) -> None:
    await server.set_user_profile("name", "홍길동")
    await server.set_user_profile("name", "  ")

    result = await server.get_user_profile()
    assert result["profile"]["name"] is None


@pytest.mark.asyncio
async def test_refresh_preserves_user_overrides(
    isolated_profile_path: Path,
    stub_basic_info: dict[str, Any],
) -> None:
    existing = UserProfile(
        student_id="20240001",
        name="사용자가 입력한 이름",
        college="사용자가 입력한 단과대",
        track="사용자가 입력한 트랙",
        department="오래된 학과",
        grade=1,
        entered_year=2020,
    )
    server.save_profile(existing)

    result = await server.refresh_user_profile(preserve_user_overrides=True)
    profile = result["profile"]

    assert profile["student_id"] == "20240001"
    assert profile["name"] == "사용자가 입력한 이름"
    assert profile["college"] == "사용자가 입력한 단과대"
    assert profile["track"] == "사용자가 입력한 트랙"

    assert profile["department"] == "컴퓨터학부"
    assert profile["grade"] == 3
    assert profile["entered_year"] == 2023

    assert sorted(result["refreshed_fields"]) == [
        "department",
        "entered_year",
        "grade",
    ]
    assert result["warnings"] == []


@pytest.mark.asyncio
async def test_refresh_without_preserve_discards_user_fields(
    isolated_profile_path: Path,
    stub_basic_info: dict[str, Any],
) -> None:
    existing = UserProfile(
        student_id="20240001",
        name="버려질 이름",
        department="버려질 학과",
    )
    server.save_profile(existing)

    result = await server.refresh_user_profile(preserve_user_overrides=False)
    profile = result["profile"]

    assert profile["student_id"] is None
    assert profile["name"] is None
    assert profile["department"] == "컴퓨터학부"
    assert profile["grade"] == 3
    assert profile["entered_year"] == 2023
    assert result["reset_user_overrides"] is True


@pytest.mark.asyncio
async def test_refresh_with_preserve_marks_no_reset(
    isolated_profile_path: Path,
    stub_basic_info: dict[str, Any],
) -> None:
    server.save_profile(UserProfile(student_id="20240001"))
    result = await server.refresh_user_profile(preserve_user_overrides=True)
    assert result["reset_user_overrides"] is False
    assert result["profile"]["student_id"] == "20240001"


@pytest.mark.asyncio
async def test_refresh_preserve_on_empty_profile(
    isolated_profile_path: Path,
    stub_basic_info: dict[str, Any],
) -> None:
    result = await server.refresh_user_profile(preserve_user_overrides=True)
    profile = result["profile"]

    assert profile["department"] == "컴퓨터학부"
    assert profile["grade"] == 3
    assert profile["entered_year"] == 2023
    assert profile["student_id"] is None


@pytest.mark.asyncio
async def test_refresh_updates_updated_at(
    isolated_profile_path: Path,
    stub_basic_info: dict[str, Any],
) -> None:
    old_timestamp = datetime(2020, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    existing = UserProfile(student_id="20240001", updated_at=old_timestamp)
    server.save_profile(existing)

    before = datetime.now(timezone.utc)
    result = await server.refresh_user_profile(preserve_user_overrides=True)
    after = datetime.now(timezone.utc)

    updated_at_str = result["profile"]["updated_at"]
    parsed = datetime.fromisoformat(updated_at_str)
    assert before <= parsed <= after


@pytest.mark.asyncio
async def test_refresh_propagates_warnings(
    isolated_profile_path: Path,
    stub_basic_info_with_warnings: list[str],
) -> None:
    """SSAINT에서 warnings(NO_SEMESTER_INFO 등)가 오면 응답에 전달."""
    result = await server.refresh_user_profile(preserve_user_overrides=True)
    assert result["warnings"] == stub_basic_info_with_warnings
