"""UserProfile 스키마 + 매핑 테스트 (파일 I/O는 test_snapshot_cache.py 참고)."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from soongpt_mcp.profile import (
    SUBMISSION_FIELDS,
    UserProfile,
)


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


def test_default_updated_at() -> None:
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


def test_from_basic_info_extracts_college() -> None:
    """SPR-55: BasicInfo에서 college(단과대) 추출."""
    basic = {
        "year": 2023,
        "grade": 3,
        "semester": 5,
        "department": "컴퓨터학부",
        "college": "공과대학",
    }
    p = UserProfile.from_basic_info(basic)
    assert p.college == "공과대학"


def test_from_basic_info_college_defaults_none_when_absent() -> None:
    """BasicInfo에 college 키가 없으면 None (USAINT 미추출 시)."""
    p = UserProfile.from_basic_info(
        {"year": 2024, "grade": 1, "semester": 1, "department": "컴퓨터학부"}
    )
    assert p.college is None


def test_from_basic_info_college_from_pydantic_model() -> None:
    """BasicInfo(pydantic)에서 college 매핑."""
    from soongpt_mcp.schemas.usaint_schemas import BasicInfo

    basic = BasicInfo(
        year=2024,
        grade=2,
        semester=3,
        department="소프트웨어학부",
        college="IT대학",
    )
    p = UserProfile.from_basic_info(basic)
    assert p.college == "IT대학"


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
