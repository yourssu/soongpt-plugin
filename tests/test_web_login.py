"""web_login 모듈 테스트.

실제 ThreadingHTTPServer를 띄워서 GET/POST 동작 검증.
rusaint 호출은 authenticate 함수를 패치하여 스킵.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from soongpt_mcp import web_login
from soongpt_mcp._authenticate import AuthenticateError


def test_bind_server_uses_localhost_only() -> None:
    """바인딩 호스트가 127.0.0.1인지 확인 (0.0.0.0 차단)."""
    from types import SimpleNamespace

    state = SimpleNamespace()
    server = web_login._bind_server(state)
    try:
        host, port = server.server_address
        assert host == "127.0.0.1"
        assert 0 < port < 65536
        assert state is server.state
    finally:
        server.server_close()


def test_bind_server_fallback_on_busy_ports(monkeypatch: pytest.MonkeyPatch) -> None:
    """선호 포트가 모두 사용 중이면 port=0으로 OS 할당."""
    from types import SimpleNamespace

    real_init = web_login._LoginServer.__init__

    def fake_init(self, addr, handler, state, *args, **kwargs):
        # 선호 포트에 대해서는 바인딩 실패 시뮬레이션
        if addr[1] in web_login.PREFERRED_PORTS:
            raise OSError("address in use (simulated)")
        return real_init(self, addr, handler, state, *args, **kwargs)

    monkeypatch.setattr(web_login._LoginServer, "__init__", fake_init)

    state = SimpleNamespace()
    server = web_login._bind_server(state)
    try:
        _, port = server.server_address
        assert port not in web_login.PREFERRED_PORTS
        assert port > 0
    finally:
        server.server_close()


def test_get_returns_form_html(login_server) -> None:
    code, body = login_server.get("/")
    assert code == 200
    assert "숭실대 uSaint 로그인" in body
    assert 'name="csrf_token"' in body
    assert login_server.state.csrf_token in body
    assert 'name="student_id"' in body
    assert 'name="password"' in body


def test_get_health_check_headers(login_server) -> None:
    """보안 헤더가 포함되는지 확인."""
    import urllib.request

    with urllib.request.urlopen(login_server.url) as resp:
        assert resp.headers["Cache-Control"] == "no-store"
        assert resp.headers["X-Content-Type-Options"] == "nosniff"
        assert resp.headers["Content-Type"] == "text/html; charset=utf-8"
        csp = resp.headers["Content-Security-Policy"]
        assert "default-src 'self'" in csp
        assert "style-src 'self' 'unsafe-inline'" in csp


def test_post_invalid_csrf_returns_403(login_server) -> None:
    code, body = login_server.post(
        "/submit",
        {"csrf_token": "wrong", "student_id": "20210001", "password": "pw"},
    )
    assert code == 403
    assert "CSRF" in body


def test_post_missing_fields_rerenders_form(login_server) -> None:
    code, body = login_server.post(
        "/submit",
        {"csrf_token": login_server.state.csrf_token, "student_id": "", "password": ""},
    )
    assert code == 200
    assert "학번과 비밀번호를 모두 입력하세요" in body
    assert not login_server.state.future.done()


def test_post_valid_credentials_resolves_future(login_server) -> None:
    fake_auth = AsyncMock(return_value='{"session": "ok"}')
    with patch("soongpt_mcp.web_login.authenticate", fake_auth):
        code, body = login_server.post(
            "/submit",
            {
                "csrf_token": login_server.state.csrf_token,
                "student_id": "20210001",
                "password": "secret",
            },
        )
    assert code == 200
    assert "로그인 성공" in body
    assert login_server.state.future.done()
    assert login_server.state.future.result() == '{"session": "ok"}'
    fake_auth.assert_awaited_once_with("20210001", "secret")


def test_post_auth_failure_rerenders_form_with_error(login_server) -> None:
    fake_auth = AsyncMock(side_effect=AuthenticateError("잘못된 비밀번호"))
    with patch("soongpt_mcp.web_login.authenticate", fake_auth):
        code, body = login_server.post(
            "/submit",
            {
                "csrf_token": login_server.state.csrf_token,
                "student_id": "20210001",
                "password": "wrong",
            },
        )
    assert code == 200
    assert "잘못된 비밀번호" in body
    assert not login_server.state.future.done()


def test_post_after_complete_rejects_replay(login_server) -> None:
    login_server.state.future.set_result('{"already": "done"}')
    code, body = login_server.post(
        "/submit",
        {
            "csrf_token": login_server.state.csrf_token,
            "student_id": "20210001",
            "password": "secret",
        },
    )
    assert code == 200
    assert "이미 로그인이 완료" in body


def test_post_rejects_oversized_body(login_server) -> None:
    # CSRI body size 제한
    huge_password = "x" * (web_login.MAX_BODY_BYTES + 100)
    code, _ = login_server.post(
        "/submit",
        {
            "csrf_token": login_server.state.csrf_token,
            "student_id": "20210001",
            "password": huge_password,
        },
    )
    assert code == 400


@pytest.mark.asyncio
async def test_run_web_login_raises_on_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """타임아웃 발생 시 WebLoginError 발생."""
    fake_auth = AsyncMock(side_effect=lambda *a: asyncio.sleep(100))
    monkeypatch.setattr(web_login, "authenticate", fake_auth)
    monkeypatch.setattr(web_login.webbrowser, "open", lambda url: True)

    with pytest.raises(web_login.WebLoginError, match="로그인 대기 시간 초과") as exc_info:
        await asyncio.wait_for(
            web_login.run_web_login(timeout_seconds=1),
            timeout=5,
        )
    # SPR-85: LLM이 "시스템 오류"로 오판해 자동 재시도하지 않도록,
    # 브라우저 로그인 폼 절차임을 명시하고 사용자 안내 후 1회 재시도하도록 안내해야 한다.
    # 핵심 안티-재시도 문구("자동으로 반복 재시도하지 마세요")를 함께 검증해 회귀를 막는다.
    message = str(exc_info.value)
    assert "웹 로그인 폼" in message
    assert "사용자 로그인 절차" in message
    assert "자동으로 반복 재시도하지 마세요" in message
