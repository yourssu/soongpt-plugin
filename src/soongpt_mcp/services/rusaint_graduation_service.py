"""
유세인트 졸업사정표 조회 서비스.

졸업 요건 상세(raw) + 핵심 요약(graduationSummary) 조회 (약 5-6초).
"""

import asyncio
import logging
import time
from typing import List, Optional, Tuple

import rusaint

from soongpt_mcp.services import session as session_module
from soongpt_mcp.services import fetchers
from soongpt_mcp.services.graduation_summary_builder import build_graduation_summary
from soongpt_mcp.services.exceptions import (
    SSOTokenError,
    RusaintConnectionError,
    RusaintTimeoutError,
    RusaintInternalError,
)

logger = logging.getLogger(__name__)


class RusaintGraduationService:
    """유세인트 졸업사정표 조회 (약 5-6초)"""

    async def fetch_usaint_graduation_info(
        self,
        session_json: str,
    ) -> dict:
        """
        졸업사정표 정보 조회.

        포함: 개별 졸업 요건 상세(requirements, raw), 핵심 요약(graduationSummary).
        """
        start_time = time.time()
        logger.info("유세인트 Graduation 데이터 조회 시작: [session_json]")

        sessions: List[Tuple[str, Optional[rusaint.USaintSession]]] = []

        try:
            session_start = time.time()
            session_grad = await session_module.create_session_from_json(session_json)
            logger.info(f"세션 복원 완료: {time.time() - session_start:.2f}초")

            sessions = [("grad", session_grad)]

            try:
                app_start = time.time()
                grad_app = await session_module.get_graduation_app(session_grad)
                logger.info(f"Application 생성 완료: {time.time() - app_start:.2f}초")
            except Exception as e:
                logger.error(
                    f"Application 생성 실패: {type(e).__name__} - {str(e)}",
                    exc_info=True,
                )
                await session_module.cleanup_sessions(sessions)
                raise

            data_start = time.time()
            graduation_reqs = await fetchers.fetch_graduation_requirements(grad_app)
            logger.info(f"데이터 조회 완료: {time.time() - data_start:.2f}초")

            if not graduation_reqs.requirements:
                logger.warning(
                    "graduationRequirements.requirements가 비어 있음: [session_json] (graduationSummary 전 필드 null 가능)"
                )

            total_time = time.time() - start_time
            logger.info(
                f"유세인트 Graduation 데이터 조회 완료: [session_json] (총 {total_time:.2f}초)"
            )

            graduation_summary = build_graduation_summary(graduation_reqs.requirements)

            if graduation_summary is None:
                logger.warning(
                    "graduationSummary 전체가 null: [session_json]"
                )
            else:
                null_fields = [
                    name for name, value in graduation_summary.model_dump().items()
                    if value is None
                ]
                if null_fields:
                    logger.warning(
                        "graduationSummary 내 null 필드: [session_json], null_fields=%s",
                        null_fields,
                    )

            return {
                "graduationRequirements": graduation_reqs,
                "graduationSummary": graduation_summary,
            }

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
                f"유세인트 Graduation 데이터 조회 중 오류 발생 ([session_json]): {type(e).__name__}\n"
                f"에러 메시지: {str(e)}",
                exc_info=True,
            )
            raise RusaintInternalError(f"예기치 않은 오류: {type(e).__name__} - {str(e)}")
        finally:
            await session_module.cleanup_sessions(sessions)
