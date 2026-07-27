"""session_manager 테스트: 캐싱, 직렬화, invalidate, fallback."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

import soongpt_mcp.session_manager as sm
from soongpt_mcp.session_manager import SessionError, SessionManager


@pytest.mark.asyncio
async def test_returns_stored_session_from_keyring(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sm, "load_session", lambda: '{"stored": true}')
    saved: list[str] = []
    monkeypatch.setattr(sm, "save_session", lambda s: saved.append(s))

    mgr = SessionManager()
    result = await mgr.get_valid_session()

    assert result == '{"stored": true}'
    assert saved == []  # 재저장 안 함


@pytest.mark.asyncio
async def test_falls_back_to_web_login_when_no_stored_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sm, "load_session", lambda: None)
    fake_run = AsyncMock(return_value='{"new": "session"}')
    monkeypatch.setattr(sm, "run_web_login", fake_run)
    saved: list[str] = []
    monkeypatch.setattr(sm, "save_session", lambda s: saved.append(s))

    mgr = SessionManager()
    result = await mgr.get_valid_session()

    assert result == '{"new": "session"}'
    assert saved == ['{"new": "session"}']
    fake_run.assert_awaited_once()


@pytest.mark.asyncio
async def test_caches_session_after_first_login(monkeypatch: pytest.MonkeyPatch) -> None:
    """동일 매니저 인스턴스의 두 번째 호출은 캐시에서 즉시 반환."""
    monkeypatch.setattr(sm, "load_session", lambda: None)
    call_count = [0]

    async def fake_run() -> str:
        call_count[0] += 1
        return f'{{"n": {call_count[0]}}}'

    monkeypatch.setattr(sm, "run_web_login", fake_run)
    monkeypatch.setattr(sm, "save_session", lambda s: None)

    mgr = SessionManager()
    first = await mgr.get_valid_session()
    second = await mgr.get_valid_session()

    assert first == second
    assert call_count[0] == 1  # 웹 로그인 1회만


@pytest.mark.asyncio
async def test_concurrent_calls_serialized_single_web_login(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """병렬 툴 호출이 Lock으로 직렬화되어 웹 로그인은 1회만 트리거."""
    monkeypatch.setattr(sm, "load_session", lambda: None)
    call_count = [0]
    started_event = asyncio.Event()

    async def fake_run() -> str:
        call_count[0] += 1
        if call_count[0] == 1:
            started_event.set()
            await asyncio.sleep(0.1)
        return f'{{"n": {call_count[0]}}}'

    monkeypatch.setattr(sm, "run_web_login", fake_run)
    monkeypatch.setattr(sm, "save_session", lambda s: None)

    mgr = SessionManager()
    results = await asyncio.gather(
        mgr.get_valid_session(),
        mgr.get_valid_session(),
        mgr.get_valid_session(),
    )

    assert call_count[0] == 1
    assert all(r == results[0] for r in results)


@pytest.mark.asyncio
async def test_invalidate_forces_relogin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sm, "load_session", lambda: None)
    sessions = []

    async def fake_run() -> str:
        n = len(sessions) + 1
        s = f'{{"n": {n}}}'
        sessions.append(s)
        return s

    monkeypatch.setattr(sm, "run_web_login", fake_run)
    monkeypatch.setattr(sm, "save_session", lambda s: None)

    mgr = SessionManager()
    first = await mgr.get_valid_session()
    mgr.invalidate()
    second = await mgr.get_valid_session()

    assert first != second
    assert len(sessions) == 2


@pytest.mark.asyncio
async def test_invalidate_skips_keyring_load(monkeypatch: pytest.MonkeyPatch) -> None:
    """invalidate 후에는 keyring이 무시되고 무조건 웹 로그인."""
    keyring_calls = [0]
    monkeypatch.setattr(
        sm, "load_session", lambda: (keyring_calls.__setitem__(0, keyring_calls[0] + 1), '{"old": true})')[1]
    )
    monkeypatch.setattr(sm, "run_web_login", AsyncMock(return_value='{"fresh": true}'))
    monkeypatch.setattr(sm, "save_session", lambda s: None)

    mgr = SessionManager()
    await mgr.get_valid_session()  # 첫 호출: keyring 조회 → 웹 로그인
    keyring_calls[0] = 0
    mgr.invalidate()
    await mgr.get_valid_session()  # invalidate 후 keyring 스킵

    assert keyring_calls[0] == 0


@pytest.mark.asyncio
async def test_web_login_error_propagates_as_session_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from soongpt_mcp.web_login import WebLoginError

    monkeypatch.setattr(sm, "load_session", lambda: None)
    monkeypatch.setattr(
        sm, "run_web_login", AsyncMock(side_effect=WebLoginError("timeout"))
    )
    monkeypatch.setattr(sm, "save_session", lambda s: None)

    mgr = SessionManager()
    with pytest.raises(SessionError, match="timeout"):
        await mgr.get_valid_session()


@pytest.mark.asyncio
async def test_save_session_error_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    from soongpt_mcp.auth import AuthError

    monkeypatch.setattr(sm, "load_session", lambda: None)
    monkeypatch.setattr(sm, "run_web_login", AsyncMock(return_value='{"x": 1}'))
    monkeypatch.setattr(sm, "save_session", lambda s: (_ for _ in ()).throw(AuthError("keyring locked")))

    mgr = SessionManager()
    with pytest.raises(SessionError, match="keyring"):
        await mgr.get_valid_session()


@pytest.mark.asyncio
async def test_get_session_manager_singleton() -> None:
    sm._manager = None  # 테스트 격리
    a = sm.get_session_manager()
    b = sm.get_session_manager()
    assert a is b
    sm._manager = None  # cleanup
