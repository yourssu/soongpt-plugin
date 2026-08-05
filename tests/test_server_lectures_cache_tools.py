"""SPR-76 소형 응답 옵션 테스트.

find_lectures(summary=True) / load_lectures_cache(include_lectures=False) /
parse_lectures_cache(summary=True)가 lectures/parsed 상세를 생략하고 메타만
반환하며, 기본값은 기존 동작(하위 호환)을 유지하는지 검증한다.

CLAUDE_PLUGIN_DATA는 conftest의 전역 autouse 픽스처가 임시 디렉토리로 격리한다.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
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


# ── load_lectures_cache codes 필터 (SPR-88) ───────────────────────────────


@pytest.mark.asyncio
async def test_load_lectures_cache_codes_returns_only_matching() -> None:
    """codes 지정 → 매칭 강의 dict만 flat lectures로, groups는 메타 축소."""
    cache_mod.save_lectures_cache(_build_cache())

    resp = await server.load_lectures_cache(
        2026, "1", codes=["CS10101", "2150078502"]
    )

    assert resp["include_lectures"] is True
    assert [lect["code"] for lect in resp["lectures"]] == ["CS10101", "2150078502"]
    # lecture dict는 원본(find_lectures 형식) 그대로 — schedule_room 보존
    assert resp["lectures"][0]["schedule_room"].startswith("월")
    assert resp["matched_count"] == 2
    assert resp["unmatched_codes"] == []
    assert resp["codes"] == ["CS10101", "2150078502"]
    # groups는 메타로 축소 — lectures 상세 없음 (병목 해결 핵심)
    assert "lectures" not in resp["groups"]["major_IT대학_컴퓨터학부"]
    assert resp["groups"]["major_IT대학_컴퓨터학부"]["codes"] == [
        "CS10101",
        "CS10201",
    ]
    assert resp["count"] == 4  # 그룹 수 관례 유지
    assert resp["total_lectures"] == 4
    assert resp["_cache"]["source"] == "cache"


@pytest.mark.asyncio
async def test_load_lectures_cache_codes_cross_group_order() -> None:
    """코드가 여러 그룹에 걸쳐 있어도 캐시 순서대로 flat 수집."""
    cache_mod.save_lectures_cache(_build_cache())

    resp = await server.load_lectures_cache(
        2026, "1", codes=["2150078501", "CS10201"]
    )

    # 캐시 그룹 순서(major → chapel)대로 매칭
    assert [lect["code"] for lect in resp["lectures"]] == ["CS10201", "2150078501"]


@pytest.mark.asyncio
async def test_load_lectures_cache_codes_unmatched_reported() -> None:
    """캐시에 없는 code는 unmatched_codes로 보고 (응답은 성공 유지)."""
    cache_mod.save_lectures_cache(_build_cache())

    resp = await server.load_lectures_cache(2026, "1", codes=["CS10101", "XXXX"])

    assert [lect["code"] for lect in resp["lectures"]] == ["CS10101"]
    assert resp["unmatched_codes"] == ["XXXX"]
    assert resp["matched_count"] == 1


@pytest.mark.asyncio
async def test_load_lectures_cache_codes_dedups_cross_group() -> None:
    """같은 code가 여러 그룹에 있어도 최초 항목만 유지 (renderer dedup 일치)."""
    cache = _build_cache()
    cache.groups["chapel"].lectures.append(
        {"code": "CS10101", "name": "중복사본", "schedule_room": "금 09:00-10:00"}
    )
    cache_mod.save_lectures_cache(cache)

    resp = await server.load_lectures_cache(2026, "1", codes=["CS10101"])

    assert len(resp["lectures"]) == 1
    assert resp["lectures"][0]["name"] == "컴퓨터개론"  # major 그룹 최초 항목 유지
    assert resp["matched_count"] == 1
    assert resp["unmatched_codes"] == []


@pytest.mark.asyncio
async def test_load_lectures_cache_codes_empty() -> None:
    """codes=[] → lectures 비움 (유효 입력)."""
    cache_mod.save_lectures_cache(_build_cache())

    resp = await server.load_lectures_cache(2026, "1", codes=[])

    assert resp["lectures"] == []
    assert resp["matched_count"] == 0
    assert resp["unmatched_codes"] == []


@pytest.mark.asyncio
async def test_load_lectures_cache_codes_miss_shape() -> None:
    """miss + codes → 빈 lectures + 전체 codes를 unmatched로 (일관된 형태)."""
    resp = await server.load_lectures_cache(2026, "2", codes=["CS10101"])

    assert resp["_cache"]["source"] == "miss"
    assert resp["lectures"] == []
    assert resp["codes"] == ["CS10101"]
    assert resp["include_lectures"] is True  # codes 모드는 상세 반환 의미 (SPR-88)
    assert resp["matched_count"] == 0
    assert resp["unmatched_codes"] == ["CS10101"]


@pytest.mark.asyncio
async def test_load_lectures_cache_default_no_codes_keys() -> None:
    """기본 호출엔 codes 필터 키가 없다 (하위 호환, 응답 스키마 불변)."""
    cache_mod.save_lectures_cache(_build_cache())

    resp = await server.load_lectures_cache(2026, "1")

    assert "lectures" not in resp
    assert "codes" not in resp
    assert "matched_count" not in resp
    assert "unmatched_codes" not in resp


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
    assert resp["summary"] is False


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


@pytest.mark.asyncio
async def test_find_lectures_summary_save_to_cache_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """summary + save_to_cache=False → 저장 스킵, _cache.saved=False/cached_at=None."""
    monkeypatch.setattr(server, "_run_with_session", _fake_success_run)

    resp = await server.find_lectures(
        2026,
        "1",
        "major",
        collage="IT대학",
        department="컴퓨터학부",
        summary=True,
        save_to_cache=False,
    )

    assert "lectures" not in resp
    assert resp["summary"] is True
    assert resp["_cache"]["saved"] is False
    assert resp["_cache"]["cached_at"] is None
    # 저장을 건너뛰었으므로 캐시에 그룹이 남지 않는다
    cache, _ = cache_mod.load_lectures_cache(2026, "1")
    assert cache is None


# ── parse_lectures_cache summary ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_parse_lectures_cache_summary_omits_parsed() -> None:
    """summary=True → parsed 생략, parsed_count + subject_groups/stats만."""
    cache_mod.save_lectures_cache(_build_cache())

    resp = await server.parse_lectures_cache(2026, "1", summary=True)

    assert resp["summary"] is True
    assert "parsed" not in resp  # find_lectures/load 메타 모드와 동일하게 키 생략
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
    assert "parsed" not in resp
    assert resp["parsed_count"] == 0
    assert resp["summary"] is True


@pytest.mark.asyncio
async def test_parse_lectures_cache_summary_stale() -> None:
    """stale 캐시 + summary=True → parsed 생략 + guidance 포함."""
    cache = _build_cache()
    cache.cached_at = datetime.now(timezone.utc) - timedelta(days=8)
    cache_mod.save_lectures_cache(cache)

    resp = await server.parse_lectures_cache(2026, "1", summary=True)

    assert resp["_cache"]["source"] == "stale"
    assert "parsed" not in resp
    assert resp["parsed_count"] == 4
    assert "guidance" in resp
    assert resp["summary"] is True


# ── SPR-95: include_subject_groups 옵션 ────────────────────────────────────


@pytest.mark.asyncio
async def test_parse_lectures_cache_omits_subject_groups() -> None:
    """include_subject_groups=False → subject_groups 키 생략 (~20KB 절감)."""
    cache_mod.save_lectures_cache(_build_cache())

    resp = await server.parse_lectures_cache(2026, "1", include_subject_groups=False)

    assert "subject_groups" not in resp
    # parsed/stats 등 나머지는 그대로 (기본 summary=False라 parsed 포함)
    assert len(resp["parsed"]) == 4
    assert resp["stats"]["total"] == 4


@pytest.mark.asyncio
async def test_parse_lectures_cache_miss_omits_subject_groups() -> None:
    """miss 경로에서도 include_subject_groups=False → 키 생략 (일관성)."""
    resp = await server.parse_lectures_cache(
        2026, "2", summary=True, include_subject_groups=False
    )

    assert resp["_cache"]["source"] == "miss"
    assert "subject_groups" not in resp


# ── SPR-87: 부분 조회 옵션 (codes / subject_keys / category_prefixes) ──


def _build_category_cache() -> LecturesCache:
    """카테고리 다양성을 담은 캐시 — 부분 조회(SPR-87) 테스트용."""
    return LecturesCache(
        year=2026,
        semester="1",
        groups={
            "major_IT대학_컴퓨터학부": LectureGroupEntry(
                category_type="major",
                params={"collage": "IT대학", "department": "컴퓨터학부"},
                lectures=[
                    {
                        "code": "2150164203",
                        "name": "전기기초",
                        "category": "전기-컴퓨터학부",
                        "schedule_room": "월 10:30-12:00 (베어드홀 01101-김자헌)",
                    },
                    {
                        "code": "2150164301",
                        "name": "전공필수",
                        "category": "전필-컴퓨터학부",
                        "schedule_room": "화 11:00-13:00 (숭덕 02108-박은영)",
                    },
                ],
                count=2,
                error=None,
            ),
            "chapel": LectureGroupEntry(
                category_type="chapel",
                params={"lecture_name": "비전채플"},
                lectures=[
                    {
                        "code": "2150078501",
                        "name": "비전채플",
                        "category": "채플",
                        "schedule_room": "수 15:00-16:00 (베어드홀 A)",
                    },
                ],
                count=1,
                error=None,
            ),
            "optional_elective_all": LectureGroupEntry(
                category_type="optional_elective",
                params={"category": "전체"},
                lectures=[
                    {
                        "code": "3161011001",
                        "name": "교양선택",
                        "category": "교선",
                        "schedule_room": "목 13:00-14:15 (진리 A)",
                    },
                ],
                count=1,
                error=None,
            ),
        },
        cached_at=datetime.now(timezone.utc),
    )


@pytest.mark.asyncio
async def test_parse_lectures_cache_partial_category_prefixes() -> None:
    """category_prefixes → parsed만 필터, subject_groups/stats는 전체 기준."""
    cache_mod.save_lectures_cache(_build_category_cache())

    resp = await server.parse_lectures_cache(
        2026, "1", category_prefixes=["전기-", "채플"]
    )

    codes = [p["code"] for p in resp["parsed"]]
    assert "2150164203" in codes  # 전기-컴퓨터학부
    assert "2150078501" in codes  # 채플
    assert "2150164301" not in codes  # 전필-은 prefix 불일치
    assert "3161011001" not in codes  # 교선 제외
    assert resp["parsed_count"] == 2
    assert resp["summary"] is False
    # 인덱스/stats는 전체 기준 — 컴포저가 분반 인덱스는 전부 가진다
    assert set(resp["subject_groups"].keys()) >= {
        "21501642",
        "21501643",
        "21500785",
        "31610110",
    }
    assert resp["stats"]["total"] == 4
    assert resp["filters"]["category_prefixes"] == ["전기-", "채플"]
    assert resp["filters"]["codes"] is None
    assert resp["filters"]["subject_keys"] is None


@pytest.mark.asyncio
async def test_parse_lectures_cache_partial_subject_keys() -> None:
    """subject_keys → 분반 그룹 전체 반환."""
    cache_mod.save_lectures_cache(_build_cache())  # chapel 2150078501/02

    resp = await server.parse_lectures_cache(2026, "1", subject_keys=["21500785"])

    assert [p["code"] for p in resp["parsed"]] == ["2150078501", "2150078502"]
    assert resp["parsed_count"] == 2
    assert resp["filters"]["subject_keys"] == ["21500785"]


@pytest.mark.asyncio
async def test_parse_lectures_cache_partial_codes() -> None:
    """codes → 정확 조회 + 필터 echo + 전체 subject_groups 유지."""
    cache_mod.save_lectures_cache(_build_cache())

    resp = await server.parse_lectures_cache(2026, "1", codes=["CS10101"])

    assert [p["code"] for p in resp["parsed"]] == ["CS10101"]
    assert resp["parsed_count"] == 1
    assert resp["filters"] == {
        "codes": ["CS10101"],
        "subject_keys": None,
        "category_prefixes": None,
    }
    # 필터로 parsed에 없는 분반도 subject_groups 인덱스에는 남는다
    assert resp["subject_groups"]["21500785"] == ["2150078501", "2150078502"]


@pytest.mark.asyncio
async def test_parse_lectures_cache_no_filter_backward_compat() -> None:
    """필터 미지정 → 전체 parsed + filters 모두 None (하위 호환)."""
    cache_mod.save_lectures_cache(_build_cache())

    resp = await server.parse_lectures_cache(2026, "1")

    assert len(resp["parsed"]) == 4
    assert resp["parsed_count"] == 4
    assert resp["filters"] == {
        "codes": None,
        "subject_keys": None,
        "category_prefixes": None,
    }


@pytest.mark.asyncio
async def test_parse_lectures_cache_partial_summary_omits_parsed() -> None:
    """summary=True + 필터 → parsed 생략, 필터는 필터된 수로 parsed_count 반영."""
    cache_mod.save_lectures_cache(_build_category_cache())

    resp = await server.parse_lectures_cache(
        2026, "1", summary=True, category_prefixes=["교선"]
    )

    assert "parsed" not in resp
    assert resp["summary"] is True
    assert resp["parsed_count"] == 1  # 교선 1강의만 필터됨
    assert resp["stats"]["total"] == 4  # stats는 전체
    assert resp["filters"]["category_prefixes"] == ["교선"]


@pytest.mark.asyncio
async def test_parse_lectures_cache_partial_miss() -> None:
    """miss 경로도 filters echo 포함."""
    resp = await server.parse_lectures_cache(
        2026, "2", category_prefixes=["채플"]
    )

    assert resp["_cache"]["source"] == "miss"
    assert resp["parsed"] == []
    assert resp["parsed_count"] == 0
    assert resp["filters"]["category_prefixes"] == ["채플"]
