"""
유세인트에서 데이터를 조회하는 함수들.

기본 정보, 수강/성적, 복수전공·교직, 졸업 요건 등을 가져옵니다.
"""

import asyncio
import logging
from typing import Any, Dict

import rusaint

from soongpt_mcp._config_compat import settings
from soongpt_mcp.services.constants import CHAPEL_CODES, RETAKE_GENERAL_REQUIRED_MAPPING
from soongpt_mcp.schemas.usaint_schemas import (
    BasicInfo,
    Flags,
    GraduationRequirementItem,
    GraduationRequirements,
    SubjectItem,
    TakenCourse,
)

logger = logging.getLogger(__name__)


def _clean_optional_text(value: Any) -> str | None:
    """Optional 문자열 정규화 — None 또는 공백이면 None."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _clean_optional_major(value: Any) -> str | None:
    """복수/연계/융합 전공 문자열 정규화 — None 또는 공백이면 None."""
    return _clean_optional_text(value)


async def fetch_basic_info(student_info_app) -> tuple[BasicInfo, list[str]]:
    """
    기본 학적 정보를 조회합니다.

    **민감 정보는 조회하지 않음**: 이름, 주민번호, 주소, 전화번호 등은 가져오지 않습니다.
    **휴학/엇학기/졸업유예 고려**: 계산이 아니라 유세인트에서 직접 크롤링

    동일한 student_info_app 세션에서 general()과 qualifications()를 호출하므로
    세션 1개로 주전공/복수/연계/부전공 + 교직 정보까지 한 번에 얻습니다.

    Returns:
        tuple: (BasicInfo, warnings) — warnings에 NO_SEMESTER_INFO가 포함될 수 있음.
            qualifications() 조회 자체가 실패하면 교직 필드는 기본값(False/None).
    """
    try:
        warnings: list[str] = []
        student_info = await student_info_app.general()

        admission_year = getattr(student_info, "apply_year", None) or getattr(
            student_info, "admission_year", None
        )
        if admission_year is None:
            logger.error("입학년도 정보를 찾을 수 없습니다")
            raise ValueError("필수 학적 정보(입학년도)를 조회할 수 없습니다")

        grade = getattr(student_info, "grade", None)
        if grade is None:
            logger.error("학년 정보를 찾을 수 없습니다")
            raise ValueError("필수 학적 정보(학년)를 조회할 수 없습니다")

        term_raw = getattr(student_info, "term", None) or getattr(
            student_info, "semester", None
        )
        if term_raw is None:
            logger.warning(
                "학기 정보가 없어 기본값(1학기) 사용. "
                "새내기일 가능성이 높지만, 데이터 누락일 수도 있음"
            )
            warnings.append("NO_SEMESTER_INFO")
            term_raw = 1
        if 1 <= term_raw <= 2:
            semester = (grade - 1) * 2 + term_raw
        else:
            semester = term_raw
        semester = max(1, min(8, semester))

        # TODO(PT-87): 2025년 3월 이후 삭제 예정 - 숭피티 출시 전까지 다음 학기 추천을 위한 임시 +1학기 보정
        if semester > 1:
            semester = min(8, semester + 1)
        grade = min(4, (semester - 1) // 2 + 1)

        department = getattr(student_info, "major", None) or getattr(
            student_info, "department", None
        )
        if not department:
            logger.error("학과 정보를 찾을 수 없습니다")
            raise ValueError("필수 학적 정보(학과)를 조회할 수 없습니다")

        # SPR-55: 단과대 추출 (rusaint.collage — USAINT에서 제공됨, Optional)
        college = _clean_optional_text(getattr(student_info, "collage", None))

        # SPR-35: 복수전공/연계·융합전공/부전공 추출 (Optional, 없으면 None)
        double_major = _clean_optional_major(getattr(student_info, "plural_major", None))
        connected_major = _clean_optional_major(
            getattr(student_info, "connected_major", None)
        )
        minor = _clean_optional_major(getattr(student_info, "sub_major", None))

        # SPR-36: 교직 이수 정보 (qualifications에서만 추출 가능)
        teaching_certification = False
        teaching_major: str | None = None
        try:
            qualifications = await student_info_app.qualifications()
            if qualifications.teaching_major:
                teaching_major = _clean_optional_major(
                    qualifications.teaching_major.major_name
                )
                teaching_certification = teaching_major is not None
        except Exception as e:
            logger.warning(
                f"교직 정보 조회 실패 (선택 정보): {type(e).__name__}"
            )

        return BasicInfo(
            year=admission_year,
            grade=grade,
            semester=semester,
            department=department,
            college=college,
            double_major=double_major,
            connected_major=connected_major,
            minor=minor,
            teaching_certification=teaching_certification,
            teaching_major=teaching_major,
        ), warnings
    except ValueError:
        raise
    except Exception as e:
        logger.error(f"기본 학적 정보 조회 실패: {type(e).__name__}")
        raise


async def fetch_all_course_data_parallel(
    course_grades_app1,
    course_grades_app2,
    semester_type_map: Dict[Any, str],
) -> tuple[list[TakenCourse], list[str], list[str]]:
    """
    2개의 CourseGradesApplication으로 학기를 나눠서 병렬 조회합니다.

    반환: (taken_courses, low_grade_codes, warnings).
    taken_courses의 각 subjects에는 과목 코드와 강의명(class_name)이 인라인으로
    들어감 — 응답 소비측이 별도 사전 join 없이 바로 해석. 강의명이 없는 과목은
    name=None. (과목명 전체 사전은 응답 파생 단 server._format_snapshot_response에서
    subjects로부터 재구성됨.)

    code_to_name(내부 로컬 변수)은 이 함수에서만 lowGrade 대체과목(교양필수
    구→신과목) 매핑에 사용되며 반환되지 않음.
    """
    try:
        semesters = await course_grades_app1.semesters(rusaint.CourseType.BACHELOR)

        if not semesters:
            logger.warning("수강 이력 없음 (빈 학기 목록) — 새내기 가능성")
            return [], [], ["NO_COURSE_HISTORY"]

        if len(semesters) <= 1:
            semesters_group1 = semesters
            semesters_group2 = []
        else:
            mid_point = (len(semesters) + 1) // 2
            semesters_group1 = semesters[:mid_point]
            semesters_group2 = semesters[mid_point:]

        tasks_group1 = [
            course_grades_app1.classes(
                rusaint.CourseType.BACHELOR,
                sem.year,
                sem.semester,
                include_details=False,
            )
            for sem in semesters_group1
        ] if semesters_group1 else []

        tasks_group2 = [
            course_grades_app2.classes(
                rusaint.CourseType.BACHELOR,
                sem.year,
                sem.semester,
                include_details=False,
            )
            for sem in semesters_group2
        ] if semesters_group2 else []

        if tasks_group1 and tasks_group2:
            classes_group1, classes_group2 = await asyncio.gather(
                asyncio.gather(*tasks_group1),
                asyncio.gather(*tasks_group2),
            )
        elif tasks_group1:
            classes_group1 = await asyncio.gather(*tasks_group1)
            classes_group2 = []
        else:
            classes_group1 = []
            classes_group2 = await asyncio.gather(*tasks_group2)

        all_semester_classes = list(classes_group1) + list(classes_group2)

        taken_courses = []
        latest_grades: Dict[str, tuple[int, int, str]] = {}
        # code_to_name은 lowGrade 대체과목(교양필수 구→신과목) 매핑에만 쓰이는
        # 내부 로컬 변수. 응답에는 노출되지 않으며, 아래 classes() 순회에서
        # subjects.name과 동일한 정규화로 생성되어 항상 동기화됨(invariant).
        code_to_name: Dict[str, str] = {}

        for idx, (semester_grade, classes) in enumerate(zip(semesters, all_semester_classes)):
            semester_str = semester_type_map.get(semester_grade.semester, "1")

            # invariant: subjects(출력)와 code_to_name(내부)은 이 한 번의 classes()
            # 순회에서 동일한 정규화(class_name or None)로 생성되어 동기화됨.
            subjects: list[SubjectItem] = []
            for cls in classes:
                code = cls.code
                if code in CHAPEL_CODES:
                    continue

                name = getattr(cls, "class_name", None) or None
                subjects.append(SubjectItem(code=code, name=name))
                if name:
                    code_to_name[code] = name

                rank = getattr(cls, "rank", None)
                if not rank:
                    continue

                rank_str = str(rank).upper().strip()

                if code not in latest_grades:
                    latest_grades[code] = (semester_grade.year, idx, rank_str)
                else:
                    prev_year, prev_idx, _ = latest_grades[code]
                    if (semester_grade.year, idx) > (prev_year, prev_idx):
                        latest_grades[code] = (semester_grade.year, idx, rank_str)

            taken_courses.append(
                TakenCourse(
                    year=semester_grade.year,
                    semester=semester_str,
                    subjects=subjects,
                )
            )

        low_grade_codes = []
        for code, (_, _, rank_str) in latest_grades.items():
            if rank_str == settings.FAIL_GRADE or rank_str in settings.LOW_GRADE_RANKS:
                low_grade_codes.append(code)

        low_grade_code_set = set(low_grade_codes)
        for code in list(low_grade_codes):
            name = code_to_name.get(code, "")
            replacement_code = RETAKE_GENERAL_REQUIRED_MAPPING.get(name)
            if replacement_code and replacement_code not in low_grade_code_set:
                low_grade_codes.append(replacement_code)
                low_grade_code_set.add(replacement_code)

        return taken_courses, low_grade_codes, []

    except Exception as e:
        logger.error(f"성적 관련 데이터 조회 실패 (병렬): {type(e).__name__}")
        raise


async def fetch_flags(student_info_app) -> Flags:
    """
    복수전공/부전공 및 교직 이수 정보를 조회합니다.

    **학과명만 조회**: 자격증 번호, 날짜 등 민감정보는 제외
    """
    try:
        qualifications = await student_info_app.qualifications()

        teaching = False
        teaching_major: str | None = None
        if qualifications.teaching_major:
            teaching_info = qualifications.teaching_major
            teaching_major = teaching_info.major_name
            teaching = teaching_major is not None

        student_info = await student_info_app.general()

        double_major = None
        minor = None

        for attr in ["plural_major", "second_major", "double_major", "dual_major", "major_double"]:
            if hasattr(student_info, attr):
                value = getattr(student_info, attr)
                if value and str(value).strip():
                    double_major = value
                    break

        for attr in ["sub_major", "minor", "minor_major", "submajor"]:
            if hasattr(student_info, attr):
                value = getattr(student_info, attr)
                if value and str(value).strip():
                    minor = value
                    break

        return Flags(
            doubleMajorDepartment=double_major,
            minorDepartment=minor,
            teaching=teaching,
            teachingMajor=teaching_major,
        )
    except Exception as e:
        logger.warning(f"복수전공/교직 정보 조회 실패 (선택 정보): {type(e).__name__}")
        return Flags(
            doubleMajorDepartment=None,
            minorDepartment=None,
            teaching=False,
            teachingMajor=None,
        )


async def fetch_graduation_requirements(grad_app) -> GraduationRequirements:
    """
    졸업 요건 상세 정보를 조회합니다 (raw 데이터).

    **개별 요건 정보 포함**: 각 요건의 이름, 기준학점, 이수학점, 충족여부 등
    """
    try:
        requirements = await grad_app.requirements()
        requirement_list = []

        if isinstance(requirements.requirements, dict):
            for key, req in requirements.requirements.items():
                name = str(key)
                requirement_value = getattr(req, "requirement", None)
                calculation_value = getattr(req, "calculation", None) or getattr(
                    req, "calcuation", None
                )
                difference_value = getattr(req, "difference", None)
                result_value = getattr(req, "result", False)
                category = getattr(req, "category", str(key))

                requirement_list.append(
                    GraduationRequirementItem(
                        name=name,
                        requirement=requirement_value,
                        calculation=calculation_value,
                        difference=difference_value,
                        result=result_value,
                        category=category,
                    )
                )

        return GraduationRequirements(requirements=requirement_list)

    except Exception as e:
        logger.error(f"졸업 요건 조회 실패: {type(e).__name__}")
        raise
