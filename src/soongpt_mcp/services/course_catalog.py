"""들을 수 있는 과목 통합 조회 오케스트레이터.

여러 카테고리 요청을 병렬로 fetch하여 {category_type: {lectures, count, error}} 형태로 그룹화.
기존 RusaintCourseScheduleService.find_lectures()를 재사용 — 새 네트워크 로직 추가 X.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from soongpt_mcp.services.rusaint_course_schedule_service import (
    RusaintCourseScheduleService,
)

logger = logging.getLogger(__name__)


@dataclass
class LectureCategoryRequest:
    """단일 카테고리 조회 요청.

    parameters는 RusaintCourseScheduleService.find_lectures()에
    category_type/include_details을 제외한 kwargs로 그대로 전달됨
    (collage, department, major, lecture_name, category, keyword).
    """

    category_type: str
    parameters: dict[str, Any] = field(default_factory=dict)


async def fetch_available_lectures(
    session_json: str,
    year: int,
    semester: str,
    requests: list[LectureCategoryRequest],
    *,
    service: RusaintCourseScheduleService | None = None,
    include_details: bool = False,
) -> dict[str, Any]:
    """여러 카테고리 요청을 병렬 fetch하여 그룹화된 결과 반환.

    - asyncio.gather(return_exceptions=True)로 부분 실패 허용
    - 실패한 카테고리는 error 필드에 메시지, 다른 카테고리는 정상 동작
    - 같은 category_type이 여러 요청에 들어오면 마지막 요청 결과로 덮어씀
      (TODO: 후속 PR에서 중복 키 처리 정책 결정)

    반환 스키마:
        {
          "year": int,
          "semester": str,
          "groups": {category_type: {"lectures": [...], "count": N, "error": str | None}},
          "totalCount": int,
          "fetchTime": "X.XXs",
          "requestedCategories": [category_type, ...],
        }
    """
    start_time = time.time()
    svc = service or RusaintCourseScheduleService()

    if not requests:
        return {
            "year": year,
            "semester": semester,
            "groups": {},
            "totalCount": 0,
            "fetchTime": f"{time.time() - start_time:.2f}s",
            "requestedCategories": [],
        }

    tasks = [
        svc.find_lectures(
            session_json,
            year=year,
            semester=semester,
            category_type=req.category_type,
            include_details=include_details,
            **req.parameters,
        )
        for req in requests
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    groups: dict[str, dict[str, Any]] = {}
    for req, result in zip(requests, results):
        key = req.category_type
        if isinstance(result, BaseException):
            groups[key] = {
                "lectures": [],
                "count": 0,
                "error": f"{type(result).__name__}: {result}",
            }
            logger.warning(
                "카테고리 '%s' 조회 실패: %s", key, groups[key]["error"]
            )
            continue

        lectures = result.get("lectures", [])
        count = result.get("count", len(lectures))
        groups[key] = {
            "lectures": lectures,
            "count": count,
            "error": None,
        }

    total_count = sum(g["count"] for g in groups.values())

    total_time = time.time() - start_time
    logger.info(
        "통합 강의 조회 완료: %d개 카테고리, 총 %d건 (%.2f초)",
        len(requests), total_count, total_time,
    )

    return {
        "year": year,
        "semester": semester,
        "groups": groups,
        "totalCount": total_count,
        "fetchTime": f"{total_time:.2f}s",
        "requestedCategories": [req.category_type for req in requests],
    }
