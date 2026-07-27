"""UserProfile 스키마 + 로드/저장/매핑 테스트."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

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
