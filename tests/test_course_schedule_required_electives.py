"""`RusaintCourseScheduleService.find_required_electives` 단위 테스트 (SPR-51).

`find_optional_elective_categories`와 동일 패턴으로:
- 세션 복원 → CourseScheduleApplication 생성 → ``app.required_electives(year, sem)``
  호출을 검증하고,
- semester 매핑/검증, 반환 형태(``{lecture_names, count, fetchTime}``)를 확인한다.

USAINT 세션/네트워크 없이 session 모듈과 app을 mock으로 교체해 독립 실행한다.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
import rusaint

from soongpt_mcp.services.rusaint_course_schedule_service import (
    RusaintCourseScheduleService,
)


@pytest.fixture
def service() -> RusaintCourseScheduleService:
    return RusaintCourseScheduleService()


@pytest.fixture
def fake_app() -> AsyncMock:
    """``required_electives``/``optional_elective_categories``만 가진 fake app."""
    app = AsyncMock()
    app.required_electives.return_value = [
        "[SW와AI]AI개발과실전",
        "[인간과성서]인류문명과기독교",
        "한반도평화와통일",
    ]
    return app


def _patch_session_flow(
    monkeypatch: pytest.MonkeyPatch,
    fake_app: AsyncMock,
) -> AsyncMock:
    """session_module의 세션 복원 → app 생성 → cleanup을 mock으로 대체."""
    import soongpt_mcp.services.rusaint_course_schedule_service as svc_mod

    fake_session = AsyncMock()
    monkeypatch.setattr(
        svc_mod.session_module, "create_session_from_json", AsyncMock(return_value=fake_session)
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
    return fake_session


@pytest.mark.asyncio
async def test_find_required_electives_returns_names(
    service: RusaintCourseScheduleService,
    fake_app: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """app.required_electives 호출 인자(year, SemesterType)와 반환 형태 검증."""
    _patch_session_flow(monkeypatch, fake_app)

    result = await service.find_required_electives(
        "dummy-session", year=2026, semester="2"
    )

    # SemesterType으로 변환되어 호출되어야 함
    fake_app.required_electives.assert_awaited_once_with(
        2026, rusaint.SemesterType.TWO
    )
    assert result == {
        "lecture_names": [
            "[SW와AI]AI개발과실전",
            "[인간과성서]인류문명과기독교",
            "한반도평화와통일",
        ],
        "count": 3,
        "fetchTime": result["fetchTime"],  # 시간 문자열은 형태만 확인
    }
    assert isinstance(result["fetchTime"], str)
    assert result["fetchTime"].endswith("s")


@pytest.mark.asyncio
async def test_find_required_electives_semester_one_mapping(
    service: RusaintCourseScheduleService,
    fake_app: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """semester='1' → SemesterType.ONE 매핑."""
    _patch_session_flow(monkeypatch, fake_app)

    await service.find_required_electives("dummy-session", year=2026, semester="1")

    fake_app.required_electives.assert_awaited_once_with(
        2026, rusaint.SemesterType.ONE
    )


@pytest.mark.asyncio
async def test_find_required_electives_invalid_semester_raises(
    service: RusaintCourseScheduleService,
    fake_app: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """지원하지 않는 semester는 ValueError (app 호출 전에 검증)."""
    _patch_session_flow(monkeypatch, fake_app)

    with pytest.raises(ValueError, match="지원하지 않는 semester"):
        await service.find_required_electives(
            "dummy-session", year=2026, semester="3"
        )

    fake_app.required_electives.assert_not_awaited()
