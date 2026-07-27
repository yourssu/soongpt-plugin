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
    - 같은 category_type이 여러 요청에 들어오면 마지막 요청 결과로 덮어씀.
      이때 requestedCategories에는 중복 키가 순서대로 그대로 남음 (요청 의도 보존).
      TODO: 중복 키 처리 정책은 후속 PR에서 결정
    - 빈 requests 분기는 server.get_available_lectures에서도 중복 처리함
      (session_manager를 건너뛰기 위한 최적화). 직접 호출자를 위해 여기서도 유지.

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
    seen_keys: set[str] = set()
    for req, result in zip(requests, results):
        key = req.category_type
        if key in seen_keys:
            logger.warning(
                "카테고리 '%s'가 여러 요청에 중복됨 — 마지막 결과로 덮어씀", key
            )
        seen_keys.add(key)

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
