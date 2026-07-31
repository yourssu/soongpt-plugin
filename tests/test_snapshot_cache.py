"""snapshot_cache 모듈 테스트 — 프로필 + 수강이력 단일 SoT (SPR-46).

프로필 영속화(load_profile/save_profile), 스냅샷 캐시(load/save_snapshot_cache),
TTL, 이전 프로필 파일 마이그레이션을 검증한다.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from soongpt_mcp import snapshot_cache as sc
from soongpt_mcp.profile import UserProfile
from soongpt_mcp.schemas.usaint_schemas import BasicInfo, TakenCourse
from soongpt_mcp.snapshot_cache import (
    SnapshotCache,
    is_snapshot_cache_fresh,
    load_profile,
    load_snapshot_cache,
    resolve_snapshot_path,
    save_profile,
    save_snapshot_cache,
)


@pytest.fixture
def isolated_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """CLAUDE_PLUGIN_DATA 대신 tmp_path를 루트로 사용."""
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path))
    return tmp_path


@pytest.fixture
def fixed_period(monkeypatch: pytest.MonkeyPatch) -> tuple[int, str]:
    """현재 학기를 (2026, '1')로 고정."""
    monkeypatch.setattr(
        "soongpt_mcp.snapshot_cache.current_academic_period",
        lambda: (2026, "1"),
    )
    return 2026, "1"


# --- resolve_snapshot_path ---


def test_resolve_snapshot_path_uses_current_semester_by_default(
    isolated_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "soongpt_mcp.snapshot_cache.current_academic_period",
        lambda: (2026, "1"),
    )
    assert resolve_snapshot_path() == isolated_root / "snapshot_2026_1.json"


def test_resolve_snapshot_path_accepts_explicit_year_semester(
    isolated_root: Path,
) -> None:
    assert resolve_snapshot_path(2025, "2") == isolated_root / "snapshot_2025_2.json"


# --- is_snapshot_cache_fresh (TTL) ---


def test_is_fresh_within_ttl() -> None:
    now = datetime.now(timezone.utc)
    assert is_snapshot_cache_fresh(now - timedelta(days=sc.CACHE_TTL_DAYS - 1), now=now)
    assert not is_snapshot_cache_fresh(now - timedelta(days=sc.CACHE_TTL_DAYS + 1), now=now)


# --- SnapshotCache 스키마 ---


def test_snapshot_cache_roundtrip_full(isolated_root: Path) -> None:
    basic = BasicInfo(year=2023, grade=3, semester=5, department="컴퓨터학부")
    cache = SnapshotCache(
        year=2026,
        semester="1",
        profile=UserProfile.from_basic_info(basic),
        basicInfo=basic,
        takenCourses=[TakenCourse(year=2025, semester="1", subjectCodes=["CSE1234"])],
        lowGradeSubjectCodes=["CSE1234"],
        subjectNames={"CSE1234": "자료구조"},
        fetched_at=datetime.now(timezone.utc),
    )
    save_snapshot_cache(cache)

    loaded, fetched_at = load_snapshot_cache(2026, "1")
    assert loaded is not None
    assert loaded.profile.department == "컴퓨터학부"
    assert loaded.takenCourses[0].subjectCodes == ["CSE1234"]
    assert loaded.lowGradeSubjectCodes == ["CSE1234"]
    assert loaded.subjectNames == {"CSE1234": "자료구조"}
    assert fetched_at is not None


def test_snapshot_cache_extra_field_forbidden() -> None:
    with pytest.raises(ValidationError):
        SnapshotCache(year=2026, semester="1", profile=UserProfile(), unknown="x")


# --- load_snapshot_cache 실패 분기 ---


def test_load_snapshot_cache_missing_file(isolated_root: Path) -> None:
    cache, fetched_at = load_snapshot_cache(2026, "1")
    assert cache is None
    assert fetched_at is None


def test_load_snapshot_cache_corrupted_file(isolated_root: Path) -> None:
    (isolated_root / "snapshot_2026_1.json").write_text(
        "not json {{{", encoding="utf-8"
    )
    cache, fetched_at = load_snapshot_cache(2026, "1")
    assert cache is None
    assert fetched_at is None


def test_load_snapshot_cache_schema_violation(isolated_root: Path) -> None:
    (isolated_root / "snapshot_2026_1.json").write_text(
        json.dumps({"year": 2026, "semester": "1", "profile": {"grade": 99}}),
        encoding="utf-8",
    )
    cache, fetched_at = load_snapshot_cache(2026, "1")
    assert cache is None
    assert fetched_at is None


def test_profile_only_snapshot_has_none_fetched_at(
    isolated_root: Path, fixed_period: tuple[int, str]
) -> None:
    """프로필만 저장된 파일은 fetched_at=None — get_usaint_snapshot이 miss로 판단."""
    save_profile(UserProfile(student_id="20240001"))
    cache, fetched_at = load_snapshot_cache(2026, "1")
    assert cache is not None
    assert cache.profile.student_id == "20240001"
    assert fetched_at is None


# --- load_profile / save_profile (스냅샷 파일 기반) ---


def test_load_profile_missing_file_returns_none(isolated_root: Path) -> None:
    assert load_profile() is None


def test_save_then_load_roundtrip(
    isolated_root: Path, fixed_period: tuple[int, str]
) -> None:
    p = UserProfile(
        student_id="20240001",
        name="홍길동",
        college="IT대학",
        department="컴퓨터학부",
        grade=3,
        entered_year=2024,
    )
    save_profile(p)
    assert (isolated_root / "snapshot_2026_1.json").exists()

    loaded = load_profile()
    assert loaded is not None
    assert loaded.student_id == "20240001"
    assert loaded.name == "홍길동"
    assert loaded.department == "컴퓨터학부"
    assert loaded.grade == 3
    assert loaded.entered_year == 2024


def test_save_creates_parent_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    nested = tmp_path / "nested" / "deep"
    monkeypatch.setattr(
        "soongpt_mcp.snapshot_cache.resolve_snapshot_path",
        lambda *a, **k: nested / "snapshot_2026_1.json",
    )
    save_profile(UserProfile(name="x"), year=2026, semester="1")
    assert (nested / "snapshot_2026_1.json").exists()


def test_load_profile_handles_corrupted_snapshot(isolated_root: Path) -> None:
    (isolated_root / "snapshot_2026_1.json").write_text(
        "not json {{{", encoding="utf-8"
    )
    assert load_profile(2026, "1") is None


def test_load_profile_explicit_path(tmp_path: Path) -> None:
    target = tmp_path / "custom.json"
    save_profile(UserProfile(name="x"), path=target)
    loaded = load_profile(path=target)
    assert loaded is not None
    assert loaded.name == "x"


def test_save_load_roundtrip_per_semester(isolated_root: Path) -> None:
    p = UserProfile(student_id="20240001", name="길동", grade=2)
    target = save_profile(p, year=2026, semester="1")
    assert target.name == "snapshot_2026_1.json"

    loaded = load_profile(year=2026, semester="1")
    assert loaded is not None
    assert loaded.student_id == "20240001"


def test_save_profile_preserves_fetched_at_and_academic_data(
    isolated_root: Path,
) -> None:
    """프로필 수정(save_profile)이 수강이력 신선도/데이터를 보존해야 한다."""
    basic = BasicInfo(year=2023, grade=3, semester=5, department="컴퓨터학부")
    cache = SnapshotCache(
        year=2026,
        semester="1",
        profile=UserProfile.from_basic_info(basic),
        basicInfo=basic,
        takenCourses=[TakenCourse(year=2025, semester="1", subjectCodes=["CSE1234"])],
        lowGradeSubjectCodes=["CSE1234"],
        subjectNames={"CSE1234": "자료구조"},
        fetched_at=datetime.now(timezone.utc),
    )
    save_snapshot_cache(cache)

    # 프로필만 부분 수정
    save_profile(cache.profile.model_copy(update={"student_id": "20240001"}), year=2026, semester="1")

    loaded, fetched_at = load_snapshot_cache(2026, "1")
    assert loaded is not None
    assert loaded.profile.student_id == "20240001"
    assert loaded.takenCourses[0].subjectCodes == ["CSE1234"]
    assert loaded.lowGradeSubjectCodes == ["CSE1234"]
    assert fetched_at is not None  # 수강이력 신선도 유지


# --- 이전 프로필 파일 마이그레이션 ---


def test_load_falls_back_to_legacy_profile_json(isolated_root: Path) -> None:
    """레거시 profile.json만 있을 때 load 시 자동 마이그레이션 읽기."""
    legacy = isolated_root / "profile.json"
    legacy.write_text(
        json.dumps({"student_id": "20240001", "name": "레거시"}),
        encoding="utf-8",
    )
    assert not resolve_snapshot_path(2026, "1").exists()

    loaded = load_profile(2026, "1")
    assert loaded is not None
    assert loaded.student_id == "20240001"
    assert loaded.name == "레거시"


def test_load_falls_back_to_legacy_per_semester_profile(isolated_root: Path) -> None:
    """이전 학기별 profile_{year}_{semester}.json 폴백 (SPR-33~45 형식)."""
    legacy = isolated_root / "profile_2026_1.json"
    legacy.write_text(
        json.dumps({"student_id": "20240001", "name": "학기별"}),
        encoding="utf-8",
    )
    loaded = load_profile(2026, "1")
    assert loaded is not None
    assert loaded.student_id == "20240001"
    assert loaded.name == "학기별"


def test_save_to_snapshot_removes_legacy_files(
    isolated_root: Path, fixed_period: tuple[int, str]
) -> None:
    """스냅샷으로 저장하면 이전 profile.json/profile_{year}_{semester}.json 제거."""
    legacy_root = isolated_root / "profile.json"
    legacy_root.write_text(json.dumps({"name": "레거시"}), encoding="utf-8")
    legacy_per = isolated_root / "profile_2026_1.json"
    legacy_per.write_text(json.dumps({"name": "학기별"}), encoding="utf-8")

    save_profile(UserProfile(name="새이름"))
    assert not legacy_root.exists()
    assert not legacy_per.exists()
    assert (isolated_root / "snapshot_2026_1.json").exists()


def test_save_with_explicit_path_keeps_legacy(tmp_path: Path) -> None:
    """path 인자로 명시 저장 시 레거시 제거 로직 건너뜀 (best-effort)."""
    legacy = tmp_path / "profile.json"
    legacy.write_text(json.dumps({"name": "레거시"}), encoding="utf-8")
    custom = tmp_path / "custom.json"
    save_profile(UserProfile(name="x"), path=custom)
    assert legacy.exists()
