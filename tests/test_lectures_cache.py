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
    is_lectures_cache_fresh,
    load_lectures_cache,
    save_lectures_cache,
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
