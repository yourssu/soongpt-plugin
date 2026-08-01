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

# 로그인 페이지는 정적 인라인 <style> 블록만 사용하므로 'unsafe-inline'을 허용.
# 사용자 입력을 style에 주입하는 코드는 추가하지 말 것 (XSS 위험).
# script-src 미지정 시 default-src 'self' fallback → 인라인 스크립트는 계속 차단됨.
_CSP_HEADER = "default-src 'self'; style-src 'self' 'unsafe-inline'"

_PAGE = """\
<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>숭실대 uSaint 로그인</title>
<style>
:root {{
  color-scheme: light dark;
  --brand-primary: #6b5cff;
  --brand-secondary: #5736f5;
  --text-default: #292929;
  --text-muted: #4b505d;
  --text-subtle: #686868;
  --text-placeholder: #cfcfcf;
  --bg-page: #f7f8f8;
  --bg-brand-light: #ecefff;
  --bg-card: #ffffff;
  --border-default: #e3e4e8;
  --radius-sm: 6px;
  --radius-md: 8px;
  --radius-lg: 10px;
  --radius-xl: 14px;
}}
* {{ box-sizing: border-box; }}
body {{
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
  margin: 0;
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 40px 20px;
  background: linear-gradient(180deg, var(--bg-brand-light) 0%, var(--bg-page) 45%);
  color: var(--text-default);
}}
.card {{
  width: 100%;
  max-width: 380px;
  background: var(--bg-card);
  border-radius: var(--radius-xl);
  padding: 2.4rem 2rem;
  box-shadow: 0 2px 4px rgba(41,41,41,.04), 0 12px 32px rgba(107,92,255,.12);
}}
.logo {{ display: flex; justify-content: center; margin-bottom: 1.2rem; }}
.logo svg {{ width: 42px; height: 56px; }}
h1 {{ font-size: 1.3rem; font-weight: 700; text-align: center; margin: 0 0 .5rem; color: var(--text-default); }}
p.note {{ color: var(--text-subtle); font-size: .85rem; line-height: 1.5; text-align: center; margin: 0 0 1.6rem; }}
label {{ display: block; margin: 1.1rem 0 .4rem; font-size: .82rem; font-weight: 600; color: var(--text-muted); }}
input[type=text], input[type=password] {{
  width: 100%; padding: .75rem .85rem; font-size: 1rem;
  border: 1px solid var(--border-default); border-radius: var(--radius-md);
  background: var(--bg-page); color: var(--text-default);
  transition: border-color .15s, box-shadow .15s;
}}
input[type=text]::placeholder, input[type=password]::placeholder {{ color: var(--text-placeholder); }}
input[type=text]:focus, input[type=password]:focus {{
  outline: none; border-color: var(--brand-primary); background: #fff;
  box-shadow: 0 0 0 3px var(--bg-brand-light);
}}
button {{
  margin-top: 1.6rem; width: 100%; padding: .85rem; font-size: 1rem; font-weight: 700;
  background: var(--brand-primary); color: #fff; border: 0; border-radius: var(--radius-md);
  cursor: pointer; transition: background .15s;
}}
button:hover {{ background: var(--brand-secondary); }}
button:active {{ transform: translateY(1px); }}
.error {{
  margin-top: 1rem; padding: .7rem .85rem; border-radius: var(--radius-md);
  background: #fdecec; color: #b91c1c; font-size: .85rem; line-height: 1.4;
}}
.success {{ text-align: center; }}
.success .check {{
  width: 52px; height: 52px; margin: 0 auto 1rem; border-radius: 50%;
  background: var(--bg-brand-light); color: var(--brand-primary);
  display: flex; align-items: center; justify-content: center; font-size: 1.6rem;
}}
.success h1 {{ margin-bottom: .4rem; }}
.success p {{ color: var(--text-subtle); font-size: .85rem; }}
.local-badge {{
  display: flex; align-items: center; justify-content: center; gap: .35rem;
  margin: 0 0 1.2rem; padding: .3rem .7rem; border-radius: 999px;
  background: var(--bg-brand-light); color: var(--brand-secondary);
  font-size: .72rem; font-weight: 600; text-align: center;
}}
@media (prefers-color-scheme: dark) {{
  body {{ background: linear-gradient(180deg, #1e1b33 0%, #121214 45%); color: #f1f1f3; }}
  .card {{ background: #1c1d22; box-shadow: 0 12px 32px rgba(0,0,0,.4); }}
  h1 {{ color: #f1f1f3; }}
  p.note, .success p {{ color: #9a9ea8; }}
  label {{ color: #c7cad2; }}
  input[type=text], input[type=password] {{ background: #26272e; border-color: #383a44; color: #f1f1f3; }}
  input[type=text]:focus, input[type=password]:focus {{ background: #2b2c34; box-shadow: 0 0 0 3px rgba(107,92,255,.25); }}
  .error {{ background: rgba(185,28,28,.15); color: #fca5a5; }}
  .success .check {{ background: rgba(107,92,255,.2); }}
  .local-badge {{ background: rgba(107,92,255,.18); color: #b9adff; }}
}}
</style>
</head>
<body>
<div class="card">
<div class="logo">
<svg width="300" height="400" viewBox="0 0 300 400" xmlns="http://www.w3.org/2000/svg">
  <path fill="#7870F4" d="M0 100h100v100H0zm100 100h100v100H100z"/>
  <path fill="#C3D5F2" d="M200 100h100v100H200zM0 300h100v100H0z"/>
  <path fill="#E6BFF2" d="M200 100H100a100 100 0 0 1 100-100v100zm-100 200v100a100 100 0 0 0 100-100h-100z"/>
</svg>
</div>
<div class="local-badge">&#128274; 로컬 환경에서만 실행되는 페이지입니다</div>
{body}
</div>
</body>
</html>
"""

_FORM = """
<h1>숭실대 uSaint 로그인</h1>
<p class="note">학번과 uSaint 비밀번호를 입력하세요. 비밀번호는 인증 직후 메모리에서 삭제되며, 디스크/로그에는 저장되지 않습니다.</p>
<form method="post" action="/submit" autocomplete="off">
  <input type="hidden" name="csrf_token" value="{csrf_token}">
  <label for="student_id">학번</label>
  <input type="text" id="student_id" name="student_id" inputmode="numeric" autocomplete="username" placeholder="학번을 입력하세요" required>
  <label for="password">uSaint 비밀번호</label>
  <input type="password" id="password" name="password" autocomplete="current-password" placeholder="비밀번호를 입력하세요" required>
  <button type="submit">로그인</button>
  {error_html}
</form>
"""

_ERROR = '<p class="error">{message}</p>'

_SUCCESS = """
<div class="success">
<div class="check">&#10003;</div>
<h1>로그인 성공</h1>
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
        # 인라인 <style> 허용을 위한 'unsafe-inline' — 무단 제거 금지 (정적 CSS 전용, _CSP_HEADER 주석 참조)
        self.send_header("Content-Security-Policy", _CSP_HEADER)
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
