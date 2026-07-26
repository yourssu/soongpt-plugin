"""
Rusaint 라이브러리를 사용한 유세인트 데이터 크롤링 서비스 (파사드).

- 학적/성적 이력: RusaintAcademicService 위임
- 졸업사정표: RusaintGraduationService 위임
- 전체 스냅샷(학적+졸업): 이 파일에서 직접 처리 (4세션 병렬)

모든 공개 메서드는 JSON 직렬화된 유세인트 세션(session_json)을 받습니다.
student_id/s_token은 더 이상 사용하지 않습니다.
"""

import asyncio
import logging
import time
from typing import List, Optional, Tuple

import rusaint

from soongpt_mcp.schemas.usaint_schemas import UsaintSnapshotResponse
from soongpt_mcp.services.constants import SEMESTER_TYPE_MAP
from soongpt_mcp.services import session as session_module
from soongpt_mcp.services import fetchers
from soongpt_mcp.services.exceptions import (
    SSOTokenError,
    RusaintConnectionError,
    RusaintTimeoutError,
    RusaintInternalError,
)
from soongpt_mcp.services.rusaint_academic_service import RusaintAcademicService
from soongpt_mcp.services.rusaint_graduation_service import RusaintGraduationService

logger = logging.getLogger(__name__)


class RusaintService:
    """
    유세인트 크롤링 파사드.

    - fetch_usaint_snapshot: 전체 스냅샷 (학적+졸업, 4세션)
    - fetch_usaint_snapshot_academic: 학적/성적만 → Academic 서비스 위임
    - fetch_usaint_graduation_info: 졸업사정표만 → Graduation 서비스 위임
    - validate_session: 세션 JSON 유효성 검증
    """

    SEMESTER_TYPE_MAP = SEMESTER_TYPE_MAP

    def __init__(self) -> None:
        self._academic = RusaintAcademicService()
        self._graduation = RusaintGraduationService()

    async def fetch_usaint_snapshot(
        self,
        session_json: str,
    ) -> UsaintSnapshotResponse:
        """
        JSON 직렬화 세션으로 유세인트 전체 스냅샷 조회 (학적+졸업, 4세션 병렬).
        """
        start_time = time.time()
        logger.info("유세인트 데이터 조회 시작: [session_json]")

        sessions: List[Tuple[str, Optional[rusaint.USaintSession]]] = []

        try:
            session_start = time.time()
            session_grad, session_course1, session_course2, session_student = (
                await asyncio.gather(
                    session_module.create_session_from_json(session_json),
                    session_module.create_session_from_json(session_json),
                    session_module.create_session_from_json(session_json),
                    session_module.create_session_from_json(session_json),
                )
            )
            logger.info(f"세션 복원 완료: {time.time() - session_start:.2f}초")

            sessions = [
                ("grad", session_grad),
                ("course1", session_course1),
                ("course2", session_course2),
                ("student", session_student),
            ]

            try:
                app_start = time.time()
                grad_app, course_grades_app1, course_grades_app2, student_info_app = (
                    await asyncio.gather(
                        session_module.get_graduation_app(session_grad),
                        session_module.get_course_grades_app(session_course1),
                        session_module.get_course_grades_app(session_course2),
                        session_module.get_student_info_app(session_student),
                    )
                )
            except Exception as e:
                logger.error(
                    f"Application 생성 실패: {type(e).__name__} - {str(e)}",
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
                f"에러 메시지: {str(e)}",
                exc_info=True,
            )
            raise RusaintInternalError(f"유세인트 데이터 조회 중 오류: {type(e).__name__} - {str(e)}")
        except asyncio.TimeoutError:
            logger.error("유세인트 연결 시간 초과 ([session_json])")
            raise RusaintTimeoutError("유세인트 서버 응답 시간이 초과되었습니다.")
        except Exception as e:
            logger.error(
                f"유세인트 데이터 조회 중 오류 발생 ([session_json]): {type(e).__name__}\n"
                f"에러 메시지: {str(e)}",
                exc_info=True,
            )
            raise RusaintInternalError(f"예기치 않은 오류: {type(e).__name__} - {str(e)}")
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
