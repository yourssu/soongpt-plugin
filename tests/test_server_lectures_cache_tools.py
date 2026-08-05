"""SPR-76 소형 응답 옵션 테스트.

find_lectures(summary=True) / load_lectures_cache(include_lectures=False) /
parse_lectures_cache(summary=True)가 lectures/parsed 상세를 생략하고 메타만
반환하며, 기본값은 기존 동작(하위 호환)을 유지하는지 검증한다.

CLAUDE_PLUGIN_DATA는 conftest의 전역 autouse 픽스처가 임시 디렉토리로 격리한다.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from soongpt_mcp import lectures_cache as cache_mod
from soongpt_mcp import server
from soongpt_mcp.lectures_cache import LectureGroupEntry, LecturesCache


def _build_cache() -> LecturesCache:
    """4개 그룹 샘플 — major 2강의, chapel(소그룹채플) 2강의, 교선 전체 0,
    connected_major(정상 실패) 0+error."""
    return LecturesCache(
        year=2026,
        semester="1",
        groups={
            "major_IT대학_컴퓨터학부": LectureGroupEntry(
                category_type="major",
                params={"collage": "IT대학", "department": "컴퓨터학부"},
                lectures=[
                    {
                        "code": "CS10101",
                        "name": "컴퓨터개론",
                        "schedule_room": "월 10:30-12:00 (베어드홀 01101-김자헌)",
                    },
                    {
                        "code": "CS10201",
                        "name": "자료구조",
                        "schedule_room": "화 11:00-13:00 (숭덕 02108-박은영)",
                    },
                ],
                count=2,
                error=None,
            ),
            "chapel": LectureGroupEntry(
                category_type="chapel",
                params={"lecture_name": "소그룹채플"},
                lectures=[
                    {
                        "code": "2150078501",
                        "name": "소그룹채플",
                        "schedule_room": "수 15:00-16:00 (베어드홀 A)",
                    },
                    {
                        "code": "2150078502",
                        "name": "소그룹채플",
                        "schedule_room": "수 15:00-16:00 (베어드홀 B)",
                    },
                ],
                count=2,
                error=None,
            ),
            "optional_elective_all": LectureGroupEntry(
                category_type="optional_elective",
                params={"category": "전체"},
                lectures=[],
                count=0,
                error=None,
            ),
            "connected_major": LectureGroupEntry(
                category_type="connected_major",
                params={"major": "융합소프트웨어"},
                lectures=[],
                count=0,
                error="Cannot find option",
            ),
        },
        cached_at=datetime.now(timezone.utc),
    )


# ── load_lectures_cache ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_load_lectures_cache_default_includes_lectures() -> None:
    """기본(include_lectures=True)은 기존 동작 — lectures 상세 유지 (하위 호환)."""
    cache_mod.save_lectures_cache(_build_cache())

    resp = await server.load_lectures_cache(2026, "1")

    assert resp["include_lectures"] is True
    assert resp["count"] == 4
    group = resp["groups"]["major_IT대학_컴퓨터학부"]
    assert group["lectures"][0]["code"] == "CS10101"
    assert resp["_cache"]["source"] == "cache"


@pytest.mark.asyncio
async def test_load_lectures_cache_meta_mode_omits_lectures() -> None:
    """include_lectures=False → lectures 제거, codes·count·error·params 보존."""
    cache_mod.save_lectures_cache(_build_cache())

    resp = await server.load_lectures_cache(2026, "1", include_lectures=False)

    assert resp["include_lectures"] is False
    assert resp["count"] == 4
    group = resp["groups"]["major_IT대학_컴퓨터학부"]
    assert "lectures" not in group
    assert group["codes"] == ["CS10101", "CS10201"]
    assert group["count"] == 2
    assert group["category_type"] == "major"
    assert group["params"]["department"] == "컴퓨터학부"
    assert group["error"] is None
    # chapel code 집합 보존 — composer 채플 식별(groups[chapel] code) 대비
    assert resp["groups"]["chapel"]["codes"] == ["2150078501", "2150078502"]
    # error 그룹은 메타에도 error 필드가 남는다
    assert resp["groups"]["connected_major"]["error"] == "Cannot find option"
    assert resp["groups"]["connected_major"]["codes"] == []
    # 빈 그룹도 메타 정상 구성
    assert resp["groups"]["optional_elective_all"]["count"] == 0
    assert resp["_cache"]["source"] == "cache"


@pytest.mark.asyncio
async def test_load_lectures_cache_miss_meta_mode() -> None:
    """miss 경로도 include_lectures 플래그를 그대로 반환."""
    resp = await server.load_lectures_cache(2026, "2", include_lectures=False)

    assert resp["_cache"]["source"] == "miss"
    assert resp["groups"] == {}
    assert resp["count"] == 0
    assert resp["include_lectures"] is False


# ── find_lectures summary ──────────────────────────────────────────────────

_FAKE_RESULT = {
    "lectures": [
        {
            "code": "CS10101",
            "name": "컴퓨터개론",
            "schedule_room": "월 10:30-12:00 (베어드홀 01101-김자헌)",
        }
    ],
    "count": 1,
    "fetchTime": "0.10s",
    "includeDetails": False,
}


async def _fake_success_run(func: Any) -> dict[str, Any]:
    return dict(_FAKE_RESULT)


@pytest.mark.asyncio
async def test_find_lectures_summary_omits_lectures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """summary=True → lectures 생략, count/_cache만. 그래도 캐시엔 상세 저장."""
    monkeypatch.setattr(server, "_run_with_session", _fake_success_run)

    resp = await server.find_lectures(
        2026,
        "1",
        "major",
        collage="IT대학",
        department="컴퓨터학부",
        summary=True,
    )

    assert "lectures" not in resp
    assert resp["summary"] is True
    assert resp["count"] == 1
    assert resp["fetchTime"] == "0.10s"
    assert resp["_cache"]["group_key"] == "major_IT대학_컴퓨터학부"
    assert resp["_cache"]["saved"] is True
    # summary여도 캐시에는 lectures 전체가 저장된다 (fetch 시점 = 저장 시점)
    cache, _ = cache_mod.load_lectures_cache(2026, "1")
    assert cache is not None
    assert cache.groups["major_IT대학_컴퓨터학부"].lectures == _FAKE_RESULT["lectures"]


@pytest.mark.asyncio
async def test_find_lectures_default_includes_lectures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """기본(summary=False)은 기존 동작 — lectures 상세 유지 (하위 호환)."""
    monkeypatch.setattr(server, "_run_with_session", _fake_success_run)

    resp = await server.find_lectures(
        2026, "1", "major", collage="IT대학", department="컴퓨터학부"
    )

    assert "lectures" in resp
    assert resp["lectures"] == _FAKE_RESULT["lectures"]
    assert resp["count"] == 1
    assert "summary" not in resp


@pytest.mark.asyncio
async def test_find_lectures_summary_error_still_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """summary 모드에서도 fetch 실패는 예외 재전파 + error 그룹 기록 (SPR-75)."""
    from soongpt_mcp.services.exceptions import RusaintInternalError

    async def _fake_raise_run(func: Any) -> dict[str, Any]:
        raise RusaintInternalError("유세인트 강의시간표 조회 중 오류")

    monkeypatch.setattr(server, "_run_with_session", _fake_raise_run)

    with pytest.raises(RusaintInternalError):
        await server.find_lectures(
            2026,
            "1",
            "major",
            collage="IT대학",
            department="컴퓨터학부",
            summary=True,
        )

    cache, _ = cache_mod.load_lectures_cache(2026, "1")
    assert cache is not None
    assert cache.groups["major_IT대학_컴퓨터학부"].error is not None


# ── parse_lectures_cache summary ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_parse_lectures_cache_summary_omits_parsed() -> None:
    """summary=True → parsed 생략, parsed_count + subject_groups/stats만."""
    cache_mod.save_lectures_cache(_build_cache())

    resp = await server.parse_lectures_cache(2026, "1", summary=True)

    assert resp["summary"] is True
    assert resp["parsed"] == []
    assert resp["parsed_count"] == 4  # major 2 + chapel 2 (빈 그룹 제외)
    assert resp["stats"]["total"] == 4
    assert resp["stats"]["parsed_ok"] == 4
    assert resp["subject_groups"]["21500785"] == ["2150078501", "2150078502"]
    assert resp["_cache"]["source"] == "cache"


@pytest.mark.asyncio
async def test_parse_lectures_cache_default_includes_parsed() -> None:
    """기본(summary=False)은 기존 동작 — parsed 상세 유지 (하위 호환)."""
    cache_mod.save_lectures_cache(_build_cache())

    resp = await server.parse_lectures_cache(2026, "1")

    assert len(resp["parsed"]) == 4
    assert resp["parsed"][0]["code"] == "CS10101"
    assert resp["stats"]["total"] == 4
    assert resp["summary"] is False


@pytest.mark.asyncio
async def test_parse_lectures_cache_miss_summary() -> None:
    """miss 경로도 summary 형태로 반환."""
    resp = await server.parse_lectures_cache(2026, "2", summary=True)

    assert resp["_cache"]["source"] == "miss"
    assert resp["parsed"] == []
    assert resp["parsed_count"] == 0
    assert resp["summary"] is True
