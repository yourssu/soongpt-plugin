"""load_lectures_cache 도구 응답(count/total_lectures 분리) 통합 테스트.

- count = 그룹 수 (len(groups), 다른 도구 관례와 동일)
- total_lectures = 전 그룹 count 합 (SPR-78) — 요약 표시는 이 필드 사용
캐시 파일은 직접 쓸 수 있으므로 세션 스텁이 필요 없다.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from soongpt_mcp import server
from soongpt_mcp.lectures_cache import (
    LectureGroupEntry,
    LecturesCache,
    save_lectures_cache,
)


def _sample_cache(year: int = 2026, semester: str = "1") -> LecturesCache:
    def _entry(code: str, count: int) -> LectureGroupEntry:
        return LectureGroupEntry(
            category_type="major",
            params={"collage": "IT대학", "department": "컴퓨터학부"},
            lectures=[{"code": code} for _ in range(count)],
            count=count,
            error=None,
        )

    return LecturesCache(
        year=year,
        semester=semester,
        groups={
            "major_IT대학_컴퓨터학부": _entry("CS101", 45),
            "optional_elective_all": _entry("ELEC001", 337),
            "chapel": LectureGroupEntry(
                category_type="chapel",
                params={"lecture_name": "비전채플"},
                lectures=[],
                count=0,
                error="일시적 오류",
            ),
        },
        cached_at=datetime.now(timezone.utc),
    )


@pytest.mark.asyncio
async def test_miss_returns_zero_for_both_counts() -> None:
    """파일 없음: count와 total_lectures 모두 0."""
    result = await server.load_lectures_cache(2026, "1")
    assert result["groups"] == {}
    assert result["count"] == 0
    assert result["total_lectures"] == 0
    assert result["_cache"]["source"] == "miss"


@pytest.mark.asyncio
async def test_count_is_group_count_and_total_lectures_sums() -> None:
    """count = 그룹 수, total_lectures = 성공 그룹 count 합 (error 그룹 제외)."""
    save_lectures_cache(_sample_cache())
    result = await server.load_lectures_cache(2026, "1")
    assert result["_cache"]["source"] == "cache"
    assert result["count"] == 3
    assert result["total_lectures"] == 45 + 337
