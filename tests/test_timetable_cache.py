"""시간표 후보 캐시 모듈 + server 도구 3개 테스트 (SPR-52).

모듈(영속화/스키마)과 server 도구(load/save/clear_timetable_candidates)를
CLAUDE_PLUGIN_DATA 격리로 검증. USAINT 세션 사용 안 함.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from soongpt_mcp import lectures_cache as lc
from soongpt_mcp import server
from soongpt_mcp.timetable_cache import (
    TimetableCache,
    TimetableCandidate,
    add_candidate,
    clear_timetable_cache,
    load_timetable_cache,
    resolve_timetable_path,
    save_timetable_cache,
)


@pytest.fixture
def isolated_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """CLAUDE_PLUGIN_DATA → tmp_path."""
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path))
    return tmp_path


def _candidate(**overrides: object) -> dict:
    """save_timetable_candidate에 전달하는 후보 dict."""
    base = {
        "name": "안 A — 15학점",
        "lecture_codes": ["2150164203", "2150164204"],
        "total_credits": 15.0,
        "has_blocking_conflict": False,
        "conflicts_summary": "충돌 없음",
        "notes": "",
        "confirmed": False,
    }
    base.update(overrides)
    return base


def _sample_cache(year: int = 2026, semester: str = "1") -> TimetableCache:
    return TimetableCache(
        year=year,
        semester=semester,
        candidates=[
            TimetableCandidate.model_validate(_candidate()),
        ],
        generation_params={
            "interview_updated_at": "2026-07-01T00:00:00+00:00",
            "lectures_cached_at": "2026-07-02T00:00:00+00:00",
        },
        cached_at=datetime.now(timezone.utc),
    )


def _save_lectures_cache(
    isolated_root: Path, codes: tuple[str, ...] = ("2150164203", "2150164204")
) -> None:
    """code 존재 검증용 강의 캐시 저장."""
    cache = lc.LecturesCache(
        year=2026,
        semester="1",
        groups={
            "major_primary": lc.LectureGroupEntry(
                category_type="major",
                params={},
                lectures=[{"code": code} for code in codes],
                count=len(codes),
                error=None,
            )
        },
        cached_at=datetime.now(timezone.utc),
    )
    lc.save_lectures_cache(cache)


# ── 모듈: 경로 / roundtrip / None-safe ─────────────────────────────────


def test_resolve_path_uses_plugin_data(isolated_root: Path) -> None:
    assert resolve_timetable_path(2026, "1") == (
        isolated_root / "timetable_2026_1.json"
    )


def test_save_then_load_roundtrip(isolated_root: Path) -> None:
    save_timetable_cache(_sample_cache())
    loaded = load_timetable_cache(2026, "1")
    assert loaded is not None
    assert loaded.year == 2026
    assert loaded.semester == "1"
    assert len(loaded.candidates) == 1
    cand = loaded.candidates[0]
    assert cand.name == "안 A — 15학점"
    assert cand.lecture_codes == ["2150164203", "2150164204"]
    assert cand.total_credits == 15.0
    assert cand.has_blocking_conflict is False
    assert cand.conflicts_summary == "충돌 없음"
    assert loaded.generation_params["interview_updated_at"]
    assert loaded.cached_at.tzinfo is not None


def test_load_missing_returns_none(isolated_root: Path) -> None:
    assert load_timetable_cache(2026, "1") is None


def test_load_corrupted_json_returns_none(isolated_root: Path) -> None:
    (isolated_root / "timetable_2026_1.json").write_text(
        "not json {{{", encoding="utf-8"
    )
    assert load_timetable_cache(2026, "1") is None


def test_load_schema_violation_extra_field_returns_none(
    isolated_root: Path,
) -> None:
    """extra="forbid" 위반 케이스."""
    payload = {
        "year": 2026,
        "semester": "1",
        "candidates": [],
        "generation_params": {},
        "cached_at": datetime.now(timezone.utc).isoformat(),
        "rogue_field": "should fail",
    }
    (isolated_root / "timetable_2026_1.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    assert load_timetable_cache(2026, "1") is None


def test_load_missing_cached_at_returns_none(isolated_root: Path) -> None:
    payload = {"year": 2026, "semester": "1", "candidates": []}
    (isolated_root / "timetable_2026_1.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    assert load_timetable_cache(2026, "1") is None


def test_load_preserves_file_on_corruption(isolated_root: Path) -> None:
    """손상 파일은 삭제하지 않고 보존 (사용자 산출물이 사라진 것처럼 보이지 않게)."""
    target = isolated_root / "timetable_2026_1.json"
    garbage = "not json {{{"
    target.write_text(garbage, encoding="utf-8")
    assert load_timetable_cache(2026, "1") is None
    assert target.exists()
    assert target.read_text(encoding="utf-8") == garbage


def test_save_creates_parent_directory(isolated_root: Path) -> None:
    target = save_timetable_cache(_sample_cache())
    assert target.exists()
    assert target.parent == isolated_root


def test_save_atomic_no_tmp_leftover(isolated_root: Path) -> None:
    """atomic write 후 .tmp 잔재 없음."""
    target = save_timetable_cache(_sample_cache())
    assert not target.with_suffix(".json.tmp").exists()


def test_different_semesters_isolated(isolated_root: Path) -> None:
    save_timetable_cache(_sample_cache(2026, "1"))
    save_timetable_cache(_sample_cache(2026, "2"))
    assert load_timetable_cache(2026, "1") is not None
    assert load_timetable_cache(2026, "2") is not None
    assert load_timetable_cache(2026, "2").semester == "2"  # type: ignore[union-attr]


# ── 모듈: add_candidate (append / 같은 name replace) ────────────────────


def test_add_candidate_append(isolated_root: Path) -> None:
    cache = _sample_cache()
    second = TimetableCandidate.model_validate(
        _candidate(name="안 B — 12학점", total_credits=12.0)
    )
    updated, replaced = add_candidate(cache, second)
    assert replaced is False
    assert len(updated.candidates) == 2
    assert [c.name for c in updated.candidates] == ["안 A — 15학점", "안 B — 12학점"]
    # generation_params는 유지 (model_copy 기반)
    assert updated.generation_params == cache.generation_params


def test_add_candidate_same_name_replace(isolated_root: Path) -> None:
    cache = _sample_cache()
    revised = TimetableCandidate.model_validate(
        _candidate(
            name="안 A — 15학점",
            lecture_codes=["2150164203"],
            total_credits=12.0,
            confirmed=True,
        )
    )
    updated, replaced = add_candidate(cache, revised)
    assert replaced is True
    assert len(updated.candidates) == 1  # 폐기 후보 축적 방지
    assert updated.candidates[0].total_credits == 12.0
    assert updated.candidates[0].lecture_codes == ["2150164203"]
    assert updated.candidates[0].confirmed is True


def test_add_candidate_other_names_untouched(isolated_root: Path) -> None:
    cache = _sample_cache()
    cache.candidates.append(
        TimetableCandidate.model_validate(_candidate(name="안 B — 12학점"))
    )
    updated, replaced = add_candidate(
        cache,
        TimetableCandidate.model_validate(
            _candidate(name="안 B — 12학점", total_credits=13.0)
        ),
    )
    assert replaced is True
    assert len(updated.candidates) == 2
    by_name = {c.name: c for c in updated.candidates}
    assert by_name["안 A — 15학점"].total_credits == 15.0
    assert by_name["안 B — 12학점"].total_credits == 13.0


def test_add_candidate_none_creates_new_cache(isolated_root: Path) -> None:
    updated, replaced = add_candidate(
        None,
        TimetableCandidate.model_validate(_candidate()),
        year=2026,
        semester="1",
    )
    assert replaced is False
    assert updated.year == 2026
    assert updated.semester == "1"
    assert len(updated.candidates) == 1


def test_add_candidate_none_without_year_semester_raises() -> None:
    with pytest.raises(ValueError, match="year/semester"):
        add_candidate(None, TimetableCandidate.model_validate(_candidate()))


def test_confirmed_default_false() -> None:
    cand = TimetableCandidate.model_validate(_candidate())
    assert cand.confirmed is False


def test_created_at_default_filled() -> None:
    cand = TimetableCandidate.model_validate(_candidate())
    assert cand.created_at is not None
    assert cand.created_at.tzinfo is not None


def test_clear_removes_file(isolated_root: Path) -> None:
    save_timetable_cache(_sample_cache())
    assert clear_timetable_cache(2026, "1") is True
    assert load_timetable_cache(2026, "1") is None


def test_clear_missing_file_returns_false(isolated_root: Path) -> None:
    assert clear_timetable_cache(2026, "1") is False


# ── server 도구: save→load roundtrip / clear→miss / code 검증 ───────────


@pytest.mark.asyncio
async def test_server_save_then_load_roundtrip(isolated_root: Path) -> None:
    _save_lectures_cache(isolated_root)
    saved = await server.save_timetable_candidate(2026, "1", _candidate())
    assert saved["saved"] is True
    assert saved["replaced"] is False
    assert saved["count"] == 1
    assert "path" in saved

    loaded = await server.load_timetable_candidates(2026, "1")
    assert loaded["_cache"]["source"] == "hit"
    assert loaded["_cache"]["saved_at"] is not None
    assert len(loaded["candidates"]) == 1
    assert loaded["candidates"][0]["name"] == "안 A — 15학점"
    assert loaded["candidates"][0]["confirmed"] is False


@pytest.mark.asyncio
async def test_server_save_same_name_replaces(isolated_root: Path) -> None:
    _save_lectures_cache(isolated_root)
    await server.save_timetable_candidate(2026, "1", _candidate())
    result = await server.save_timetable_candidate(
        2026, "1", _candidate(total_credits=12.0, confirmed=True)
    )
    assert result["replaced"] is True
    assert result["count"] == 1

    loaded = await server.load_timetable_candidates(2026, "1")
    assert len(loaded["candidates"]) == 1
    assert loaded["candidates"][0]["total_credits"] == 12.0
    assert loaded["candidates"][0]["confirmed"] is True


@pytest.mark.asyncio
async def test_server_load_miss_guidance(isolated_root: Path) -> None:
    result = await server.load_timetable_candidates(2026, "1")
    assert result["_cache"]["source"] == "miss"
    assert result["candidates"] == []
    assert result["generation_params"] == {}
    assert "composer" in result["guidance"]


@pytest.mark.asyncio
async def test_server_save_persists_generation_params(
    isolated_root: Path,
) -> None:
    """재개 mismatch 판정용 generation_params 스냅샷 저장 + merge."""
    _save_lectures_cache(isolated_root)
    await server.save_timetable_candidate(
        2026, "1", _candidate(),
        generation_params={
            "interview_updated_at": "2026-07-01T00:00:00+00:00",
            "lectures_cached_at": "2026-07-02T00:00:00+00:00",
        },
    )
    # 후속 저장에서 generation_params 없이 호출해도 기존 값 보존 (merge)
    await server.save_timetable_candidate(2026, "1", _candidate(name="안 B — 12학점"))
    loaded = await server.load_timetable_candidates(2026, "1")
    assert loaded["generation_params"] == {
        "interview_updated_at": "2026-07-01T00:00:00+00:00",
        "lectures_cached_at": "2026-07-02T00:00:00+00:00",
    }
    # 기존 값과 다른 키가 오면 merge (기존 키 유지 + 신규 키 추가)
    await server.save_timetable_candidate(
        2026, "1", _candidate(name="안 C — 9학점"),
        generation_params={"lectures_cached_at": "2026-07-05T00:00:00+00:00"},
    )
    loaded = await server.load_timetable_candidates(2026, "1")
    assert loaded["generation_params"]["lectures_cached_at"] == "2026-07-05T00:00:00+00:00"
    assert loaded["generation_params"]["interview_updated_at"] == "2026-07-01T00:00:00+00:00"


@pytest.mark.asyncio
async def test_server_clear_then_miss(isolated_root: Path) -> None:
    _save_lectures_cache(isolated_root)
    await server.save_timetable_candidate(2026, "1", _candidate())
    cleared = await server.clear_timetable_candidates(2026, "1")
    assert cleared == {"cleared": True}
    loaded = await server.load_timetable_candidates(2026, "1")
    assert loaded["_cache"]["source"] == "miss"
    # 파일 삭제 후 clear 재호출 → cleared: false
    again = await server.clear_timetable_candidates(2026, "1")
    assert again == {"cleared": False}


@pytest.mark.asyncio
async def test_server_save_unknown_code_raises(isolated_root: Path) -> None:
    """강의 캐시에 없는 code → ValueError (LLM 코드 전사 오류 차단, 중요①)."""
    _save_lectures_cache(isolated_root, codes=("2150164203",))
    with pytest.raises(ValueError, match="없는 code"):
        await server.save_timetable_candidate(
            2026, "1", _candidate(lecture_codes=["2150164203", "9999999999"])
        )


@pytest.mark.asyncio
async def test_server_save_no_lectures_cache_raises(isolated_root: Path) -> None:
    """강의 캐시 자체가 없으면 모든 code가 미존재 → ValueError."""
    with pytest.raises(ValueError, match="없는 code"):
        await server.save_timetable_candidate(2026, "1", _candidate())


@pytest.mark.asyncio
async def test_server_save_missing_required_field_raises(
    isolated_root: Path,
) -> None:
    """필수 필드 누락 → pydantic ValidationError (ValueError 하위)."""
    _save_lectures_cache(isolated_root)
    bad = _candidate()
    del bad["name"]
    with pytest.raises(ValidationError):
        await server.save_timetable_candidate(2026, "1", bad)


@pytest.mark.asyncio
async def test_server_save_extra_field_raises(isolated_root: Path) -> None:
    """extra="forbid" 위반 → ValidationError."""
    _save_lectures_cache(isolated_root)
    bad = _candidate(rogue_field="x")
    with pytest.raises(ValidationError):
        await server.save_timetable_candidate(2026, "1", bad)
