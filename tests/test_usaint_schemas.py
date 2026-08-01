"""usaint_schemas 스키마 검증 (SPR-47: subjects 인라인 구조).

TakenCourse.subjects(코드+강의명 인라인)와 SubjectItem, 그리고
UsaintSnapshotResponse에서 subjectNames가 제거되었는지 검증한다.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from soongpt_mcp.schemas.usaint_schemas import (
    BasicInfo,
    SubjectItem,
    TakenCourse,
    UsaintSnapshotResponse,
)


def _basic_info() -> BasicInfo:
    return BasicInfo(year=2022, grade=3, semester=5, department="컴퓨터학부")


# --- SubjectItem ---


def test_subject_item_requires_code() -> None:
    with pytest.raises(ValidationError):
        SubjectItem(name="자료구조")


def test_subject_item_name_defaults_to_none() -> None:
    item = SubjectItem(code="21012345")
    assert item.code == "21012345"
    assert item.name is None


def test_subject_item_round_trip() -> None:
    item = SubjectItem(code="21012345", name="자료구조")
    dumped = item.model_dump(mode="json")
    assert dumped == {"code": "21012345", "name": "자료구조"}


def test_subject_item_forbids_extra_fields() -> None:
    with pytest.raises(ValidationError):
        SubjectItem(code="21012345", name="자료구조", unexpected="x")


# --- TakenCourse.subjects ---


def test_taken_course_subjects_default_empty() -> None:
    course = TakenCourse(year=2024, semester="1")
    assert course.subjects == []


def test_taken_course_holds_subject_items_inline() -> None:
    course = TakenCourse(
        year=2024,
        semester="1",
        subjects=[
            SubjectItem(code="21012345", name="자료구조"),
            SubjectItem(code="21012346", name=None),
        ],
    )
    dumped = course.model_dump(mode="json")
    assert dumped["subjects"] == [
        {"code": "21012345", "name": "자료구조"},
        {"code": "21012346", "name": None},
    ]


def test_taken_course_forbids_extra_fields() -> None:
    with pytest.raises(ValidationError):
        TakenCourse(year=2024, semester="1", unknown="x")


# --- UsaintSnapshotResponse (subjectNames 제거) ---


def test_response_no_longer_has_subject_names_field() -> None:
    """subjectNames는 응답 파생 필드로 스키마에서 제거됨 (SPR-47)."""
    assert "subjectNames" not in UsaintSnapshotResponse.model_fields


def test_response_round_trip_without_subject_names() -> None:
    response = UsaintSnapshotResponse(
        takenCourses=[
            TakenCourse(
                year=2024,
                semester="1",
                subjects=[SubjectItem(code="21012345", name="자료구조")],
            )
        ],
        lowGradeSubjectCodes=["21012345"],
        basicInfo=_basic_info(),
    )
    dumped = response.model_dump(mode="json")
    assert "subjectNames" not in dumped
    assert dumped["takenCourses"][0]["subjects"] == [
        {"code": "21012345", "name": "자료구조"}
    ]
