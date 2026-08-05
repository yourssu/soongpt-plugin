"""`RusaintCourseScheduleService.find_lectures` 미개설 과목 처리 테스트 (SPR-79).

해당 학기에 개설되지 않은 강의(과목)를 조회하면 rusaint가 "No lecture found"
RusaintError를 던진다. 이는 조회 실패가 아니라 정상적인 빈 결과 조건이므로,
서비스는 예외로 전파하지 않고 ``{lectures: [], count: 0}``을 반환해야 한다.
그 외 RusaintError(세션/네트워크 등)는 여전히 ``RusaintInternalError``로
전파되어 기존 error 그룹 기록 흐름을 유지한다.

USAINT 세션/네트워크 없이 session 모듈과 app을 mock으로 교체해 독립 실행한다.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
import rusaint

from soongpt_mcp.services.exceptions import RusaintInternalError
from soongpt_mcp.services.rusaint_course_schedule_service import (
    RusaintCourseScheduleService,
)


@pytest.fixture
def service() -> RusaintCourseScheduleService:
    return RusaintCourseScheduleService()


def _patch_session_flow(
    monkeypatch: pytest.MonkeyPatch,
    fake_app: AsyncMock,
) -> None:
    """session_module의 세션 복원 → app 생성 → cleanup을 mock으로 대체."""
    import soongpt_mcp.services.rusaint_course_schedule_service as svc_mod

    fake_session = AsyncMock()
    monkeypatch.setattr(
        svc_mod.session_module,
        "create_session_from_json",
        AsyncMock(return_value=fake_session),
    )
    monkeypatch.setattr(
        svc_mod.session_module,
        "get_course_schedule_app",
        AsyncMock(return_value=fake_app),
    )
    monkeypatch.setattr(
        svc_mod.session_module,
        "cleanup_sessions",
        AsyncMock(),
    )


@pytest.mark.asyncio
async def test_find_lectures_no_lecture_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
    service: RusaintCourseScheduleService,
) -> None:
    """미개설 과목("No lecture found") → 예외 없이 빈 결과(count: 0) 반환."""
    app = AsyncMock()
    app.find_lectures.side_effect = rusaint.RusaintError.General("No lecture found")
    _patch_session_flow(monkeypatch, app)

    result = await service.find_lectures(
        "dummy-session",
        year=2026,
        semester="1",
        category_type="required_elective",
        lecture_name="[컴퓨팅적사고]컴퓨팅적사고와알고리즘",
    )

    assert result["lectures"] == []
    assert result["count"] == 0
    assert result["includeDetails"] is False
    assert "fetchTime" in result


@pytest.mark.asyncio
async def test_find_lectures_no_lecture_detailed_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
    service: RusaintCourseScheduleService,
) -> None:
    """include_details 경로(find_detailed_lectures)에서도 미개설 → 빈 결과."""
    app = AsyncMock()
    app.find_detailed_lectures.side_effect = rusaint.RusaintError.General(
        "No lecture found"
    )
    _patch_session_flow(monkeypatch, app)

    result = await service.find_lectures(
        "dummy-session",
        year=2026,
        semester="1",
        category_type="required_elective",
        lecture_name="[컴퓨팅적사고]컴퓨팅적사고활용",
        include_details=True,
    )

    assert result["lectures"] == []
    assert result["count"] == 0
    assert result["includeDetails"] is True


@pytest.mark.asyncio
async def test_find_lectures_other_rusaint_error_still_raises(
    monkeypatch: pytest.MonkeyPatch,
    service: RusaintCourseScheduleService,
) -> None:
    """미개설이 아닌 RusaintError는 여전히 RusaintInternalError로 전파."""
    app = AsyncMock()
    app.find_lectures.side_effect = rusaint.RusaintError.General("WebDynpro 내부 오류")
    _patch_session_flow(monkeypatch, app)

    with pytest.raises(RusaintInternalError, match="WebDynpro 내부 오류"):
        await service.find_lectures(
            "dummy-session",
            year=2026,
            semester="1",
            category_type="major",
            collage="IT대학",
            department="컴퓨터학부",
        )


@pytest.mark.asyncio
async def test_find_lectures_connected_major_option_error_still_raises(
    monkeypatch: pytest.MonkeyPatch,
    service: RusaintCourseScheduleService,
) -> None:
    """연계전공 실패("Cannot find ... option")는 미개설이 아니므로 여전히 전파.

    스킬(soongpt-available-lectures)은 연계/융합 중 한쪽 실패를 정상 무시로
    기대한다 — "No lecture found"가 아닌 WebDynpro 옵션 오류는 error 그룹
    흐름(예외 전파)을 유지해야 한다 (SPR-79 회귀 방지).
    """
    app = AsyncMock()
    app.find_lectures.side_effect = rusaint.RusaintError.General(
        "Cannot find ... option in .../CONNECT_MAJOR"
    )
    _patch_session_flow(monkeypatch, app)

    with pytest.raises(RusaintInternalError, match="CONNECT_MAJOR"):
        await service.find_lectures(
            "dummy-session",
            year=2026,
            semester="1",
            category_type="connected_major",
            major="미디어예술",
        )
