"""강의 캐시 로직 테스트 (TTL, atomic write, 스키마 위반 복구)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from soongpt_mcp import lectures_cache as cache_mod
from soongpt_mcp.lectures_cache import (
    CACHE_TTL_DAYS,
    LectureGroupEntry,
    LecturesCache,
    group_key_for,
    is_lectures_cache_fresh,
    load_lectures_cache,
    merge_lectures_groups,
    save_lectures_cache,
    save_lectures_group,
)


@pytest.fixture
def isolated_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """CLAUDE_PLUGIN_DATA → tmp_path."""
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path))
    return tmp_path


def _sample_cache(year: int = 2026, semester: str = "1") -> LecturesCache:
    return LecturesCache(
        year=year,
        semester=semester,
        groups={
            "major_primary": LectureGroupEntry(
                category_type="major",
                params={"collage": "IT대학", "department": "컴퓨터학부"},
                lectures=[{"code": "CS101", "name": "컴퓨터개론"}],
                count=1,
                error=None,
            ),
            "optional_elective_all": LectureGroupEntry(
                category_type="optional_elective",
                params={"category": "전체"},
                lectures=[],
                count=0,
                error=None,
            ),
        },
        cached_at=datetime.now(timezone.utc),
    )


def test_resolve_path_uses_plugin_data(isolated_root: Path) -> None:
    assert cache_mod.resolve_lectures_cache_path(2026, "1") == (
        isolated_root / "lectures_2026_1.json"
    )


def test_save_then_load_roundtrip(isolated_root: Path) -> None:
    cache = _sample_cache()
    save_lectures_cache(cache)
    loaded, cached_at = load_lectures_cache(2026, "1")
    assert loaded is not None
    assert loaded.year == 2026
    assert loaded.semester == "1"
    assert "major_primary" in loaded.groups
    assert loaded.groups["major_primary"].count == 1
    assert loaded.groups["major_primary"].lectures[0]["code"] == "CS101"
    assert cached_at is not None
    assert cached_at.tzinfo is not None


def test_load_missing_returns_none(isolated_root: Path) -> None:
    assert load_lectures_cache(2026, "1") == (None, None)


def test_is_fresh_within_ttl() -> None:
    now = datetime(2026, 7, 28, tzinfo=timezone.utc)
    cached = now - timedelta(days=CACHE_TTL_DAYS - 1)
    assert is_lectures_cache_fresh(cached, now=now) is True


def test_is_fresh_expired() -> None:
    now = datetime(2026, 7, 28, tzinfo=timezone.utc)
    cached = now - timedelta(days=CACHE_TTL_DAYS + 1)
    assert is_lectures_cache_fresh(cached, now=now) is False


def test_load_corrupted_json_returns_none(isolated_root: Path) -> None:
    (isolated_root / "lectures_2026_1.json").write_text(
        "not json {{{", encoding="utf-8"
    )
    assert load_lectures_cache(2026, "1") == (None, None)


def test_load_schema_violation_extra_field_returns_none(
    isolated_root: Path,
) -> None:
    """extra="forbid" 위반 케이스."""
    payload = {
        "year": 2026,
        "semester": "1",
        "groups": {},
        "cached_at": datetime.now(timezone.utc).isoformat(),
        "rogue_field": "should fail",
    }
    (isolated_root / "lectures_2026_1.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    assert load_lectures_cache(2026, "1") == (None, None)


def test_load_missing_cached_at_returns_none(isolated_root: Path) -> None:
    payload = {"year": 2026, "semester": "1", "groups": {}}
    (isolated_root / "lectures_2026_1.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    assert load_lectures_cache(2026, "1") == (None, None)


def test_load_invalid_cached_at_returns_none(isolated_root: Path) -> None:
    payload = {
        "year": 2026,
        "semester": "1",
        "groups": {},
        "cached_at": "not-iso",
    }
    (isolated_root / "lectures_2026_1.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    assert load_lectures_cache(2026, "1") == (None, None)


def test_save_creates_parent_directory(isolated_root: Path) -> None:
    """이미 isolated_root 자체는 존재하지만, 파일 저장 시 부모 디렉토리 보장 로직 검증."""
    cache = _sample_cache()
    target = save_lectures_cache(cache)
    assert target.exists()
    assert target.parent == isolated_root


def test_save_overwrite(isolated_root: Path) -> None:
    """동일 학기 재저장 시 덮어쓰기."""
    cache = _sample_cache()
    save_lectures_cache(cache)
    updated = _sample_cache()
    updated.groups["new_group"] = LectureGroupEntry(
        category_type="chapel",
        params={"lecture_name": "비전채플"},
        lectures=[],
        count=0,
    )
    save_lectures_cache(updated)
    loaded, _ = load_lectures_cache(2026, "1")
    assert loaded is not None
    assert "new_group" in loaded.groups


def test_different_semesters_isolated(isolated_root: Path) -> None:
    """학기별 파일 분리 검증."""
    save_lectures_cache(_sample_cache(2026, "1"))
    save_lectures_cache(_sample_cache(2026, "2"))
    loaded1, _ = load_lectures_cache(2026, "1")
    loaded2, _ = load_lectures_cache(2026, "2")
    assert loaded1 is not None and loaded2 is not None
    assert loaded1.semester == "1"
    assert loaded2.semester == "2"


# ---------------------------------------------------------------------------
# SPR-75: 그룹 키 규칙 (group_key_for) — 키 규칙은 이 테스트로 고정된다.
# ---------------------------------------------------------------------------

GROUP_KEY_CASES = [
    pytest.param(
        "optional_elective", {"category": "전체"}, "optional_elective_all",
        id="optional_elective_all",
    ),
    pytest.param(
        "optional_elective", {"category": "과학·기술"},
        "optional_elective_과학·기술",
        id="optional_elective_분야",
    ),
    pytest.param(
        "major", {"collage": "IT대학", "department": "컴퓨터학부"},
        "major_IT대학_컴퓨터학부",
        id="major_collage_department",
    ),
    pytest.param(
        "major",
        {"collage": "IT대학", "department": "컴퓨터학부", "major": "컴퓨터전공"},
        "major_IT대학_컴퓨터학부_컴퓨터전공",
        id="major_with_major_param",
    ),
    pytest.param(
        "recognized_other_major",
        {"collage": "IT대학", "department": "컴퓨터학부"},
        "recognized_other_major_IT대학_컴퓨터학부",
        id="recognized_other_major",
    ),
    pytest.param(
        "graduated", {"collage": "IT대학", "department": "컴퓨터학부"},
        "graduated_IT대학_컴퓨터학부",
        id="graduated",
    ),
    pytest.param(
        "required_elective", {"lecture_name": "[SW와AI]AI개발과실전"},
        "required_elective_[SW와AI]AI개발과실전",
        id="required_elective_원본_과목명",
    ),
    pytest.param("chapel", {"lecture_name": "비전채플"}, "chapel", id="chapel"),
    pytest.param(
        "connected_major", {"major": "연계전공"}, "connected_major",
        id="connected_major",
    ),
    pytest.param(
        "united_major", {"major": "융합전공"}, "united_major", id="united_major",
    ),
    pytest.param("education", {}, "education", id="education"),
    pytest.param("cyber", {}, "cyber", id="cyber"),
    pytest.param(
        "find_by_lecture", {"keyword": "미분적분학"},
        "find_by_lecture_미분적분학", id="find_by_lecture",
    ),
    pytest.param(
        "find_by_professor", {"keyword": "홍길동"},
        "find_by_professor_홍길동", id="find_by_professor",
    ),
]


@pytest.mark.parametrize(
    ("category_type", "params", "expected_key"), GROUP_KEY_CASES
)
def test_group_key_for_rules(
    category_type: str, params: dict[str, str], expected_key: str
) -> None:
    assert (
        group_key_for(category_type, **params) == expected_key
    )


def test_group_key_for_unknown_type_raises() -> None:
    with pytest.raises(ValueError, match="미지원 category_type"):
        group_key_for("totally_unknown")


def test_group_key_for_different_departments_distinct() -> None:
    """같은 category_type·collage라도 department가 다르면 다른 키."""
    a = group_key_for(
        "major", collage="IT대학", department="컴퓨터학부"
    )
    b = group_key_for(
        "major", collage="IT대학", department="전자정보공학부"
    )
    assert a != b


# ---------------------------------------------------------------------------
# SPR-75: 그룹 병합 (merge_lectures_groups) — 덮어쓰기 제거.
# ---------------------------------------------------------------------------

def _entry(
    category_type: str, params: dict[str, str | None], code: str
) -> LectureGroupEntry:
    return LectureGroupEntry(
        category_type=category_type,
        params=params,
        lectures=[{"code": code}],
        count=1,
        error=None,
    )


def _legacy_major_cache() -> LecturesCache:
    """구버전 스킬이 저장한 키(major_primary 등)로 구성된 캐시."""
    return LecturesCache(
        year=2026,
        semester="1",
        groups={
            "major_primary": _entry(
                "major",
                {"collage": "IT대학", "department": "컴퓨터학부", "major": None},
                "OLD",
            ),
            "optional_elective_all": _entry(
                "optional_elective", {"category": "전체"}, "ELEC"
            ),
        },
        cached_at=datetime.now(timezone.utc),
    )


def test_merge_preserves_unrelated_and_replaces_same_lookup() -> None:
    """같은 조회(식별 파라미터) 그룹만 대체되고, 무관한 그룹은 보존된다."""
    existing = _legacy_major_cache()
    new_groups = {
        "major_IT대학_컴퓨터학부": _entry(
            "major",
            {"collage": "IT대학", "department": "컴퓨터학부", "major": None},
            "NEW",
        )
    }
    merged = merge_lectures_groups(existing, 2026, "1", new_groups)

    # 레거시 major_primary(같은 조회) → canonical 키로 대체되어 사라짐.
    assert "major_primary" not in merged.groups
    assert merged.groups["major_IT대학_컴퓨터학부"].lectures[0]["code"] == "NEW"
    # 무관한 그룹은 보존.
    assert merged.groups["optional_elective_all"].lectures[0]["code"] == "ELEC"


def test_merge_keeps_distinct_lookups_separate() -> None:
    """다른 department 조회는 서로 대체되지 않고 공존."""
    existing = LecturesCache(
        year=2026,
        semester="1",
        groups={
            "major_IT대학_컴퓨터학부": _entry(
                "major",
                {"collage": "IT대학", "department": "컴퓨터학부", "major": None},
                "CS",
            )
        },
        cached_at=datetime.now(timezone.utc),
    )
    new_groups = {
        "major_IT대학_전자정보공학부": _entry(
            "major",
            {"collage": "IT대학", "department": "전자정보공학부", "major": None},
            "EE",
        )
    }
    merged = merge_lectures_groups(existing, 2026, "1", new_groups)
    assert set(merged.groups) == {
        "major_IT대학_컴퓨터학부",
        "major_IT대학_전자정보공학부",
    }


def test_merge_with_no_existing_creates_cache() -> None:
    merged = merge_lectures_groups(
        None,
        2026,
        "1",
        {"chapel": _entry("chapel", {"lecture_name": "비전채플"}, "CH1")},
    )
    assert merged.year == 2026
    assert merged.semester == "1"
    assert "chapel" in merged.groups


def _error_entry(
    category_type: str, params: dict[str, str | None], message: str
) -> LectureGroupEntry:
    return LectureGroupEntry(
        category_type=category_type,
        params=params,
        lectures=[],
        count=0,
        error=message,
    )


def test_merge_error_group_does_not_replace_existing_success() -> None:
    """부분 실패(error 그룹)가 기존 성공 그룹을 대체하지 않는다 (critic MAJOR-3)."""
    existing = LecturesCache(
        year=2026,
        semester="1",
        groups={
            "major_IT대학_컴퓨터학부": _entry(
                "major",
                {"collage": "IT대학", "department": "컴퓨터학부", "major": None},
                "CS",
            )
        },
        cached_at=datetime.now(timezone.utc),
    )
    error_entry = _error_entry(
        "major",
        {"collage": "IT대학", "department": "컴퓨터학부", "major": None},
        "일시적 오류",
    )
    merged = merge_lectures_groups(
        existing, 2026, "1", {"major_IT대학_컴퓨터학부": error_entry}
    )
    # 기존 성공 데이터 보존 + error 그룹은 저장되지 않음.
    group = merged.groups["major_IT대학_컴퓨터학부"]
    assert group.error is None
    assert group.lectures[0]["code"] == "CS"
    assert len(merged.groups) == 1


def test_merge_error_group_recorded_when_no_existing_success() -> None:
    """기존 성공 그룹이 없으면 error 그룹을 남겨 load에서 실패를 확인 가능."""
    error_entry = _error_entry("chapel", {"lecture_name": "비전채플"}, "조회 실패")
    merged = merge_lectures_groups(None, 2026, "1", {"chapel": error_entry})
    assert merged.groups["chapel"].error == "조회 실패"
    assert merged.groups["chapel"].lectures == []


def test_merge_success_replaces_existing_error() -> None:
    """재조회 성공은 기존 error 그룹을 대체한다."""
    existing = LecturesCache(
        year=2026,
        semester="1",
        groups={
            "major_IT대학_컴퓨터학부": _error_entry(
                "major",
                {"collage": "IT대학", "department": "컴퓨터학부", "major": None},
                "이전 실패",
            )
        },
        cached_at=datetime.now(timezone.utc),
    )
    merged = merge_lectures_groups(
        existing,
        2026,
        "1",
        {
            "major_IT대학_컴퓨터학부": _entry(
                "major",
                {"collage": "IT대학", "department": "컴퓨터학부", "major": None},
                "NEW",
            )
        },
    )
    group = merged.groups["major_IT대학_컴퓨터학부"]
    assert group.error is None
    assert group.lectures[0]["code"] == "NEW"


def test_merge_chapel_replaces_regardless_of_lecture_name() -> None:
    """채플은 grade 기반 단일 종류 불변 — 다른 채플명 fetch가 기존 chapel 대체."""
    existing = LecturesCache(
        year=2026,
        semester="1",
        groups={"chapel": _entry("chapel", {"lecture_name": "비전채플"}, "V")},
        cached_at=datetime.now(timezone.utc),
    )
    merged = merge_lectures_groups(
        existing,
        2026,
        "1",
        {"chapel": _entry("chapel", {"lecture_name": "소그룹채플"}, "S")},
    )
    assert len(merged.groups) == 1
    assert merged.groups["chapel"].lectures[0]["code"] == "S"


def test_save_lectures_group_incremental_merge(isolated_root: Path) -> None:
    """그룹 단위 병합 저장 — 나중에 저장한 그룹이 앞선 그룹을 보존."""
    save_lectures_group(
        2026,
        "1",
        "major_IT대학_컴퓨터학부",
        _entry("major", {"collage": "IT대학", "department": "컴퓨터학부"}, "CS"),
    )
    save_lectures_group(
        2026,
        "1",
        "optional_elective_all",
        _entry("optional_elective", {"category": "전체"}, "ELEC"),
    )
    cache, _ = load_lectures_cache(2026, "1")
    assert cache is not None
    assert set(cache.groups) == {
        "major_IT대학_컴퓨터학부",
        "optional_elective_all",
    }
    assert cache.groups["major_IT대학_컴퓨터학부"].lectures[0]["code"] == "CS"
    assert cache.groups["optional_elective_all"].lectures[0]["code"] == "ELEC"
