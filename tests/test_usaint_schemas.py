"""usaint_schemas 스키마 검증 (SPR-40: subjectNames 필드)."""
from __future__ import annotations

from soongpt_mcp.schemas.usaint_schemas import (
    BasicInfo,
    TakenCourse,
    UsaintSnapshotResponse,
)


def _basic_info() -> BasicInfo:
    return BasicInfo(year=2022, grade=3, semester=5, department="컴퓨터학부")


def test_subject_names_defaults_to_empty_dict() -> None:
    response = UsaintSnapshotResponse(basicInfo=_basic_info())
    assert response.subjectNames == {}


def test_subject_names_round_trips_through_model_dump() -> None:
    response = UsaintSnapshotResponse(
        takenCourses=[TakenCourse(year=2024, semester="1", subjectCodes=["21012345"])],
        lowGradeSubjectCodes=["21012345"],
        subjectNames={"21012345": "자료구조"},
        basicInfo=_basic_info(),
    )
    dumped = response.model_dump(mode="json")
    assert dumped["subjectNames"] == {"21012345": "자료구조"}
