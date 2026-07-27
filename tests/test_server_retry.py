"""server._run_with_session 재시도 로직 테스트."""
from __future__ import annotations

import pytest

from soongpt_mcp.server import _run_with_session
from soongpt_mcp.services.exceptions import SSOTokenError
from soongpt_mcp.session_manager import SessionError


class _FakeManager:
    def __init__(self) -> None:
        self.calls = 0
        self.invalidated = 0

    async def get_valid_session(self, *, force_relogin: bool = False) -> str:
        self.calls += 1
        return f"session-{self.calls}"

    def invalidate(self) -> None:
        self.invalidated += 1


@pytest.mark.asyncio
async def test_success_no_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    mgr = _FakeManager()
    monkeypatch.setattr("soongpt_mcp.server.get_session_manager", lambda: mgr)

    calls: list[str] = []

    async def service(session: str) -> str:
        calls.append(session)
        return "ok"

    result = await _run_with_session(service)
    assert result == "ok"
    assert calls == ["session-1"]
    assert mgr.invalidated == 0


@pytest.mark.asyncio
async def test_retry_on_sso_expiry(monkeypatch: pytest.MonkeyPatch) -> None:
    mgr = _FakeManager()
    monkeypatch.setattr("soongpt_mcp.server.get_session_manager", lambda: mgr)

    calls: list[str] = []

    async def service(session: str) -> str:
        calls.append(session)
        if session == "session-1":
            raise SSOTokenError("expired")
        return "ok"

    result = await _run_with_session(service)
    assert result == "ok"
    assert calls == ["session-1", "session-2"]
    assert mgr.invalidated == 1


@pytest.mark.asyncio
async def test_give_up_after_second_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    mgr = _FakeManager()
    monkeypatch.setattr("soongpt_mcp.server.get_session_manager", lambda: mgr)

    async def service(session: str) -> str:
        raise SSOTokenError("still expired")

    with pytest.raises(RuntimeError, match="재로그인 후에도"):
        await _run_with_session(service)
    assert mgr.invalidated == 1


@pytest.mark.asyncio
async def test_session_error_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    mgr = _FakeManager()
    mgr.get_valid_session = _failing_get  # type: ignore[method-assign]
    monkeypatch.setattr("soongpt_mcp.server.get_session_manager", lambda: mgr)

    async def service(session: str) -> str:
        return "ok"

    with pytest.raises(RuntimeError, match="로그인 자동 진행 실패"):
        await _run_with_session(service)


async def _failing_get(*args, **kwargs):
    raise SessionError("boom")
