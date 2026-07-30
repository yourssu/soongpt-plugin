"""
유세인트 강의시간표 조회 서비스.

학기/카테고리로 강의 목록을 검색합니다. 숭피티 자체 과목 DB 대신 USAINT에서 실시간 조회.
"""

import asyncio
import logging
import time
from typing import Any

import rusaint

from soongpt_mcp.services import session as session_module
from soongpt_mcp.services.exceptions import (
    RusaintConnectionError,
    RusaintInternalError,
    RusaintTimeoutError,
    SSOTokenError,
)

logger = logging.getLogger(__name__)


SEMESTER_MAP: dict[str, rusaint.SemesterType] = {
    "1": rusaint.SemesterType.ONE,
    "summer": rusaint.SemesterType.SUMMER,
    "2": rusaint.SemesterType.TWO,
    "winter": rusaint.SemesterType.WINTER,
}

# category_type → LectureCategoryBuilder 메서드 이름
CATEGORY_TYPES = (
    "major",
    "required_elective",
    "optional_elective",
    "chapel",
    "education",
    "graduated",
    "connected_major",
    "united_major",
    "find_by_professor",
    "find_by_lecture",
    "recognized_other_major",
    "cyber",
)


class RusaintCourseScheduleService:
    """유세인트 강의시간표 조회 (학기/카테고리별 강의 검색)"""

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
    ) -> dict[str, Any]:
        """
        강의시간표에서 강의 검색.

        category_type에 따라 필요한 파라미터가 다름:
        - major / recognized_other_major: collage, department 필수, major 선택
        - required_elective / chapel: lecture_name 필수
        - optional_elective: category 필수
        - graduated: collage, department 필수
        - connected_major / united_major: major 필수
        - find_by_professor / find_by_lecture: keyword 필수
        - education / cyber: 추가 파라미터 없음
        """
        start_time = time.time()

        if category_type not in CATEGORY_TYPES:
            raise ValueError(
                f"지원하지 않는 category_type: {category_type}. "
                f"지원 목록: {', '.join(CATEGORY_TYPES)}"
            )

        semester_enum = SEMESTER_MAP.get(semester.lower())
        if semester_enum is None:
            raise ValueError(
                f"지원하지 않는 semester: {semester}. "
                "지원 값: 1, 2, summer, winter"
            )

        lecture_category = self._build_category(
            category_type,
            collage=collage,
            department=department,
            major=major,
            lecture_name=lecture_name,
            category=category,
            keyword=keyword,
        )

        logger.info(
            "유세인트 강의시간표 조회 시작: year=%d semester=%s category_type=%s include_details=%s",
            year, semester, category_type, include_details,
        )

        sessions: list[tuple[str, rusaint.USaintSession | None]] = []

        try:
            session_start = time.time()
            session_obj = await session_module.create_session_from_json(session_json)
            sessions = [("course_schedule", session_obj)]
            logger.info(f"세션 복원 완료: {time.time() - session_start:.2f}초")

            app_start = time.time()
            app = await session_module.get_course_schedule_app(session_obj)
            logger.info(f"Application 생성 완료: {time.time() - app_start:.2f}초")

            data_start = time.time()
            if include_details:
                detailed = await app.find_detailed_lectures(
                    year, semester_enum, lecture_category, True
                )
                lectures = [self._dump_detailed(item) for item in detailed]
            else:
                raw_lectures = await app.find_lectures(
                    year, semester_enum, lecture_category
                )
                lectures = [self._dump_lecture(item) for item in raw_lectures]
            logger.info(f"데이터 조회 완료: {time.time() - data_start:.2f}초")

            total_time = time.time() - start_time
            logger.info(
                "유세인트 강의시간표 조회 완료: %d건 (총 %.2f초)",
                len(lectures), total_time,
            )

            return {
                "lectures": lectures,
                "count": len(lectures),
                "fetchTime": f"{total_time:.2f}s",
                "includeDetails": include_details,
            }

        except ValueError:
            raise
        except (SSOTokenError, RusaintConnectionError, RusaintTimeoutError, RusaintInternalError):
            raise
        except rusaint.RusaintError as e:
            logger.error(
                f"Rusaint 오류: {type(e).__name__} - {e!s}",
                exc_info=True,
            )
            raise RusaintInternalError(
                f"유세인트 강의시간표 조회 중 오류: {type(e).__name__} - {e!s}"
            )
        except asyncio.TimeoutError:
            logger.error("유세인트 연결 시간 초과")
            raise RusaintTimeoutError("유세인트 서버 응답 시간이 초과되었습니다.")
        except Exception as e:
            logger.error(
                f"유세인트 강의시간표 조회 중 예기치 않은 오류: {type(e).__name__} - {e!s}",
                exc_info=True,
            )
            raise RusaintInternalError(f"예기치 않은 오류: {type(e).__name__} - {e!s}")
        finally:
            await session_module.cleanup_sessions(sessions)

    async def build_department_map(
        self,
        session_json: str,
        year: int,
        semester: str,
    ) -> dict[str, Any]:
        """단과대를 순회하며 {학과명: 단과대} 매핑을 한 번에 빌드.

        단과대마다 _run_with_session을 타면 세션 복원 비용이 N배 발생하므로,
        빌드는 단일 세션 컨텍스트에서 collages() + 모든 departments()를 묶어 수행.
        """
        start_time = time.time()

        semester_enum = SEMESTER_MAP.get(semester.lower())
        if semester_enum is None:
            raise ValueError(
                f"지원하지 않는 semester: {semester}. "
                "지원 값: 1, 2, summer, winter"
            )

        logger.info(
            "학과-단과대 매핑 빌드 시작: year=%d semester=%s", year, semester
        )

        sessions: list[tuple[str, rusaint.USaintSession | None]] = []
        try:
            session_obj = await session_module.create_session_from_json(session_json)
            sessions = [("course_schedule", session_obj)]
            app = await session_module.get_course_schedule_app(session_obj)

            collages = await app.collages(year, semester_enum)
            logger.info(
                "단과대 %d개 조회 완료 (%.2f초)",
                len(collages),
                time.time() - start_time,
            )

            mapping: dict[str, str] = {}
            for collage in collages:
                departments = await app.departments(
                    year, semester_enum, collage
                )
                for dept in departments:
                    mapping[dept] = collage

            total = time.time() - start_time
            logger.info(
                "학과-단과대 매핑 빌드 완료: 학과 %d개 / 단과대 %d개 (%.2f초)",
                len(mapping),
                len(collages),
                total,
            )
            return {
                "mapping": mapping,
                "collages": list(collages),
                "department_count": len(mapping),
                "collage_count": len(collages),
                "fetchTime": f"{total:.2f}s",
            }

        except ValueError:
            raise
        except (
            SSOTokenError,
            RusaintConnectionError,
            RusaintTimeoutError,
            RusaintInternalError,
        ):
            raise
        except rusaint.RusaintError as e:
            logger.error(
                "Rusaint 오류: %s - %s", type(e).__name__, str(e), exc_info=True
            )
            raise RusaintInternalError(
                f"학과-단과대 매핑 빌드 중 오류: {type(e).__name__} - {e!s}"
            )
        except asyncio.TimeoutError:
            logger.error("유세인트 연결 시간 초과 (매핑 빌드)")
            raise RusaintTimeoutError("유세인트 서버 응답 시간이 초과되었습니다.")
        except Exception as e:
            logger.error(
                "매핑 빌드 중 예기치 않은 오류: %s - %s",
                type(e).__name__,
                str(e),
                exc_info=True,
            )
            raise RusaintInternalError(
                f"예기치 않은 오류: {type(e).__name__} - {e!s}"
            )
        finally:
            await session_module.cleanup_sessions(sessions)

    async def find_collages(
        self,
        session_json: str,
        year: int,
        semester: str,
    ) -> list[str]:
        """선택 학기 기준 단과대 목록 조회 (학과-단과대 매핑 빌드용)."""
        return await self._fetch_list(
            session_json,
            year,
            semester,
            lambda app, y, sem: app.collages(y, sem),
            label="collages",
        )

    async def find_departments(
        self,
        session_json: str,
        year: int,
        semester: str,
        collage: str,
    ) -> list[str]:
        """특정 단과대의 학과(부) 목록 조회 (학과-단과대 매핑 빌드용)."""
        if not collage or not str(collage).strip():
            raise ValueError("collage 파라미터가 필요합니다")

        async def _call(
            app: rusaint.CourseScheduleApplication,
            y: int,
            sem: rusaint.SemesterType,
        ) -> list[str]:
            return await app.departments(y, sem, collage)

        return await self._fetch_list(
            session_json,
            year,
            semester,
            _call,
            label=f"departments(collage={collage!r})",
        )

    async def _fetch_list(
        self,
        session_json: str,
        year: int,
        semester: str,
        call: Any,
        label: str,
    ) -> list[str]:
        """세션 복원 → CourseScheduleApplication 생성 → 단일 목록 호출 공통 흐름."""
        start_time = time.time()

        semester_enum = SEMESTER_MAP.get(semester.lower())
        if semester_enum is None:
            raise ValueError(
                f"지원하지 않는 semester: {semester}. "
                "지원 값: 1, 2, summer, winter"
            )

        logger.info("유세인트 %s 조회 시작: year=%d semester=%s", label, year, semester)

        sessions: list[tuple[str, rusaint.USaintSession | None]] = []
        try:
            session_obj = await session_module.create_session_from_json(session_json)
            sessions = [("course_schedule", session_obj)]
            app = await session_module.get_course_schedule_app(session_obj)
            result = await call(app, year, semester_enum)
            total = time.time() - start_time
            logger.info("유세인트 %s 조회 완료: %d건 (%.2f초)", label, len(result), total)
            return result

        except ValueError:
            raise
        except (
            SSOTokenError,
            RusaintConnectionError,
            RusaintTimeoutError,
            RusaintInternalError,
        ):
            raise
        except rusaint.RusaintError as e:
            logger.error(
                "Rusaint 오류: %s - %s", type(e).__name__, str(e), exc_info=True
            )
            raise RusaintInternalError(
                f"유세인트 {label} 조회 중 오류: {type(e).__name__} - {e!s}"
            )
        except asyncio.TimeoutError:
            logger.error("유세인트 연결 시간 초과 (%s)", label)
            raise RusaintTimeoutError("유세인트 서버 응답 시간이 초과되었습니다.")
        except Exception as e:
            logger.error(
                "유세인트 %s 조회 중 예기치 않은 오류: %s - %s",
                label,
                type(e).__name__,
                str(e),
                exc_info=True,
            )
            raise RusaintInternalError(
                f"예기치 않은 오류: {type(e).__name__} - {e!s}"
            )
        finally:
            await session_module.cleanup_sessions(sessions)

    async def find_optional_elective_categories(
        self,
        session_json: str,
        year: int,
        semester: str,
    ) -> Dict[str, Any]:
        """해당 학기의 교양선택 분야 목록을 조회.

        반환: { categories: [str], count, fetchTime }
        분야명 예: "[‘23이후]과학·기술". 학번/학기에 따라 다름.
        """
        start_time = time.time()

        semester_enum = SEMESTER_MAP.get(semester.lower())
        if semester_enum is None:
            raise ValueError(
                f"지원하지 않는 semester: {semester}. "
                "지원 값: 1, 2, summer, winter"
            )

        logger.info(
            "유세인트 교양선택 분야 조회 시작: year=%d semester=%s",
            year, semester,
        )

        sessions: List[Tuple[str, Optional[rusaint.USaintSession]]] = []

        try:
            session_start = time.time()
            session_obj = await session_module.create_session_from_json(session_json)
            sessions = [("course_schedule_categories", session_obj)]
            logger.info(f"세션 복원 완료: {time.time() - session_start:.2f}초")

            app_start = time.time()
            app = await session_module.get_course_schedule_app(session_obj)
            logger.info(f"Application 생성 완료: {time.time() - app_start:.2f}초")

            data_start = time.time()
            categories = await app.optional_elective_categories(year, semester_enum)
            logger.info(f"데이터 조회 완료: {time.time() - data_start:.2f}초")

            total_time = time.time() - start_time
            logger.info(
                "유세인트 교양선택 분야 조회 완료: %d건 (총 %.2f초)",
                len(categories), total_time,
            )

            return {
                "categories": list(categories),
                "count": len(categories),
                "fetchTime": f"{total_time:.2f}s",
            }

        except ValueError:
            raise
        except (SSOTokenError, RusaintConnectionError, RusaintTimeoutError, RusaintInternalError):
            raise
        except rusaint.RusaintError as e:
            logger.error(
                f"Rusaint 오류: {type(e).__name__} - {str(e)}",
                exc_info=True,
            )
            raise RusaintInternalError(
                f"유세인트 교양선택 분야 조회 중 오류: {type(e).__name__} - {str(e)}"
            )
        except asyncio.TimeoutError:
            logger.error("유세인트 연결 시간 초과")
            raise RusaintTimeoutError("유세인트 서버 응답 시간이 초과되었습니다.")
        except Exception as e:
            logger.error(
                f"유세인트 교양선택 분야 조회 중 예기치 않은 오류: {type(e).__name__} - {str(e)}",
                exc_info=True,
            )
            raise RusaintInternalError(f"예기치 않은 오류: {type(e).__name__} - {str(e)}")
        finally:
            await session_module.cleanup_sessions(sessions)

    def _build_category(
        self,
        category_type: str,
        collage: str | None,
        department: str | None,
        major: str | None,
        lecture_name: str | None,
        category: str | None,
        keyword: str | None,
    ) -> rusaint.LectureCategory:
        builder = rusaint.LectureCategoryBuilder()

        if category_type == "major":
            self._require("major", collage=collage, department=department)
            return builder.major(collage, department, major)
        if category_type == "recognized_other_major":
            self._require("recognized_other_major", collage=collage, department=department)
            return builder.recognized_other_major(collage, department, major)
        if category_type == "graduated":
            self._require("graduated", collage=collage, department=department)
            return builder.graduated(collage, department)
        if category_type == "required_elective":
            self._require("required_elective", lecture_name=lecture_name)
            return builder.required_elective(lecture_name)
        if category_type == "chapel":
            self._require("chapel", lecture_name=lecture_name)
            return builder.chapel(lecture_name)
        if category_type == "optional_elective":
            self._require("optional_elective", category=category)
            return builder.optional_elective(category)
        if category_type == "connected_major":
            self._require("connected_major", major=major)
            return builder.connected_major(major)
        if category_type == "united_major":
            self._require("united_major", major=major)
            return builder.united_major(major)
        if category_type == "find_by_professor":
            self._require("find_by_professor", keyword=keyword)
            return builder.find_by_professor(keyword)
        if category_type == "find_by_lecture":
            self._require("find_by_lecture", keyword=keyword)
            return builder.find_by_lecture(keyword)
        if category_type == "education":
            return builder.education()
        if category_type == "cyber":
            return builder.cyber()

        raise ValueError(f"처리되지 않은 category_type: {category_type}")

    def _require(self, category_type: str, **fields: str | None) -> None:
        missing = [k for k, v in fields.items() if v is None or str(v).strip() == ""]
        if missing:
            raise ValueError(
                f"category_type '{category_type}'에 필요한 파라미터 누락: {', '.join(missing)}"
            )

    @staticmethod
    def _dump_lecture(lec: Any) -> dict[str, Any]:
        """Lecture 객체를 JSON 직렬화 가능한 dict로 변환."""
        if hasattr(lec, "model_dump"):
            return lec.model_dump(mode="json")
        return {
            "code": getattr(lec, "code", None),
            "name": getattr(lec, "name", None),
            "category": getattr(lec, "category", None),
            "sub_category": getattr(lec, "sub_category", None),
            "field": getattr(lec, "field", None),
            "division": getattr(lec, "division", None),
            "professor": getattr(lec, "professor", None),
            "department": getattr(lec, "department", None),
            "time_points": getattr(lec, "time_points", None),
            "personeel": getattr(lec, "personeel", None),
            "remaining_seats": getattr(lec, "remaining_seats", None),
            "schedule_room": getattr(lec, "schedule_room", None),
            "target": getattr(lec, "target", None),
            "abeek_info": getattr(lec, "abeek_info", None),
            "syllabus_link": getattr(lec, "syllabus", None),
        }

    def _dump_detailed(self, item: Any) -> dict[str, Any]:
        """DetailedLecture (lecture + detail + syllabus)를 dict로 변환."""
        lecture = getattr(item, "lecture", None)
        base = self._dump_lecture(lecture) if lecture is not None else {}

        detail = getattr(item, "detail", None)
        base["detail"] = self._dump_optional_model(detail)

        syllabus = getattr(item, "syllabus", None)
        base["syllabus"] = self._dump_optional_model(syllabus)

        return base

    @staticmethod
    def _dump_optional_model(obj: Any) -> dict[str, Any] | None:
        if obj is None:
            return None
        if hasattr(obj, "model_dump"):
            return obj.model_dump(mode="json")
        if hasattr(obj, "__dict__"):
            return {k: getattr(obj, k) for k in vars(obj)}
        return None
