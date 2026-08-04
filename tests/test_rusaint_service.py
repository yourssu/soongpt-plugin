"""RusaintService 단위 테스트.

fetch_usaint_snapshot의 세션/App 생성 경계를 검증한다. 실제 USAINT I/O 없이
session_module과 fetchers의 경계 함수를 스텁으로 교체해, 어떤 세션이 몇 개
만들어지는지를 확인한다.
"""
from __future__ import annotations

from typing import Any

import pytest

from soongpt_mcp.schemas.usaint_schemas import BasicInfo, Flags, UsaintSnapshotResponse
from soongpt_mcp.services import fetchers
from soongpt_mcp.services import session as session_module
from soongpt_mcp.services.rusaint_service import RusaintService


@pytest.mark.asyncio
async def test_fetch_snapshot_creates_three_sessions_and_skips_graduation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SPR-64: 스냅샷은 세션 3개(course1/course2/student)만 만든다.

    이전에는 졸업사정표 세션(grad)까지 4개를 병렬 생성했으나 데이터 조회에
    쓰이지 않아 병목만 유발해 제거했다. 이 테스트는 grad 세션/앱이 다시
    끼어들면(회귀) 잡아낸다 — get_graduation_app 호출 수가 0이어야 한다.
    """
    calls: dict[str, int] = {
        "create_session": 0,
        "graduation_app": 0,
        "course_grades_app": 0,
        "student_info_app": 0,
    }

    async def fake_create_session(_session_json: str) -> Any:
        calls["create_session"] += 1
        return object()  # 세션 sentinel — fetchers가 스텁이므로 내부 미사용

    async def fake_graduation_app(_session: Any) -> Any:
        calls["graduation_app"] += 1
        return object()

    async def fake_course_grades_app(_session: Any) -> Any:
        calls["course_grades_app"] += 1
        return object()

    async def fake_student_info_app(_session: Any) -> Any:
        calls["student_info_app"] += 1
        return object()

    async def noop_cleanup(_sessions: Any) -> None:
        return None

    monkeypatch.setattr(session_module, "create_session_from_json", fake_create_session)
    monkeypatch.setattr(session_module, "get_graduation_app", fake_graduation_app)
    monkeypatch.setattr(session_module, "get_course_grades_app", fake_course_grades_app)
    monkeypatch.setattr(session_module, "get_student_info_app", fake_student_info_app)
    monkeypatch.setattr(session_module, "cleanup_sessions", noop_cleanup)

    async def fake_basic_info(_app: Any) -> tuple[BasicInfo, list[str]]:
        return BasicInfo(year=2023, grade=3, semester=5, department="컴퓨터학부"), []

    async def fake_course_data(_app1: Any, _app2: Any, _semester_map: Any) -> tuple:
        return [], [], []

    async def fake_flags(_app: Any) -> Flags:
        return Flags()

    monkeypatch.setattr(fetchers, "fetch_basic_info", fake_basic_info)
    monkeypatch.setattr(fetchers, "fetch_all_course_data_parallel", fake_course_data)
    monkeypatch.setattr(fetchers, "fetch_flags", fake_flags)

    result = await RusaintService().fetch_usaint_snapshot("dummy-session-json")

    # 세션은 정확히 3개 (course1/course2/student)
    assert calls["create_session"] == 3
    # 졸업사정표 세션/앱은 절대 생성하지 않는다 — 핵심 회귀 방지
    assert calls["graduation_app"] == 0
    # 앱 생성은 course_grades 2개 + student 1개
    assert calls["course_grades_app"] == 2
    assert calls["student_info_app"] == 1
    # 결과는 정상적으로 조립된다
    assert isinstance(result, UsaintSnapshotResponse)
    assert result.basicInfo.department == "컴퓨터학부"
