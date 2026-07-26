# soongpt-mcp

숭실대 SSAINT 데이터를 Claude Code에서 가져오는 로컬 MCP 서버.

## 설치

```bash
pip install -e .
# 또는
pip install soongpt-mcp
```

## 최초 로그인

터미널에서 아래 명령어를 실행한 뒤 학번과 비밀번호를 입력합니다.

```bash
soongpt-mcp-login
```

로그인에 성공하면 rusaint 세션 JSON이 OS 키체인(macOS Keychain / Windows Credential Manager / Linux Secret Service)에 저장됩니다. 학번과 비밀번호는 디스크에 저장되지 않습니다.

## Claude Code 연결

```bash
claude mcp add soongpt-mcp -- python -m soongpt_mcp
```

## 사용

Claude Code 대화창에서 아래처럼 요청하면, Claude가 자동으로 `get_usaint_snapshot` 도구를 호출합니다.

- "내 수강 정보 가져와"
- "내 졸업 사정표 보여줘"
- "현재 학적 상태 알려줘"

## 보안

- 학번과 비밀번호는 로그인 시점에만 메모리에서 사용되고 디스크에 저장되지 않습니다.
- 인증에 필요한 최소 정보(rusaint session JSON)만 OS 키체인에 저장됩니다.
- MCP 도구 시그니처에는 학번/비밀번호 매개변수가 노출되지 않습니다.

## 문제 해결

- **"로그인이 필요합니다"** → 터미널에서 `soongpt-mcp-login`을 실행하세요.
- **"세션이 만료되었습니다"** → u-saint 세션은 보통 1~2시간 후 만료됩니다. `soongpt-mcp-login`을 다시 실행하세요.
- **Linux headless 환경에서 keyring이 실패하는 경우** → `SOONGPT_SESSION_JSON` 환경 변수에 로그인 시 출력된 session JSON 문자열을 직접 설정하세요.
