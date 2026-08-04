"""
Rusaint 라이브러리를 사용한 유세인트 데이터 크롤링 서비스 (파사드).

- 학적/성적 이력: RusaintAcademicService 위임
- 졸업사정표: RusaintGraduationService 위임
- 전체 스냅샷(학적/수강이력): 이 파일에서 직접 처리 (3세션 병렬)

모든 공개 메서드는 JSON 직렬화된 유세인트 세션(session_json)을 받습니다.
student_id/s_token은 더 이상 사용하지 않습니다.
"""

import asyncio
import logging
import time

import rusaint

from soongpt_mcp.schemas.usaint_schemas import BasicInfo, UsaintSnapshotResponse
from soongpt_mcp.services import fetchers
from soongpt_mcp.services import session as session_module
from soongpt_mcp.services.constants import SEMESTER_TYPE_MAP
from soongpt_mcp.services.exceptions import (
    RusaintConnectionError,
    RusaintInternalError,
    RusaintTimeoutError,
    SSOTokenError,
)
from soongpt_mcp.services.rusaint_academic_service import RusaintAcademicService
from soongpt_mcp.services.rusaint_course_schedule_service import (
    RusaintCourseScheduleService,
)
from soongpt_mcp.services.rusaint_graduation_service import RusaintGraduationService

logger = logging.getLogger(__name__)


