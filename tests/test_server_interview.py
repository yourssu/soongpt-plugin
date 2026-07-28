"""get/set/list_interviews MCP 툴 엔드투엔드 테스트.

파일 경로는 tmp_path + CLAUDE_PLUGIN_DATA로 격리. USAINT 세션 사용 안 함.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from soongpt_mcp import server


@pytest.fixture
def isolated_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """interview 모듈 경로를 tmp_path로 격리."""
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path))
    return tmp_path


@pytest.mark.asyncio
async def test_get_interview_when_missing(isolated_root: Path) -> None:
    result = await server.get_interview(2026, "1")
    assert result["interview"] is None
    assert all(v is False for v in result["completion"].values())
    assert "set_interview" in result["guidance"]


@pytest.mark.asyncio
async def test_set_interview_creates_new(isolated_root: Path) -> None:
    result = await server.set_interview(
        2026, "1", "semester_strategy", "15학점 정도 생각 중"
    )
    interview = result["interview"]
    assert interview["year"] == 2026
    assert interview["semester"] == "1"
    assert interview["semester_strategy"] == "15학점 정도 생각 중"
    assert result["completion"]["semester_strategy"] is True
    assert result["completion"]["time_preferences"] is False


@pytest.mark.asyncio
async def test_set_interview_overwrites_previous_text(isolated_root: Path) -> None:
    """같은 섹션 두 번 호출하면 덮어쓰기 (merge 아님)."""
    await server.set_interview(
        2026, "1", "time_preferences", "아침型, 금요일 공강 희망"
    )
    result = await server.set_interview(
        2026, "1", "time_preferences", "오후 위주로 변경"
    )
    assert result["interview"]["time_preferences"] == "오후 위주로 변경"


@pytest.mark.asyncio
async def test_set_interview_preserves_other_sections(
    isolated_root: Path,
) -> None:
    await server.set_interview(
        2026, "1", "semester_strategy", "15학점"
    )
    await server.set_interview(
        2026, "1", "subject_preferences", "전공 비중 높게"
    )
    result = await server.get_interview(2026, "1")
    interview = result["interview"]
    assert interview["semester_strategy"] == "15학점"
    assert interview["subject_preferences"] == "전공 비중 높게"


@pytest.mark.asyncio
async def test_set_interview_unknown_section_raises(
    isolated_root: Path,
) -> None:
    with pytest.raises(ValueError, match="알 수 없는 인터뷰 섹션"):
        await server.set_interview(2026, "1", "unknown", "x")


@pytest.mark.asyncio
async def test_set_interview_invalid_semester_raises(
    isolated_root: Path,
) -> None:
    with pytest.raises(ValueError, match="semester는 '1' 또는 '2'"):
        await server.set_interview(2026, "3", "semester_strategy", "x")


@pytest.mark.asyncio
async def test_list_interviews_empty(isolated_root: Path) -> None:
    result = await server.list_interviews()
    assert result == {"interviews": [], "count": 0}


@pytest.mark.asyncio
async def test_list_interviews_returns_all_periods(isolated_root: Path) -> None:
    await server.set_interview(
        2026, "1", "semester_strategy", "15학점"
    )
    await server.set_interview(
        2025, "2", "time_preferences", "오후 위주"
    )
    result = await server.list_interviews()
    assert result["count"] == 2
    items = result["interviews"]
    # 정렬: 파일명 기준 sorted → 2025_2가 먼저
    assert items[0]["year"] == 2025
    assert items[0]["semester"] == "2"
    assert items[1]["year"] == 2026
    assert items[1]["semester"] == "1"
    # completion 반영
    assert items[0]["completion"]["time_preferences"] is True
    assert items[0]["completion"]["semester_strategy"] is False
    assert items[1]["completion"]["semester_strategy"] is True


@pytest.mark.asyncio
async def test_set_interview_skips_corrupted_existing(
    isolated_root: Path,
) -> None:
    """손상된 기존 파일 무시하고 새 인터뷰로 덮어씀."""
    target = isolated_root / "interview_2026_1.json"
    target.write_text("not json {{{", encoding="utf-8")
    result = await server.set_interview(
        2026, "1", "semester_strategy", "18학점"
    )
    assert result["interview"]["semester_strategy"] == "18학점"


@pytest.mark.asyncio
async def test_set_interview_whitespace_only_not_complete(
    isolated_root: Path,
) -> None:
    """공백만 있는 content는 completion에서 False."""
    result = await server.set_interview(
        2026, "1", "semester_strategy", "   "
    )
    assert result["interview"]["semester_strategy"] == "   "
    assert result["completion"]["semester_strategy"] is False


@pytest.mark.asyncio
async def test_list_interviews_skips_malformed_files(
    isolated_root: Path,
) -> None:
    """interview_*.json 패턴이지만 스키마 위반인 파일은 건너뜀 (best-effort).

    스키마가 허용하는 파일(year/semester 타입만 맞)은 값이 이상해도 포함됨 —
    list_interview_files는 스키마 검증만 하고 의미 검증은 안 함.
    """
    # 정상 파일 1개
    await server.set_interview(
        2026, "1", "semester_strategy", "15학점"
    )
    # glob 패턴은 맞으나 스키마 위반 (year/semester 누락)
    bad = isolated_root / "interview_abc.json"
    bad.write_text('{"semester_strategy": "x"}', encoding="utf-8")
    # JSON 파싱 자체가 안 되는 파일
    corrupt = isolated_root / "interview_corrupt.json"
    corrupt.write_text("not json {{{", encoding="utf-8")

    result = await server.list_interviews()
    assert result["count"] == 1
    only = result["interviews"][0]
    assert only["year"] == 2026
    assert only["semester"] == "1"
