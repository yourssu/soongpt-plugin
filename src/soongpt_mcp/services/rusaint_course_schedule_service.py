"""
유세인트 강의시간표 조회 서비스.

학기/카테고리로 강의 목록을 검색합니다. 숭피티 자체 과목 DB 대신 USAINT에서 실시간 조회.
"""

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

import rusaint

from soongpt_mcp.services import session as session_module
from soongpt_mcp.services.exceptions import (
    SSOTokenError,
    RusaintConnectionError,
    RusaintTimeoutError,
    RusaintInternalError,
)

logger = logging.getLogger(__name__)


SEMESTER_MAP: Dict[str, rusaint.SemesterType] = {
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
        collage: Optional[str] = None,
        department: Optional[str] = None,
        major: Optional[str] = None,
        lecture_name: Optional[str] = None,
        category: Optional[str] = None,
        keyword: Optional[str] = None,
        include_details: bool = False,
    ) -> Dict[str, Any]:
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

        sessions: List[Tuple[str, Optional[rusaint.USaintSession]]] = []

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
                f"Rusaint 오류: {type(e).__name__} - {str(e)}",
                exc_info=True,
            )
            raise RusaintInternalError(
                f"유세인트 강의시간표 조회 중 오류: {type(e).__name__} - {str(e)}"
            )
        except asyncio.TimeoutError:
            logger.error("유세인트 연결 시간 초과")
            raise RusaintTimeoutError("유세인트 서버 응답 시간이 초과되었습니다.")
        except Exception as e:
            logger.error(
                f"유세인트 강의시간표 조회 중 예기치 않은 오류: {type(e).__name__} - {str(e)}",
                exc_info=True,
            )
            raise RusaintInternalError(f"예기치 않은 오류: {type(e).__name__} - {str(e)}")
        finally:
            await session_module.cleanup_sessions(sessions)

    def _build_category(
        self,
        category_type: str,
        collage: Optional[str],
        department: Optional[str],
        major: Optional[str],
        lecture_name: Optional[str],
        category: Optional[str],
        keyword: Optional[str],
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

    def _require(self, category_type: str, **fields: Optional[str]) -> None:
        missing = [k for k, v in fields.items() if v is None or str(v).strip() == ""]
        if missing:
            raise ValueError(
                f"category_type '{category_type}'에 필요한 파라미터 누락: {', '.join(missing)}"
            )

    @staticmethod
    def _dump_lecture(lec: Any) -> Dict[str, Any]:
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

    def _dump_detailed(self, item: Any) -> Dict[str, Any]:
        """DetailedLecture (lecture + detail + syllabus)를 dict로 변환."""
        lecture = getattr(item, "lecture", None)
        base = self._dump_lecture(lecture) if lecture is not None else {}

        detail = getattr(item, "detail", None)
        base["detail"] = self._dump_optional_model(detail)

        syllabus = getattr(item, "syllabus", None)
        base["syllabus"] = self._dump_optional_model(syllabus)

        return base

    @staticmethod
    def _dump_optional_model(obj: Any) -> Optional[Dict[str, Any]]:
        if obj is None:
            return None
        if hasattr(obj, "model_dump"):
            return obj.model_dump(mode="json")
        if hasattr(obj, "__dict__"):
            return {k: getattr(obj, k) for k in vars(obj)}
        return None
