"""학과-단과대 매핑 캐시 로직 테스트 (TTL, atomic write, 손상 복구)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from soongpt_mcp import department_map as dm_mod
from soongpt_mcp.department_map import (
    CACHE_TTL_DAYS,
    DepartmentMap,
    is_department_map_fresh,
    load_department_map,
    save_department_map,
)


@pytest.fixture
def isolated_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """CLAUDE_PLUGIN_DATA → tmp_path."""
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path))
    return tmp_path


@pytest.fixture
def sample_map() -> DepartmentMap:
    return DepartmentMap(
        year=2026,
        semester="2",
        mapping={"컴퓨터학부": "IT대학", "경영학부": "경영대학"},
        built_at=datetime(2026, 7, 30, tzinfo=timezone.utc),
    )


def test_resolve_path_uses_plugin_data(isolated_root: Path) -> None:
    assert dm_mod.resolve_department_map_path(2026) == (
        isolated_root / "department_map_2026.json"
    )


def test_save_then_load_roundtrip(
    isolated_root: Path, sample_map: DepartmentMap
) -> None:
    save_department_map(sample_map)
    loaded, built_at = load_department_map(2026)
    assert loaded is not None
    assert loaded.year == 2026
    assert loaded.semester == "2"
    assert loaded.mapping == {"컴퓨터학부": "IT대학", "경영학부": "경영대학"}
    assert built_at is not None
    assert built_at.tzinfo is not None
    assert built_at == sample_map.built_at


def test_load_missing_returns_none(isolated_root: Path) -> None:
    assert load_department_map(2026) == (None, None)


def test_is_fresh_within_ttl() -> None:
    now = datetime(2026, 7, 30, tzinfo=timezone.utc)
    built = now - timedelta(days=CACHE_TTL_DAYS - 1)
    assert is_department_map_fresh(built, now=now) is True


def test_is_fresh_expired() -> None:
    now = datetime(2026, 7, 30, tzinfo=timezone.utc)
    built = now - timedelta(days=CACHE_TTL_DAYS + 1)
    assert is_department_map_fresh(built, now=now) is False


def test_load_corrupted_json_returns_none(isolated_root: Path) -> None:
    (isolated_root / "department_map_2026.json").write_text(
        "not json {{{", encoding="utf-8"
    )
    assert load_department_map(2026) == (None, None)


def test_load_schema_violation_returns_none(isolated_root: Path) -> None:
    # year가 문자열, mapping이 리스트 등 스키마 위반
    (isolated_root / "department_map_2026.json").write_text(
        json.dumps(
            {
                "year": "not-int",
                "semester": "2",
                "mapping": ["not", "dict"],
                "built_at": datetime.now(timezone.utc).isoformat(),
            }
        ),
        encoding="utf-8",
    )
    assert load_department_map(2026) == (None, None)


def test_load_naive_built_at_returns_none(isolated_root: Path) -> None:
    # 타임존 없는 built_at은 신선도 판단 불가 → 무효
    (isolated_root / "department_map_2026.json").write_text(
        json.dumps(
            {
                "year": 2026,
                "semester": "2",
                "mapping": {"컴퓨터학부": "IT대학"},
                "built_at": "2026-07-30T00:00:00",
            }
        ),
        encoding="utf-8",
    )
    assert load_department_map(2026) == (None, None)


def test_save_creates_parent_directory(tmp_path: Path) -> None:
    nested = tmp_path / "deep" / "department_map_2026.json"
    dm = DepartmentMap(
        year=2026,
        semester="2",
        mapping={"X": "Y"},
        built_at=datetime.now(timezone.utc),
    )
    save_department_map(dm, path=nested)
    assert nested.exists()


# --- 번들 seed 로딩 ---


def test_load_bundled_missing_returns_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """seed 파일 자체가 없으면 None."""
    monkeypatch.setattr(
        dm_mod,
        "resolve_bundled_department_map_path",
        lambda year: tmp_path / f"department_map_{year}.json",
    )
    assert dm_mod.load_bundled_department_map(2026) is None


def test_load_bundled_valid_returns_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_path = tmp_path / "department_map_2026.json"
    seed_path.write_text(
        json.dumps(
            {
                "year": 2026,
                "semester": "1",
                "mapping": {"컴퓨터학부": "IT대학"},
                "built_at": datetime.now(timezone.utc).isoformat(),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        dm_mod,
        "resolve_bundled_department_map_path",
        lambda year: tmp_path / f"department_map_{year}.json",
    )

    loaded = dm_mod.load_bundled_department_map(2026)
    assert loaded is not None
    assert loaded.mapping == {"컴퓨터학부": "IT대학"}
    assert loaded.year == 2026


def test_load_bundled_corrupted_returns_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_path = tmp_path / "department_map_2026.json"
    seed_path.write_text("not json {{{", encoding="utf-8")
    monkeypatch.setattr(
        dm_mod,
        "resolve_bundled_department_map_path",
        lambda year: tmp_path / f"department_map_{year}.json",
    )
    assert dm_mod.load_bundled_department_map(2026) is None


def test_load_bundled_schema_violation_returns_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_path = tmp_path / "department_map_2026.json"
    seed_path.write_text(
        json.dumps(
            {
                "year": "not-int",
                "semester": "2",
                "mapping": "should-be-dict",
                "built_at": datetime.now(timezone.utc).isoformat(),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        dm_mod,
        "resolve_bundled_department_map_path",
        lambda year: tmp_path / f"department_map_{year}.json",
    )
    assert dm_mod.load_bundled_department_map(2026) is None
