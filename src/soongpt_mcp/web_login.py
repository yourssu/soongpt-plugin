"""localhost 브라우저 로그인 플로우.

127.0.0.1 전용 바인딩, 자동 포트 선택, CSRF 토큰, 1회성 서버.
MCP 서버 프로세스 안에서 온디맨드로 실행되며, 사용자가 폼을 제출하면
rusaint 세션 JSON을 반환하고 종료.
"""
from __future__ import annotations

import asyncio
import html
import secrets
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

from ._authenticate import AuthenticateError, authenticate

DEFAULT_TIMEOUT_SECONDS = 300
PREFERRED_PORTS = (8765, 8766, 8767, 8768, 8769, 8770)
HOST = "127.0.0.1"
MAX_BODY_BYTES = 8192

_PAGE = """\
<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>soongpt-mcp 로그인</title>
<style>
:root {{ color-scheme: light dark; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
       max-width: 420px; margin: 60px auto; padding: 0 20px; color: #111; }}
h1 {{ font-size: 1.4rem; margin-bottom: .4rem; }}
p.note {{ color: #666; font-size: .9rem; margin-top: 0; }}
label {{ display: block; margin: 1rem 0 .3rem; font-size: .9rem; }}
input[type=text], input[type=password] {{
    width: 100%; padding: .6rem; font-size: 1rem;
    box-sizing: border-box; border: 1px solid #ccc; border-radius: 6px; }}
button {{ margin-top: 1.4rem; width: 100%; padding: .8rem; font-size: 1rem;
    background: #2563eb; color: white; border: 0; border-radius: 6px; cursor: pointer; }}
button:hover {{ background: #1d4ed8; }}
.error {{ color: #b91c1c; margin-top: 1rem; font-size: .9rem; }}
.success {{ text-align: center; }}
.success h1 {{ color: #16a34a; }}
@media (prefers-color-scheme: dark) {{
    body {{ color: #eee; }}
    p.note {{ color: #999; }}
    input[type=text], input[type=password] {{ background: #1f2937; border-color: #374151; color: #eee; }}
}}
</style>
</head>
<body>
{body}
</body>
</html>
"""

_FORM = """
<h1>숭실대 uSaint 로그인</h1>
<p class="note">학번과 uSaint 비밀번호를 입력하세요. 비밀번호는 인증 직후 메모리에서 삭제되며, 디스크/로그에는 저장되지 않습니다.</p>
<form method="post" action="/submit" autocomplete="off">
  <input type="hidden" name="csrf_token" value="{csrf_token}">
  <label for="student_id">학번</label>
  <input type="text" id="student_id" name="student_id" inputmode="numeric" autocomplete="username" required>
  <label for="password">uSaint 비밀번호</label>
  <input type="password" id="password" name="password" autocomplete="current-password" required>
  <button type="submit">로그인</button>
  {error_html}
</form>
"""

_ERROR = '<p class="error">{message}</p>'

_SUCCESS = """
<div class="success">
<h1>✓ 로그인 성공</h1>
<p>세션이 안전하게 저장되었습니다. 이 탭을 닫아도 됩니다.</p>
</div>
"""


class WebLoginError(RuntimeError):
    """웹 로그인 플로우 실패 (타임아웃, 서버 바인딩 실패 등)."""


class _LoginState:
    """HTTP 처리 스레드와 비동기 호출자 사이의 공유 상태."""

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop
        self.future: asyncio.Future[str] = loop.create_future()
        self.csrf_token: str = secrets.token_urlsafe(32)
        self.server: _LoginServer | None = None

    def resolve(self, session_json: str) -> None:
        if not self.future.done():
            self.loop.call_soon_threadsafe(self.future.set_result, session_json)

    def request_shutdown(self) -> None:
        if self.server is not None:
            threading.Thread(target=self.server.shutdown, daemon=True).start()


class _LoginServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, addr: tuple[str, int], handler_cls: type[BaseHTTPRequestHandler], state: _LoginState) -> None:
        super().__init__(addr, handler_cls)
        self.state = state

    def handle_error(self, request, client_address) -> None:
        # 클라이언트가 일찍 연결을 끊는 경우 등 무해한 예외 로그 억제.
        # 실제 처리 중 발생한 예외는 do_POST 내부에서 try/except로 처리됨.
        return


