"""SPR-112 전용 도구 테스트.

옵션 조합을 시그니처에 고정한 전용 도구 2종이 기존 parse_lectures_cache의 해당
경로와 동일하게 동작하는지(등가) + 고정된 조합(교선+entered_year / codes+
subject_groups 제외)이 실제로 적용되는지 검증한다.

- list_optional_elective_candidates(year, semester, entered_year, offset=0)
  = parse_lectures_cache(category_prefixes=["교선"], include_subject_groups=False,
    entered_year=...) 컴팩트 경로
- get_lecture_details(year, semester, codes=[...])
  = parse_lectures_cache(codes=[...], include_subject_groups=False) 경로

CLAUDE_PLUGIN_DATA는 conftest의 전역 autouse 픽스처가 임시 디렉토리로 격리한다.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from soongpt_mcp import lectures_cache as cache_mod
from soongpt_mcp import server
from soongpt_mcp.lectures_cache import LectureGroupEntry, LecturesCache


def _build_gyoseon_cache() -> LecturesCache:
    """교선 학번 필터 테스트용 — 전 학번 태그 + 신학번 전용 + 구학번 전용."""
    return LecturesCache(
        year=2026,
        semester="1",
        groups={
            "optional_elective_all": LectureGroupEntry(
                category_type="optional_elective",
                params={"category": "전체"},
                lectures=[
                    {
                        "code": "3161011001",
                        "name": "한류와대중문화",
                        "category": "교선",
                        "time_points": "3/3",
                        "field": (
                            "['23이후]문화·예술\n"
                            "['20,'21~'22]의사소통/글로벌\n"
                            "['19]기초역량\n"
                            "['16-'18]기초역량\n"
                            "['15이전]창의"
                        ),
                        "schedule_room": "목 13:00-14:15 (진리 A-김선생)",
                    },
                    {
                        "code": "3161011002",
                        "name": "신학번전용",
                        "category": "교선",
                        "time_points": "2/2",
                        "field": "['23이후]과학·기술",
                        "schedule_room": "화 09:00-10:15 (베어드홀 B-박선생)",
                    },
                    {
                        "code": "3161011003",
                        "name": "구학번전용",
                        "category": "교선",
                        "time_points": "3/3",
                        "field": "['16-'18]기초역량\n['15이전]창의",
                        "schedule_room": "월 14:00-15:15 (숭덕 02108-이선생)",
                    },
                ],
                count=3,
                error=None,
            ),
        },
        cached_at=datetime.now(timezone.utc),
    )


def _build_gyoseon_many_cache(count: int) -> LecturesCache:
    """교선 학번 필터 페이지네이션 테스트용 — 전부 2020학번 매칭 태그 강의 N개."""
    return LecturesCache(
        year=2026,
        semester="1",
        groups={
            "optional_elective_all": LectureGroupEntry(
                category_type="optional_elective",
                params={"category": "전체"},
                lectures=[
                    {
                        "code": f"3161{i:06d}",
                        "name": f"교양선택과목{i}",
                        "category": "교선",
                        "time_points": "3/3",
                        "field": "['20,'21~'22]의사소통/글로벌",
                        "schedule_room": "월 14:00-15:15 (숭덕 02108-이선생)",
                    }
                    for i in range(count)
                ],
                count=count,
                error=None,
            ),
        },
        cached_at=datetime.now(timezone.utc),
    )


# ── list_optional_elective_candidates ─────────────────────────────────────


@pytest.mark.asyncio
async def test_list_optional_elective_candidates_fixed_combination() -> None:
    """교선 컴팩트 + entered_year 필터 + subject_groups 제외가 자동 적용된다."""
    cache_mod.save_lectures_cache(_build_gyoseon_cache())

    resp = await server.list_optional_elective_candidates(2026, "1", entered_year=2023)

    items = resp["parsed"]
    assert all(set(p) == {"code", "name", "credits", "field_tags"} for p in items)
    codes = [p["code"] for p in items]
    assert codes == ["3161011001", "3161011002"]  # 구학번 전용 제외
    assert resp["parsed_count"] == 2
    assert resp["total_matched"] == 2
    assert resp["truncated"] is False
    # 고정 조합 echo — category_prefixes=["교선"] + entered_year + 기본 상한 150
    assert resp["filters"]["category_prefixes"] == ["교선"]
    assert resp["filters"]["entered_year"] == 2023
    assert resp["filters"]["limit"] == 150
    # subject_groups 제외 고정 — 인덱스 키 없음
    assert "subject_groups" not in resp
    assert resp["stats"]["total"] == 3  # stats는 전체 기준 (하위 호환과 동일)


@pytest.mark.asyncio
async def test_list_optional_elective_candidates_matches_parse() -> None:
    """전용 도구 = parse_lectures_cache의 교선 컴팩트 경로와 등가 (SPR-112)."""
    cache_mod.save_lectures_cache(_build_gyoseon_cache())

    dedicated = await server.list_optional_elective_candidates(2026, "1", entered_year=2023)
    general = await server.parse_lectures_cache(
        2026, "1", category_prefixes=["교선"], include_subject_groups=False,
        entered_year=2023,
    )

    assert dedicated["parsed"] == general["parsed"]
    assert dedicated["parsed_count"] == general["parsed_count"]
    assert dedicated["total_matched"] == general["total_matched"]
    assert dedicated["truncated"] == general["truncated"]
    assert dedicated["stats"] == general["stats"]
    assert "subject_groups" not in dedicated
    assert "subject_groups" not in general


@pytest.mark.asyncio
async def test_list_optional_elective_candidates_pagination_offset() -> None:
    """상한 150 초과 시 truncated=true — offset으로 이어받는다 (SPR-103/112)."""
    cache_mod.save_lectures_cache(_build_gyoseon_many_cache(200))

    resp1 = await server.list_optional_elective_candidates(2026, "1", entered_year=2020)
    assert resp1["parsed_count"] == 150
    assert resp1["total_matched"] == 200
    assert resp1["truncated"] is True
    assert len(json.dumps(resp1, ensure_ascii=False)) < 50_000  # 스필 방지
    first = [p["code"] for p in resp1["parsed"]]

    resp2 = await server.list_optional_elective_candidates(
        2026, "1", entered_year=2020, offset=150
    )
    second = [p["code"] for p in resp2["parsed"]]
    assert resp2["parsed_count"] == 50
    assert resp2["truncated"] is False
    assert resp2["filters"]["offset"] == 150
    assert len(set(first) & set(second)) == 0
    assert len(first) + len(second) == 200


@pytest.mark.asyncio
async def test_list_optional_elective_candidates_non_gyoseon_excluded() -> None:
    """category_prefixes=["교선"] 고정 — 교선이 아닌 강의는 자동 제외."""
    cache = _build_gyoseon_cache()
    cache.groups["major_IT대학_컴퓨터학부"] = LectureGroupEntry(
        category_type="major",
        params={"collage": "IT대학", "department": "컴퓨터학부"},
        lectures=[
            {
                "code": "2150164203",
                "name": "전기기초",
                "category": "전기-컴퓨터학부",
                "field": "[4차]",
                "schedule_room": "월 10:30-12:00 (베어드홀 01101-김자헌)",
            },
        ],
        count=1,
        error=None,
    )
    cache_mod.save_lectures_cache(cache)

    resp = await server.list_optional_elective_candidates(2026, "1", entered_year=2023)

    codes = [p["code"] for p in resp["parsed"]]
    assert "2150164203" not in codes
    assert set(codes) == {"3161011001", "3161011002"}


@pytest.mark.asyncio
async def test_list_optional_elective_candidates_miss() -> None:
    """miss 경로 — parsed 비움 + total_matched 0/truncated False."""
    resp = await server.list_optional_elective_candidates(2026, "2", entered_year=2023)

    assert resp["_cache"]["source"] == "miss"
    assert resp["parsed"] == []
    assert resp["parsed_count"] == 0
    assert resp["total_matched"] == 0
    assert resp["truncated"] is False
    assert "subject_groups" not in resp
    assert resp["filters"]["category_prefixes"] == ["교선"]


# ── get_lecture_details ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_lecture_details_codes_full_detail() -> None:
    """codes 지정 → 해당 강의의 full parsed(슬롯 포함)만, subject_groups 제외."""
    cache_mod.save_lectures_cache(_build_gyoseon_cache())

    resp = await server.get_lecture_details(2026, "1", codes=["3161011001"])

    assert resp["parsed_count"] == 1
    item = resp["parsed"][0]
    assert item["code"] == "3161011001"
    assert "slots" in item  # full 상세 — 충돌 검사 가능
    assert item["slots"][0]["days"] == ["목"]
    assert "field_tags" in item
    assert "subject_groups" not in resp
    assert resp["filters"]["codes"] == ["3161011001"]
    assert resp["filters"]["category_prefixes"] is None


@pytest.mark.asyncio
async def test_get_lecture_details_matches_parse() -> None:
    """전용 도구 = parse_lectures_cache(codes, include_subject_groups=False)와 등가."""
    cache_mod.save_lectures_cache(_build_gyoseon_cache())

    dedicated = await server.get_lecture_details(2026, "1", codes=["3161011001", "3161011003"])
    general = await server.parse_lectures_cache(
        2026, "1", codes=["3161011001", "3161011003"], include_subject_groups=False
    )

    assert dedicated["parsed"] == general["parsed"]
    assert dedicated["parsed_count"] == general["parsed_count"]
    assert "subject_groups" not in dedicated
    assert "subject_groups" not in general


@pytest.mark.asyncio
async def test_get_lecture_details_unmatched_codes_excluded() -> None:
    """캐시에 없는 code는 parsed에서 제외 — parsed_count로 확인."""
    cache_mod.save_lectures_cache(_build_gyoseon_cache())

    resp = await server.get_lecture_details(
        2026, "1", codes=["3161011001", "9999999999"]
    )

    codes = [p["code"] for p in resp["parsed"]]
    assert codes == ["3161011001"]
    assert resp["parsed_count"] == 1
    assert resp["filters"]["codes"] == ["3161011001", "9999999999"]


@pytest.mark.asyncio
async def test_get_lecture_details_empty_codes_rejected() -> None:
    """codes 빈 리스트 → ValueError (전체 parsed 반환 foot-gun 방지, SPR-112).

    parse_lectures_cache의 빈 codes는 '필터 없음'으로 해석돼 전체 parsed(~300KB)를
    반환하지만, 전용 도구는 그 오용을 막기 위해 명시적으로 거부한다.
    """
    cache_mod.save_lectures_cache(_build_gyoseon_cache())

    with pytest.raises(ValueError):
        await server.get_lecture_details(2026, "1", codes=[])


@pytest.mark.asyncio
async def test_get_lecture_details_miss() -> None:
    """miss 경로 — parsed 비움, subject_groups 없음."""
    resp = await server.get_lecture_details(2026, "2", codes=["3161011001"])

    assert resp["_cache"]["source"] == "miss"
    assert resp["parsed"] == []
    assert resp["parsed_count"] == 0
    assert "subject_groups" not in resp
    assert resp["filters"]["codes"] == ["3161011001"]
