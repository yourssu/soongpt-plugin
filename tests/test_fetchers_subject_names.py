"""과목 코드 → 강의명 매핑 테스트 (SPR-40).

fetch_all_course_data_parallel이 rusaint classes() 응답의 class_name을
code_to_name 매핑으로 노출하는지, 채플/재수강 대체과목 코드는 제외되는지 검증.
"""
from __future__ import annotations

import rusaint

from soongpt_mcp.services import fetchers
from soongpt_mcp.services.constants import CHAPEL_CODES, SEMESTER_TYPE_MAP


class _FakeSemester:
    def __init__(self, year: int, semester: rusaint.SemesterType) -> None:
        self.year = year
        self.semester = semester


class _FakeClass:
    def __init__(self, code: str, class_name: str, rank: str | None = None) -> None:
        self.code = code
        self.class_name = class_name
        self.rank = rank


class _FakeCourseGradesApp:
    """semesters()/classes()만 구현한 CourseGradesApplication 더블."""

    def __init__(
        self,
        semesters: list[_FakeSemester],
        classes_by_semester: dict[tuple[int, rusaint.SemesterType], list[_FakeClass]],
    ) -> None:
        self._semesters = semesters
        self._classes_by_semester = classes_by_semester

    async def semesters(self, course_type):
        return self._semesters

    async def classes(self, course_type, year, semester, include_details=False):
        return self._classes_by_semester[(year, semester)]


async def test_code_to_name_includes_taken_courses_only() -> None:
    """실제 수강한 과목만 code_to_name에 채워지고 채플은 제외됨."""
    chapel_code = next(iter(CHAPEL_CODES))
    sem = _FakeSemester(2024, rusaint.SemesterType.ONE)
    classes = {
        (2024, rusaint.SemesterType.ONE): [
            _FakeClass("21012345", "자료구조", rank="A+"),
            _FakeClass(chapel_code, "채플", rank=None),
        ],
    }
    app = _FakeCourseGradesApp([sem], classes)

    taken_courses, low_grade_codes, code_to_name, warnings = (
        await fetchers.fetch_all_course_data_parallel(app, app, SEMESTER_TYPE_MAP)
    )

    assert warnings == []
    assert taken_courses[0].subjectCodes == ["21012345"]
    assert code_to_name == {"21012345": "자료구조"}
    assert low_grade_codes == []


async def test_no_course_history_returns_empty_code_to_name() -> None:
    """빈 학기 목록(새내기)이면 code_to_name도 빈 dict로 반환."""

    class _EmptySemestersApp:
        async def semesters(self, course_type):
            return []

        async def classes(self, *args, **kwargs):
            raise AssertionError("classes()는 호출되지 않아야 함")

    app = _EmptySemestersApp()

    taken_courses, low_grade_codes, code_to_name, warnings = (
        await fetchers.fetch_all_course_data_parallel(app, app, SEMESTER_TYPE_MAP)
    )

    assert taken_courses == []
    assert low_grade_codes == []
    assert code_to_name == {}
    assert warnings == ["NO_COURSE_HISTORY"]


async def test_retake_replacement_code_has_no_name_fallback() -> None:
    """재수강 대체과목 추천 코드는 실제 수강 이력이 없어 code_to_name에 없음 —
    호출측이 subjectNames.get(code, code)로 코드 자체를 폴백해야 함을 검증."""
    sem = _FakeSemester(2021, rusaint.SemesterType.ONE)
    classes = {
        (2021, rusaint.SemesterType.ONE): [
            _FakeClass("21099999", "독서와토론", rank="F"),
        ],
    }
    app = _FakeCourseGradesApp([sem], classes)

    _, low_grade_codes, code_to_name, _ = (
        await fetchers.fetch_all_course_data_parallel(app, app, SEMESTER_TYPE_MAP)
    )

    assert "21099999" in low_grade_codes
    assert "21501003" in low_grade_codes  # RETAKE_GENERAL_REQUIRED_MAPPING 대체 코드
    assert code_to_name == {"21099999": "독서와토론"}
    assert "21501003" not in code_to_name
