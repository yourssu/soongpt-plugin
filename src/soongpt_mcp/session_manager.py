"""세션 캐시 + 온디맨드 웹 로그인 관리.

동시 툴 호출 직렬화. 첫 툴 호출이 웹 로그인을 트리거하고,
병렬로 들어온 다른 툴 호출은 Lock 대기 후 갱신된 세션을 재사용.
세션 만료(SSOTokenError) 시 invalidate() → 다음 호출이 자동 재로그인.
"""
from __future__ import annotations

import asyncio

from .auth import AuthError, load_session, save_session
from .web_login import WebLoginError, run_web_login


class SessionError(RuntimeError):
    """세션 확보 실패 (웹 로그인 타임아웃, keyring 오류 등)."""


class SessionManager:
    """세션 캐시 + 직렬화 + 온디맨드 웹 로그인."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._cached: str | None = None
        self._invalidated: bool = False

    def invalidate(self) -> None:
        """캐시 무효화. 다음 get_valid_session()이 웹 로그인을 강제."""
        self._cached = None
        self._invalidated = True

    async def get_valid_session(self, *, force_relogin: bool = False) -> str:
        """유효한 세션 JSON 반환.

        흐름:
        1. force_relogin=False & 캐시 유효 → 즉시 반환
        2. force_relogin=False & keyring에 저장된 세션 있 → 캐싱 후 반환
        3. 그 외 → 웹 로그인 트리거 → 세션 저장 → 캐싱 후 반환

        동시 호출자는 Lock으로 직렬화됨.
        """
        async with self._lock:
            if not force_relogin and self._cached and not self._invalidated:
                return self._cached

            if not force_relogin and not self._invalidated:
                try:
                    stored = load_session()
                except AuthError as exc:
                    raise SessionError(str(exc)) from exc
                if stored:
                    self._cached = stored
                    return stored

            try:
                session_json = await run_web_login()
            except WebLoginError as exc:
                raise SessionError(str(exc)) from exc

            try:
                save_session(session_json)
            except AuthError as exc:
                raise SessionError(str(exc)) from exc

            self._cached = session_json
            self._invalidated = False
            return session_json


_manager: SessionManager | None = None


def get_session_manager() -> SessionManager:
    global _manager
    if _manager is None:
        _manager = SessionManager()
    return _manager
