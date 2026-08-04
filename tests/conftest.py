"""Pytest 설정 공통 픽스처."""
from __future__ import annotations

import asyncio
import secrets
import threading
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import pytest

from soongpt_mcp import web_login


@pytest.fixture(autouse=True)
def _isolate_plugin_data_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """모든 테스트에서 캐시(강의/스냅샷/후보/인터뷰)를 임시 디렉토리에 격리.

    find_lectures 자동 저장(SPR-75)처럼 테스트가 캐시 파일을 직접 쓰는 도구를
    호출하므로, 실사용자 캐시(~/.local/share/soongpt-mcp) 오염을 막는다.
    테스트별 tmp_path는 isolated_root 등 개별 픽스처가 다시 덮어쓸 수 있다.
    """
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path))


def _wait_for_server(host: str, port: int, timeout: float = 2.0) -> None:
    """서버가 accept 준비될 때까지 짧게 폴링."""
    import socket
    import time

    deadline = time.monotonic() + timeout
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return
        except OSError as exc:
            last_err = exc
            time.sleep(0.02)
    raise AssertionError(f"server not ready on {host}:{port}: {last_err}")


class ServerHandle:
    def __init__(self, server: web_login._LoginServer, state: Any) -> None:
        self.server = server
        self.state = state
        self.thread = threading.Thread(target=server.serve_forever, daemon=True)

    def start(self) -> None:
        self.thread.start()
        _wait_for_server("127.0.0.1", self.port)

    @property
    def port(self) -> int:
        return self.server.server_address[1]

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def get(self, path: str = "/") -> tuple[int, str]:
        with urllib.request.urlopen(self.url.rstrip("/") + path) as resp:
            return resp.code, resp.read().decode("utf-8")

    def post(self, path: str, fields: dict[str, str]) -> tuple[int, str]:
        data = urlencode(fields).encode("utf-8")
        try:
            with urllib.request.urlopen(
                self.url.rstrip("/") + path, data=data
            ) as resp:
                return resp.code, resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8")


@pytest.fixture
def login_server():
    """실제 localhost 서버를 띄우는 컨텍스트 매니저.

    state.future 해결을 위해 백그라운드 스레드에서 이벤트 루프 실행.
    """
    loop = asyncio.new_event_loop()
    loop_thread = threading.Thread(target=loop.run_forever, daemon=True)
    loop_thread.start()

    state = web_login._LoginState(loop)
    server = web_login._bind_server(state)
    state.server = server
    handle = ServerHandle(server, state)
    handle.start()
    try:
        yield handle
    finally:
        handle.stop()
        loop.call_soon_threadsafe(loop.stop)
        loop_thread.join(timeout=2)
        loop.close()


@pytest.fixture
def fresh_state():
    """테스트용 state 객체 (서버 바인딩 없음)."""
    loop = asyncio.new_event_loop()
    try:
        state = web_login._LoginState(loop)
        state.csrf_token = secrets.token_urlsafe(16)
        yield state
    finally:
        loop.close()
