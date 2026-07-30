"""UserProfile 스키마 + 로드/저장/매핑 테스트."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from soongpt_mcp import profile as profile_mod
from soongpt_mcp.profile import (
    SUBMISSION_FIELDS,
    UserProfile,
    load_profile,
    save_profile,
)


@pytest.fixture
def isolated_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """resolve_profile_path가 tmp_path/profile.json을 가리키도록 격리."""
    target = tmp_path / "profile.json"
    monkeypatch.setattr("soongpt_mcp.profile.resolve_profile_path", lambda: target)
    return target


@pytest.fixture
def isolated_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """CLAUDE_PLUGIN_DATA 대신 tmp_path를 루트로 사용. 학기별 파일명 그대로 검증."""
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path))
    return tmp_path


def test_grade_within_1_to_6() -> None:
    with pytest.raises(ValidationError):
        UserProfile(grade=0)
    with pytest.raises(ValidationError):
        UserProfile(grade=7)
    assert UserProfile(grade=1).grade == 1
    assert UserProfile(grade=6).grade == 6


def test_grade_none_allowed() -> None:
    p = UserProfile()
    assert p.grade is None


def test_student_id_numeric_only() -> None:
    with pytest.raises(ValidationError):
        UserProfile(student_id="20240001a")
    assert UserProfile(student_id="20240001").student_id == "20240001"


def test_student_id_strips_whitespace() -> None:
    assert UserProfile(student_id="  20240001  ").student_id == "20240001"


def test_empty_string_becomes_none() -> None:
    p = UserProfile(name="   ", college="", department="IT")
    assert p.name is None
    assert p.college is None
    assert p.department == "IT"


def test_default_updated_at(isolated_path: Path) -> None:
    p = UserProfile()
    assert p.updated_at is not None


def test_extra_field_forbidden() -> None:
    with pytest.raises(ValidationError):
        UserProfile(unknown_field="x")


def test_from_basic_info_dict() -> None:
    basic = {"year": 2023, "grade": 3, "semester": 5, "department": "컴퓨터학부"}
    p = UserProfile.from_basic_info(basic)
    assert p.department == "컴퓨터학부"
    assert p.grade == 3
    assert p.entered_year == 2023
    assert p.student_id is None
    assert p.name is None


def test_from_basic_info_pydantic_model() -> None:
    from soongpt_mcp.schemas.usaint_schemas import BasicInfo

    basic = BasicInfo(year=2024, grade=2, semester=3, department="소프트웨어학부")
    p = UserProfile.from_basic_info(basic)
    assert p.department == "소프트웨어학부"
    assert p.grade == 2
    assert p.entered_year == 2024


def test_from_basic_info_rejects_unknown_type() -> None:
    with pytest.raises(TypeError):
        UserProfile.from_basic_info(42)  # type: ignore[arg-type]


def test_from_basic_info_partial_dict() -> None:
    """일부 키만 있어도 None으로 채워 매핑."""
    p = UserProfile.from_basic_info({"year": 2024})
    assert p.entered_year == 2024
    assert p.department is None
    assert p.grade is None
    assert p.teaching_certification is False
    assert p.teaching_major is None


def test_from_basic_info_extracts_teaching_fields() -> None:
    """BasicInfo의 teaching_certification/teaching_major 매핑."""
    basic = {
        "year": 2024,
        "grade": 2,
        "semester": 3,
        "department": "컴퓨터학부",
        "teaching_certification": True,
        "teaching_major": "컴퓨터교육",
    }
    p = UserProfile.from_basic_info(basic)
    assert p.teaching_certification is True
    assert p.teaching_major == "컴퓨터교육"


def test_from_basic_info_teaching_defaults_when_absent() -> None:
    """BasicInfo에 teaching 필드 없으면 기본값."""
    basic = {"year": 2024, "grade": 2, "semester": 3, "department": "컴퓨터학부"}
    p = UserProfile.from_basic_info(basic)
    assert p.teaching_certification is False
    assert p.teaching_major is None


def test_teaching_certification_default_false() -> None:
    assert UserProfile().teaching_certification is False


def test_teaching_major_empty_string_becomes_none() -> None:
    p = UserProfile(teaching_major="   ")
    assert p.teaching_major is None


def test_apply_partial_update_sets_teaching_fields() -> None:
    p = UserProfile()
    updated = p.apply_partial_update(
        {"teaching_certification": True, "teaching_major": "컴퓨터교육"}
    )
    assert updated.teaching_certification is True
    assert updated.teaching_major == "컴퓨터교육"
    assert p.teaching_certification is False


def test_apply_partial_update_empty_string_clears_teaching_major() -> None:
    p = UserProfile(teaching_major="컴퓨터교육")
    updated = p.apply_partial_update({"teaching_major": "  "})
    assert updated.teaching_major is None


def test_from_basic_info_extracts_double_and_connected_major() -> None:
    """SPR-35: BasicInfo에서 double_major / connected_major / minor 추출."""
    basic = {
        "year": 2023,
        "grade": 3,
        "semester": 5,
        "department": "컴퓨터학부",
        "double_major": "경영학과",
        "connected_major": "AI·소프트웨어융합",
        "minor": "철학과",
    }
    p = UserProfile.from_basic_info(basic)
    assert p.double_major == "경영학과"
    assert p.connected_major == "AI·소프트웨어융합"
    assert p.minor == "철학과"


def test_from_basic_info_majors_default_none() -> None:
    """복수/연계/부전공 키가 없으면 None."""
    p = UserProfile.from_basic_info(
        {"year": 2024, "grade": 1, "semester": 1, "department": "컴퓨터학부"}
    )
    assert p.double_major is None
    assert p.connected_major is None
    assert p.minor is None


def test_apply_partial_update_strips_double_major_whitespace() -> None:
    p = UserProfile()
    updated = p.apply_partial_update({"double_major": "  경영학과  "})
    assert updated.double_major == "경영학과"


def test_apply_partial_update_empty_string_clears_majors() -> None:
    p = UserProfile(double_major="경영학과", connected_major="연계", minor="철학")
    updated = p.apply_partial_update(
        {"double_major": "   ", "connected_major": "", "minor": "  "}
    )
    assert updated.double_major is None
    assert updated.connected_major is None
    assert updated.minor is None


def test_apply_partial_update_rejects_unknown_field() -> None:
    p = UserProfile()
    with pytest.raises(ValueError, match="알 수 없는 프로필 필드"):
        p.apply_partial_update({"unknown": "x"})


def test_apply_partial_update_sets_single_field() -> None:
    p = UserProfile()
    updated = p.apply_partial_update({"name": "홍길동"})
    assert updated.name == "홍길동"
    assert p.name is None


def test_apply_partial_update_preserves_other_fields() -> None:
    p = UserProfile(name="원본", college="IT")
    updated = p.apply_partial_update({"grade": 3})
    assert updated.name == "원본"
    assert updated.college == "IT"
    assert updated.grade == 3


def test_apply_partial_update_empty_string_clears_field() -> None:
    p = UserProfile(name="원본")
    updated = p.apply_partial_update({"name": "  "})
    assert updated.name is None


def test_apply_partial_update_validates_grade() -> None:
    p = UserProfile()
    with pytest.raises(ValidationError):
        p.apply_partial_update({"grade": 99})


def test_apply_partial_update_coerces_numeric_string() -> None:
    """MCP 도구는 value: Any로 받아 LLM이 "3" 문자열을 줄 수 있음.

    pydantic v2 model_validate가 str→int 변환을 시도하므로 정수로 저장되어야 함.
    """
    p = UserProfile()
    updated = p.apply_partial_update({"grade": "3", "entered_year": "2024"})
    assert updated.grade == 3
    assert updated.entered_year == 2024


def test_apply_partial_update_rejects_non_numeric_grade() -> None:
    p = UserProfile()
    with pytest.raises(ValidationError):
        p.apply_partial_update({"grade": "3학년"})


def test_submission_fields_complete() -> None:
    assert SUBMISSION_FIELDS == frozenset(
        {
            "student_id",
            "name",
            "college",
            "department",
            "grade",
            "track",
            "entered_year",
            "double_major",
            "connected_major",
            "minor",
            "teaching_certification",
            "teaching_major",
        }
    )


def test_load_profile_missing_file_returns_none(isolated_path: Path) -> None:
    assert load_profile() is None


def test_save_then_load_roundtrip(isolated_path: Path) -> None:
    p = UserProfile(
        student_id="20240001",
        name="홍길동",
        college="IT대학",
        department="컴퓨터학부",
        grade=3,
        entered_year=2024,
    )
    save_profile(p)
    assert isolated_path.exists()

    loaded = load_profile()
    assert loaded is not None
    assert loaded.student_id == "20240001"
    assert loaded.name == "홍길동"
    assert loaded.department == "컴퓨터학부"
    assert loaded.grade == 3
    assert loaded.entered_year == 2024


def test_save_creates_parent_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    nested = tmp_path / "nested" / "deep" / "profile.json"
    monkeypatch.setattr("soongpt_mcp.profile.resolve_profile_path", lambda: nested)
    save_profile(UserProfile(name="x"))
    assert nested.exists()


def test_load_profile_handles_corrupted_file(isolated_path: Path) -> None:
    isolated_path.write_text("not json {{{", encoding="utf-8")
    assert load_profile() is None


def test_load_profile_handles_schema_violation(isolated_path: Path) -> None:
    isolated_path.write_text(
        json.dumps({"grade": 99, "name": "x"}), encoding="utf-8"
    )
    assert load_profile() is None


def test_load_profile_explicit_path(tmp_path: Path) -> None:
    target = tmp_path / "custom.json"
    save_profile(UserProfile(name="x"), path=target)
    loaded = load_profile(path=target)
    assert loaded is not None
    assert loaded.name == "x"


def test_resolve_profile_path_uses_current_semester_by_default(
    isolated_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """현재 학기(2026-07 → 2026_1) 경로를 기본으로 반환."""
    monkeypatch.setattr(
        "soongpt_mcp.profile.current_academic_period",
        lambda: (2026, "1"),
    )
    path = profile_mod.resolve_profile_path()
    assert path == isolated_root / "profile_2026_1.json"


def test_resolve_profile_path_accepts_explicit_year_semester(
    isolated_root: Path,
) -> None:
    path = profile_mod.resolve_profile_path(year=2025, semester="2")
    assert path == isolated_root / "profile_2025_2.json"


def test_save_load_roundtrip_per_semester(isolated_root: Path) -> None:
    p = UserProfile(student_id="20240001", name="길동", grade=2)
    target = save_profile(p, path=profile_mod.resolve_profile_path(2026, "1"))
    assert target.name == "profile_2026_1.json"

    loaded = load_profile(profile_mod.resolve_profile_path(2026, "1"))
    assert loaded is not None
    assert loaded.student_id == "20240001"


def test_load_falls_back_to_legacy_profile_json(isolated_root: Path) -> None:
    """레거시 profile.json만 있을 때 load 시 자동 마이그레이션 읽기."""
    legacy = isolated_root / "profile.json"
    legacy.write_text(
        json.dumps({"student_id": "20240001", "name": "레거시"}),
        encoding="utf-8",
    )
    # 현재 학기 파일은 없음
    assert not profile_mod.resolve_profile_path(2026, "1").exists()

    loaded = load_profile(profile_mod.resolve_profile_path(2026, "1"))
    assert loaded is not None
    assert loaded.student_id == "20240001"
    assert loaded.name == "레거시"


def test_save_to_new_path_removes_legacy(
    isolated_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """기본 경로로 save하면 레거시 profile.json이 제거됨 (마이그레이션 완료)."""
    monkeypatch.setattr(
        "soongpt_mcp.profile.current_academic_period",
        lambda: (2026, "1"),
    )
    legacy = isolated_root / "profile.json"
    legacy.write_text(json.dumps({"name": "레거시"}), encoding="utf-8")
    save_profile(UserProfile(name="새이름"))
    assert not legacy.exists()
    assert profile_mod.resolve_profile_path(2026, "1").exists()


def test_save_with_explicit_path_keeps_legacy(tmp_path: Path) -> None:
    """path 인자로 명시 저장 시 레거시 제거 로직 건너뜀 (best-effort)."""
    legacy = tmp_path / "profile.json"
    legacy.write_text(json.dumps({"name": "레거시"}), encoding="utf-8")
    custom = tmp_path / "custom.json"
    save_profile(UserProfile(name="x"), path=custom)
    # 명시 path 저장은 레거시 정리 안 함
    assert legacy.exists()