class RusaintService:
    """
    유세인트 크롤링 파사드.

    - fetch_usaint_snapshot: 전체 스냅샷 (학적+수강이력, 3세션)
    - fetch_usaint_snapshot_academic: 학적/성적만 → Academic 서비스 위임
    - fetch_usaint_graduation_info: 졸업사정표만 → Graduation 서비스 위임
    - find_lectures: 강의시간표 검색 → CourseSchedule 서비스 위임
    - validate_session: 세션 JSON 유효성 검증
    """

    SEMESTER_TYPE_MAP = SEMESTER_TYPE_MAP

    def __init__(self) -> None:
        self._academic = RusaintAcademicService()
        self._graduation = RusaintGraduationService()
        self._course_schedule = RusaintCourseScheduleService()

    async def fetch_usaint_snapshot(
        self,
        session_json: str,
    ) -> UsaintSnapshotResponse:
        """
        JSON 직렬화 세션으로 유세인트 전체 스냅샷 조회 (학적+수강이력, 3세션 병렬).

        졸업사정표는 이 스냅샷에 포함되지 않는다 — 별도 도구
        get_graduation_status(RusaintGraduationService)가 담당한다. 이전에는 졸업
        세션을 함께 생성했으나 데이터 조회에 쓰이지 않아 병목만 유발해 제거했다.
        """
        start_time = time.time()
        logger.info("유세인트 데이터 조회 시작: [session_json]")

        sessions: list[tuple[str, rusaint.USaintSession | None]] = []

        try:
            session_start = time.time()
            session_course1, session_course2, session_student = (
                await asyncio.gather(
                    session_module.create_session_from_json(session_json),
                    session_module.create_session_from_json(session_json),
                    session_module.create_session_from_json(session_json),
                )
            )
            logger.info(f"세션 복원 완료: {time.time() - session_start:.2f}초")

            sessions = [
                ("course1", session_course1),
                ("course2", session_course2),
                ("student", session_student),
            ]

            try:
                app_start = time.time()
                course_grades_app1, course_grades_app2, student_info_app = (
                    await asyncio.gather(
                        session_module.get_course_grades_app(session_course1),
                        session_module.get_course_grades_app(session_course2),
                        session_module.get_student_info_app(session_student),
                    )
                )
            except Exception as e:
                logger.error(
                    f"Application 생성 실패: {type(e).__name__} - {e!s}",
                    exc_info=True,
                )
                await session_module.cleanup_sessions(sessions)
                raise
            logger.info(f"Application 생성 완료: {time.time() - app_start:.2f}초")

            data_start = time.time()
            (
                (basic_info, basic_warnings),
                (taken_courses, low_grade_codes, course_warnings),
                flags,
            ) = await asyncio.gather(
                fetchers.fetch_basic_info(student_info_app),
                fetchers.fetch_all_course_data_parallel(
                    course_grades_app1, course_grades_app2, SEMESTER_TYPE_MAP
                ),
                fetchers.fetch_flags(student_info_app),
            )
            logger.info(f"데이터 조회 완료: {time.time() - data_start:.2f}초")

            warnings = basic_warnings + course_warnings
            if warnings:
                logger.info(f"Snapshot warnings: {warnings}")

            total_time = time.time() - start_time
            logger.info(
                f"유세인트 데이터 조회 완료: [session_json] (총 {total_time:.2f}초)"
            )

            return UsaintSnapshotResponse(
                takenCourses=taken_courses,
                lowGradeSubjectCodes=low_grade_codes,
                flags=flags,
                basicInfo=basic_info,
                warnings=warnings,
            )

        except (SSOTokenError, RusaintConnectionError, RusaintTimeoutError, RusaintInternalError):
            raise
        except rusaint.RusaintError as e:
            logger.error(
                f"Rusaint 오류 ([session_json]): {type(e).__name__}\n"
                f"에러 메시지: {e!s}",
                exc_info=True,
            )
            raise RusaintInternalError(f"유세인트 데이터 조회 중 오류: {type(e).__name__} - {e!s}")
        except asyncio.TimeoutError:
            logger.error("유세인트 연결 시간 초과 ([session_json])")
            raise RusaintTimeoutError("유세인트 서버 응답 시간이 초과되었습니다.")
        except Exception as e:
            logger.error(
                f"유세인트 데이터 조회 중 오류 발생 ([session_json]): {type(e).__name__}\n"
                f"에러 메시지: {e!s}",
                exc_info=True,
            )
            raise RusaintInternalError(f"예기치 않은 오류: {type(e).__name__} - {e!s}")
        finally:
            await session_module.cleanup_sessions(sessions)

    async def fetch_usaint_snapshot_academic(
        self,
        session_json: str,
    ) -> dict:
        """학적/성적 이력 조회 (졸업사정표 제외). → Academic 서비스 위임."""
        return await self._academic.fetch_usaint_snapshot_academic(session_json)

    async def fetch_usaint_graduation_info(
        self,
        session_json: str,
    ) -> dict:
        """졸업사정표 조회. → Graduation 서비스 위임."""
        return await self._graduation.fetch_usaint_graduation_info(session_json)

    async def find_lectures(
        self,
        session_json: str,
        year: int,
        semester: str,
        category_type: str,
        collage: str | None = None,
        department: str | None = None,
        major: str | None = None,
        lecture_name: str | None = None,
        category: str | None = None,
        keyword: str | None = None,
        include_details: bool = False,
    ) -> dict:
        """강의시간표 검색. → CourseSchedule 서비스 위임."""
        return await self._course_schedule.find_lectures(
            session_json,
            year=year,
            semester=semester,
            category_type=category_type,
            collage=collage,
            department=department,
            major=major,
            lecture_name=lecture_name,
            category=category,
            keyword=keyword,
            include_details=include_details,
        )

    async def find_optional_elective_categories(
        self,
        session_json: str,
        year: int,
        semester: str,
    ) -> dict:
        """교양선택 분야 목록 조회. → CourseSchedule 서비스 위임."""
        return await self._course_schedule.find_optional_elective_categories(
            session_json, year, semester
        )

    async def find_required_electives(
        self,
        session_json: str,
        year: int,
        semester: str,
    ) -> dict:
        """교양필수 과목명 목록 조회. → CourseSchedule 서비스 위임."""
        return await self._course_schedule.find_required_electives(
            session_json, year, semester
        )

    async def build_department_map(
        self,
        session_json: str,
        year: int,
        semester: str,
    ) -> dict:
        """학과-단과대 매핑 빌드. → CourseSchedule 서비스 위임."""
        return await self._course_schedule.build_department_map(
            session_json, year=year, semester=semester
        )

    async def find_collages(
        self,
        session_json: str,
        year: int,
        semester: str,
    ) -> list[str]:
        """단과대 목록 조회. → CourseSchedule 서비스 위임."""
        return await self._course_schedule.find_collages(
            session_json, year=year, semester=semester
        )

    async def find_departments(
        self,
        session_json: str,
        year: int,
        semester: str,
        collage: str,
    ) -> list[str]:
        """특정 단과대의 학과(부) 목록. → CourseSchedule 서비스 위임."""
        return await self._course_schedule.find_departments(
            session_json, year=year, semester=semester, collage=collage
        )

    async def fetch_basic_info(
        self, session_json: str
    ) -> tuple[BasicInfo, list[str]]:
        """학적 기본 정보를 가볍게 조회 (~2-3초, 1세션).

        졸업사정표/수강이력 없이 주전공/복수/연계/부전공/교직 정보만 필요한
        refresh_user_profile 등에서 사용.

        반환: (BasicInfo, warnings) — warnings는 NO_SEMESTER_INFO 등 데이터 누락 코드.
            교직/복수 정보 조회 실패 시 해당 필드는 기본값(False/None).
        """
        session = None
        try:
            session = await session_module.create_session_from_json(session_json)
            app = await session_module.get_student_info_app(session)
            basic_info, warnings = await fetchers.fetch_basic_info(app)
            return basic_info, warnings
        except (SSOTokenError, RusaintConnectionError, RusaintTimeoutError, RusaintInternalError):
            raise
        except rusaint.RusaintError as e:
            raise RusaintInternalError(
                f"학적 기본 정보 조회 중 오류: {type(e).__name__} - {e!s}"
            ) from e
        except asyncio.TimeoutError as exc:
            raise RusaintTimeoutError("유세인트 서버 응답 시간이 초과되었습니다.") from exc
        except Exception as e:
            raise RusaintInternalError(
                f"예기치 않은 오류: {type(e).__name__} - {e!s}"
            ) from e
        finally:
            if session:
                await session_module.cleanup_sessions([("basic", session)])

    async def validate_session(self, session_json: str) -> None:
        """
        세션 JSON 유효성 검증 (세션 복원만 시도 후 즉시 종료).

        데이터 조회 없이 세션 복원만 시도하므로 빠릅니다.

        Raises:
            SSOTokenError: 세션이 유효하지 않거나 만료된 경우
            RusaintConnectionError: 연결 실패
            RusaintTimeoutError: 타임아웃
            RusaintInternalError: rusaint 내부 오류
        """
        session = None
        try:
            session = await session_module.create_session_from_json(session_json)
            logger.info("세션 검증 성공: [session_json]")
        finally:
            if session:
                await session_module.cleanup_sessions([("validate", session)])
