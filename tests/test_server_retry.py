"""server._run_with_session 재시도 로직 테스트."""
from __future__ import annotations

import pytest

from soongpt_mcp.server import _run_with_session
from soongpt_mcp.services.exceptions import (
    RusaintInternalError,
    SSOTokenError,
    is_no_lecture_error,
    is_session_expiry_error,
)
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

    with pytest.raises(RuntimeError, match="로그인 절차가 완료되지 않았습니다"):
        await _run_with_session(service)


async def _failing_get(*args, **kwargs):
    raise SessionError("boom")


@pytest.mark.asyncio
async def test_retry_on_session_expiry_parse_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """저장 세션이 만료되어 로그인 페이지 파싱 실패("Cannot find SSR Client form")가
    RusaintInternalError로 래핑된 경우에도 재로그인 후 재시도."""
    mgr = _FakeManager()
    monkeypatch.setattr("soongpt_mcp.server.get_session_manager", lambda: mgr)

    calls: list[str] = []

    async def service(session: str) -> str:
        calls.append(session)
        if session == "session-1":
            raise RusaintInternalError(
                "유세인트 데이터 조회 중 오류: RusaintError.General - "
                "Failed to parse HTML body: Given body document is invalid: "
                "Cannot find SSR Client form"
            )
        return "ok"

    result = await _run_with_session(service)
    assert result == "ok"
    assert calls == ["session-1", "session-2"]
    assert mgr.invalidated == 1


@pytest.mark.asyncio
async def test_retry_on_wrapped_parse_error_cause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """마커가 원인 체인(cause)에만 있을 때도 세션 만료로 분류되어 재로그인."""
    mgr = _FakeManager()
    monkeypatch.setattr("soongpt_mcp.server.get_session_manager", lambda: mgr)

    calls: list[str] = []

    async def service(session: str) -> str:
        calls.append(session)
        if session == "session-1":
            cause = RuntimeError("Cannot find SSR Client form")
            raise RusaintInternalError("감싸진 오류 메시지") from cause
        return "ok"

    result = await _run_with_session(service)
    assert result == "ok"
    assert calls == ["session-1", "session-2"]
    assert mgr.invalidated == 1


@pytest.mark.asyncio
async def test_no_retry_on_generic_internal_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """세션 만료 신호가 아닌 RusaintInternalError는 그대로 전파 (재로그인 안 함)."""
    mgr = _FakeManager()
    monkeypatch.setattr("soongpt_mcp.server.get_session_manager", lambda: mgr)

    async def service(session: str) -> str:
        raise RusaintInternalError("알 수 없는 내부 오류")

    with pytest.raises(RusaintInternalError, match="알 수 없는 내부 오류"):
        await _run_with_session(service)
    assert mgr.invalidated == 0


@pytest.mark.asyncio
async def test_give_up_after_second_expiry_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """재로그인 후에도 같은 파싱 실패가 나오면 포기 (막다른 오류 방지 메시지)."""
    mgr = _FakeManager()
    monkeypatch.setattr("soongpt_mcp.server.get_session_manager", lambda: mgr)

    async def service(session: str) -> str:
        raise RusaintInternalError("Cannot find SSR Client form")

    with pytest.raises(RuntimeError, match="재로그인 후에도"):
        await _run_with_session(service)
    assert mgr.invalidated == 1


def test_is_session_expiry_error_matches_marker() -> None:
    assert is_session_expiry_error(RusaintInternalError("Cannot find SSR Client form"))


def test_is_session_expiry_error_ignores_unrelated() -> None:
    assert not is_session_expiry_error(
        RusaintInternalError("Cannot find element from document")
    )
    assert not is_session_expiry_error(RuntimeError("일반 오류"))


def test_is_session_expiry_error_walks_cause_chain() -> None:
    cause = RuntimeError("Cannot find SSR Client form")
    wrapped = RusaintInternalError("래핑된 메시지")
    wrapped.__cause__ = cause
    assert is_session_expiry_error(wrapped)
    assert not is_session_expiry_error(RuntimeError("원인이 없는 오류"))


def test_is_session_expiry_error_walks_context_chain() -> None:
    """`raise X from e` 없이 래핑된 경우(__context__만)에도 탐지."""
    context = RuntimeError("Cannot find SSR Client form")
    wrapped = RusaintInternalError("래핑된 메시지")
    wrapped.__context__ = context
    assert is_session_expiry_error(wrapped)


def test_is_no_lecture_error_matches_marker() -> None:
    assert is_no_lecture_error(RusaintInternalError("No lecture found"))
    assert is_no_lecture_error(
        RusaintInternalError("유세인트 강의시간표 조회 중 오류: RusaintError - No lecture found")
    )


def test_is_no_lecture_error_ignores_unrelated() -> None:
    assert not is_no_lecture_error(
        RusaintInternalError("Cannot find element from document")
    )
    assert not is_no_lecture_error(
        RusaintInternalError("유세인트 연결 실패: Cannot find SSR Client form")
    )
    # 연계/융합전공의 정상적 실패("Cannot find ... option")는 미개설이 아니다 (SPR-79 회귀 방지).
    assert not is_no_lecture_error(
        RusaintInternalError("Cannot find ... option in .../CONNECT_MAJOR")
    )
    assert not is_no_lecture_error(RuntimeError("일반 오류"))


def test_is_no_lecture_error_walks_cause_chain() -> None:
    cause = RuntimeError("No lecture found")
    wrapped = RusaintInternalError("래핑된 메시지")
    wrapped.__cause__ = cause
    assert is_no_lecture_error(wrapped)
    assert not is_no_lecture_error(RuntimeError("원인이 없는 오류"))


def test_is_no_lecture_error_walks_context_chain() -> None:
    """`raise X from e` 없이 래핑된 경우(__context__만)에도 탐지."""
    context = RuntimeError("No lecture found")
    wrapped = RusaintInternalError("래핑된 메시지")
    wrapped.__context__ = context
    assert is_no_lecture_error(wrapped)
