"""수강 과목 subjects(코드+강의명 인라인) 매핑 테스트 (SPR-47).

fetch_all_course_data_parallel이 taken_courses.subjects에 rusaint classes()
응답의 class_name을 {code, name}으로 인라인 채우는지, 채플이 제외되는지,
빈 class_name이 None으로 정규화되는지, subjects가 내부 code_to_name과
동기화되는지(invariant)를 검증한다.
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


def _subject_map(taken_courses) -> dict[str, str]:
    """taken_courses에서 {code: name} 파생 (name이 None이면 제외).

    server._derive_subject_names와 동일한 규칙 — 이 매핑이 곧 내부
    code_to_name(동일한 classes() 순회에서 생성)과 동등해야 함(invariant).
    """
    out: dict[str, str] = {}
    for course in taken_courses:
        for s in course.subjects:
            if s.name:
                out[s.code] = s.name
    return out


async def test_subjects_inline_taken_courses_only() -> None:
    """실제 수강한 과목만 subjects에 인라인 채워지고 채플은 제외됨."""
    chapel_code = next(iter(CHAPEL_CODES))
    sem = _FakeSemester(2024, rusaint.SemesterType.ONE)
    classes = {
        (2024, rusaint.SemesterType.ONE): [
            _FakeClass("21012345", "자료구조", rank="A+"),
            _FakeClass(chapel_code, "채플", rank=None),
        ],
    }
    app = _FakeCourseGradesApp([sem], classes)

    taken_courses, low_grade_codes, warnings = (
        await fetchers.fetch_all_course_data_parallel(app, app, SEMESTER_TYPE_MAP)
    )

    assert warnings == []
    codes = [s.code for s in taken_courses[0].subjects]
    assert codes == ["21012345"]
    assert taken_courses[0].subjects[0].name == "자료구조"
    assert low_grade_codes == []


async def test_no_course_history_returns_empty_subjects() -> None:
    """빈 학기 목록(새내기)이면 subjects도 빈 채로 3-tuple 반환."""

    class _EmptySemestersApp:
        async def semesters(self, course_type):
            return []

        async def classes(self, *args, **kwargs):
            raise AssertionError("classes()는 호출되지 않아야 함")

    app = _EmptySemestersApp()

    taken_courses, low_grade_codes, warnings = (
        await fetchers.fetch_all_course_data_parallel(app, app, SEMESTER_TYPE_MAP)
    )

    assert taken_courses == []
    assert low_grade_codes == []
    assert warnings == ["NO_COURSE_HISTORY"]


async def test_retake_replacement_code_absent_from_subjects() -> None:
    """재수강 대체과목 추천 코드는 실제 수강 이력이 없어 subjects에 없음 —
    호출측이 subjectNames.get(code, code)로 코드 자체를 폴백해야 함을 검증."""
    sem = _FakeSemester(2021, rusaint.SemesterType.ONE)
    classes = {
        (2021, rusaint.SemesterType.ONE): [
            _FakeClass("21099999", "독서와토론", rank="F"),
        ],
    }
    app = _FakeCourseGradesApp([sem], classes)

    taken_courses, low_grade_codes, _ = (
        await fetchers.fetch_all_course_data_parallel(app, app, SEMESTER_TYPE_MAP)
    )

    subject_map = _subject_map(taken_courses)
    assert "21099999" in low_grade_codes
    assert "21501003" in low_grade_codes  # RETAKE_GENERAL_REQUIRED_MAPPING 대체 코드
    assert subject_map == {"21099999": "독서와토론"}
    assert "21501003" not in subject_map  # 대체코드는 subjects에 없음


async def test_empty_class_name_normalized_to_none() -> None:
    """rusaint class_name이 빈 문자열이면 name=None으로 정규화."""
    sem = _FakeSemester(2024, rusaint.SemesterType.ONE)
    classes = {
        (2024, rusaint.SemesterType.ONE): [
            _FakeClass("21012345", "", rank="A+"),
        ],
    }
    app = _FakeCourseGradesApp([sem], classes)

    taken_courses, _, _ = (
        await fetchers.fetch_all_course_data_parallel(app, app, SEMESTER_TYPE_MAP)
    )

    assert taken_courses[0].subjects[0].code == "21012345"
    assert taken_courses[0].subjects[0].name is None


async def test_subjects_consistent_across_retake_semesters() -> None:
    """invariant: 같은 과목 코드가 여러 학기에 재수강으로 나와도 subjects의 name은
    일관되며, name non-None 파생 사전은 단일 항목으로 수렴한다 (내부 code_to_name과
    동일한 classes() 순회에서 생성되므로 동기화됨)."""
    sem1 = _FakeSemester(2021, rusaint.SemesterType.ONE)
    sem2 = _FakeSemester(2024, rusaint.SemesterType.ONE)
    classes = {
        (2021, rusaint.SemesterType.ONE): [_FakeClass("21012345", "자료구조", rank="D")],
        (2024, rusaint.SemesterType.ONE): [_FakeClass("21012345", "자료구조", rank="A+")],
    }
    app = _FakeCourseGradesApp([sem1, sem2], classes)

    taken_courses, _, _ = (
        await fetchers.fetch_all_course_data_parallel(app, app, SEMESTER_TYPE_MAP)
    )

    # 두 학기 모두 동일 (코드, 이름)
    for course in taken_courses:
        assert [(s.code, s.name) for s in course.subjects] == [("21012345", "자료구조")]
    # name non-None 파생 사전은 단일 항목으로 수렴 (동기화/일관성)
    assert _subject_map(taken_courses) == {"21012345": "자료구조"}