class _LoginHandler(BaseHTTPRequestHandler):
    server_version = "soongpt-mcp-login/1.0"

    @property
    def _state(self) -> _LoginState:
        return self.server.state  # type: ignore[attr-defined]

    def log_message(self, format: str, *args) -> None:
        # 로깅 비활성화 (stdout/stderr 오염 방지)
        return

    def _send_html(self, status: int, body: str) -> None:
        encoded = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'")
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self) -> None:
        if self.path not in ("/", "/index.html"):
            self._send_html(404, "<h1>Not Found</h1>")
            return
        body = _FORM.format(csrf_token=self._state.csrf_token, error_html="")
        self._send_html(200, _PAGE.format(body=body))

    def do_POST(self) -> None:
        if self.path != "/submit":
            self._send_html(404, "<h1>Not Found</h1>")
            return
        if self._state.future.done():
            self._send_html(200, _PAGE.format(body="<p>이미 로그인이 완료되었습니다.</p>"))
            return

        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0 or length > MAX_BODY_BYTES:
            self._send_html(400, "<h1>Bad Request</h1>")
            return
        try:
            raw = self.rfile.read(length).decode("utf-8", errors="replace")
        except OSError:
            self._send_html(400, "<h1>Bad Request</h1>")
            return

        form = {k: v[0] for k, v in parse_qs(raw, keep_blank_values=True).items()}
        if form.get("csrf_token") != self._state.csrf_token:
            self._send_html(403, "<h1>Forbidden</h1><p>CSRF 토큰이 유효하지 않습니다. 페이지를 새로고침하세요.</p>")
            return

        student_id = (form.get("student_id") or "").strip()
        password = form.get("password") or ""
        form["password"] = ""  # 즉시 폼에서 제거
        if not student_id or not password:
            self._render_form_error("학번과 비밀번호를 모두 입력하세요.")
            del password
            return

        try:
            session_json = asyncio.run(authenticate(student_id, password))
        except AuthenticateError as exc:
            self._render_form_error(str(exc))
            del password
            return
        except TimeoutError:
            self._render_form_error("인증 응답 대기 시간 초과. 다시 시도하세요.")
            del password
            return
        except Exception as exc:  # noqa: BLE001 - rusaint/네트워크 예외 안전망
            self._render_form_error(f"인증 중 오류가 발생했습니다: {exc}")
            del password
            return
        finally:
            del password

        # 성공: 응답 전송 → 종료 예약 → 퓨처 resolve
        self._send_html(200, _PAGE.format(body=_SUCCESS))
        self._state.resolve(session_json)
        self._state.request_shutdown()

    def _render_form_error(self, message: str) -> None:
        body = _FORM.format(
            csrf_token=self._state.csrf_token,
            error_html=_ERROR.format(message=html.escape(message)),
        )
        self._send_html(200, _PAGE.format(body=body))


def _bind_server(state: _LoginState) -> _LoginServer:
    """선호 포트 순차 시도 후 OS 할당 fallback."""
    for port in PREFERRED_PORTS:
        try:
            return _LoginServer((HOST, port), _LoginHandler, state)
        except OSError:
            continue
    try:
        return _LoginServer((HOST, 0), _LoginHandler, state)
    except OSError as exc:
        raise WebLoginError(f"localhost 서버 바인딩 실패: {exc}") from exc


async def run_web_login(
    *,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    open_browser: bool = True,
    print_url: bool = True,
) -> str:
    """localhost 웹 서버를 띄워 로그인 폼을 제공하고 세션 JSON을 기다림.

    반환값: rusaint 세션 JSON 문자열. 호출자가 keyring에 저장할 책임.
    """
    loop = asyncio.get_running_loop()
    state = _LoginState(loop)
    server = _bind_server(state)
    state.server = server

    port = server.server_address[1]
    url = f"http://{HOST}:{port}/"

    if open_browser:
        opened = webbrowser.open(url)
        if not opened and print_url:
            print(f"브라우저를 열 수 없습니다. URL을 직접 여세요: {url}", file=sys.stderr)
    elif print_url:
        print(f"로그인 URL: {url}", file=sys.stderr)

    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    try:
        session_json = await asyncio.wait_for(state.future, timeout=timeout_seconds)
    except asyncio.TimeoutError as exc:
        raise WebLoginError(
            f"로그인 대기 시간 초과 ({timeout_seconds}초). 다시 시도하세요."
        ) from exc
    finally:
        state.request_shutdown()
        server_thread.join(timeout=5)
        server.server_close()

    return session_json
