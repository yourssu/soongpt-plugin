"""강의시간표(course_schedule) 계열 도구의 동시성 상한 세마포어 테스트 (SPR-67).

USAINT WebDynpro가 동일 SSO 세션의 동시 요청을 순차 처리하므로, 강의시간표
계열 도구(find_lectures / list_required_electives / list_optional_elective_categories)가
공유하는 asyncio.Semaphore가 동시 송출을 상한으로 묶는지 검증한다.

_run_with_session을 스텁해 "포털 호출 1회"를 시뮬레이션하되, 진입/이탈 시
동시 실행 수를 측정해 최댓값(peak)이 상한을 넘지 않는지 확인한다.
USAINT fetch는 실제 호출하지 않는다.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from soongpt_mcp import server
from soongpt_mcp.config import Config


@pytest.fixture(autouse=True)
def _reset_course_schedule_semaphore() -> None:
    """각 테스트 전후로 모듈 글로벌 세마포어를 리셋.

    lazy 세마포어가 이전 테스트의 config(상한값)를 기억하지 않도록 한다.
    """
    server._course_schedule_semaphore = None
    yield
    server._course_schedule_semaphore = None


def _make_concurrency_tracker() -> tuple[Any, list[int]]:
    """동시 실행 수를 측정하는 fake _run_with_session과 peak 공유 리스트 반환.

    fake는 진입 시 current += 1, peak 갱신, 잠깐 대기 후 이탈(current -= 1).
    "포털 1회 호출"을 시뮬레이션하며, 세마포어가 없으면 N개가 동시에 들어와
    peak == N이 된다.
    """
    state = {"current": 0, "peak": 0}

    async def fake_run(func: Any) -> dict[str, Any]:
        state["current"] += 1
        state["peak"] = max(state["peak"], state["current"])
        try:
            await asyncio.sleep(0.05)
            return {"lectures": [], "count": 0, "fetchTime": "0.00s"}
        finally:
            state["current"] -= 1

    return fake_run, state


async def _fire_concurrently(coros: list[Any]) -> None:
    await asyncio.gather(*coros)


@pytest.mark.asyncio
async def test_find_lectures_concurrency_capped_at_config_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """find_lectures N개 동시 호출 → 동시 실행 수가 상한(기본 4)을 넘지 않는다."""
    limit = Config().course_schedule_concurrency
    fake_run, state = _make_concurrency_tracker()
    monkeypatch.setattr(server, "_run_with_session", fake_run)

    await _fire_concurrently(
        [
            server.find_lectures(2026, "2", "required_elective", lecture_name=f"L{i}")
            for i in range(10)
        ]
    )

    # 상한 초과 금지 (핵심 불변) + 직렬화로 1로 떨어지지 않음 (과직렬화 회귀 감지)
    assert state["peak"] <= limit, (
        f"동시 실행 수 {state['peak']}가 상한 {limit}을 초과"
    )
    assert state["peak"] >= 2, "동시 실행이 거의 일어나지 않음 (세마포어 과직렬화 의심)"


@pytest.mark.asyncio
async def test_course_schedule_tools_share_one_semaphore(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """find_lectures + list_required_electives + list_optional_elective_categories 가
    같은 세마포어를 공유 → 세 도구를 섞어 동시에 쏴도 합산 동시 실행이 상한 이내."""
    limit = Config().course_schedule_concurrency
    fake_run, state = _make_concurrency_tracker()
    monkeypatch.setattr(server, "_run_with_session", fake_run)

    # 도구별로 limit*2개씩 = 충분한 병렬 압력. 도구별 세마포어였다면
    # peak == limit*3 까지 치솟을 수 있다.
    per_tool = limit * 2
    coros: list[Any] = []
    coros += [
        server.find_lectures(2026, "2", "required_elective", lecture_name=f"L{i}")
        for i in range(per_tool)
    ]
    coros += [server.list_required_electives(2026, "2") for _ in range(per_tool)]
    coros += [
        server.list_optional_elective_categories(2026, "2") for _ in range(per_tool)
    ]

    await _fire_concurrently(coros)

    assert state["peak"] <= limit, (
        f"세 도구 합산 동시 실행 {state['peak']}가 공유 상한 {limit}을 초과"
    )
    assert state["peak"] >= 2


@pytest.mark.asyncio
async def test_concurrency_limit_read_from_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """config.course_schedule_concurrency(SOONGPT_COURSE_SCHEDULE_CONCURRENCY) 값이
    실제 상한에 반영된다. limit=2로 주입 시 동시 실행은 2 이내."""
    monkeypatch.setattr(
        server, "get_config", lambda: Config(course_schedule_concurrency=2)
    )
    fake_run, state = _make_concurrency_tracker()
    monkeypatch.setattr(server, "_run_with_session", fake_run)

    await _fire_concurrently(
        [
            server.find_lectures(2026, "2", "required_elective", lecture_name=f"L{i}")
            for i in range(8)
        ]
    )

    assert state["peak"] == 2, (
        f"config 상한 2가 반영되지 않음: peak={state['peak']}"
    )


@pytest.mark.asyncio
async def test_semaphore_is_memoized_within_loop() -> None:
    """_get_course_schedule_semaphore는 같은 루프 내에서 동일 인스턴스를 반환
    (도구 3개가 같은 세마포어를 공유하는 물리적 근거)."""
    a = server._get_course_schedule_semaphore()
    b = server._get_course_schedule_semaphore()
    assert a is b
