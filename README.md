# soongpt-mcp

숭실대 SSAINT(u-saint) 데이터를 Claude Code(CLI)에서 가져오는 **로컬 MCP 서버**. [rusaint](https://github.com/EATSTEAK/rusaint) 라이브러리 기반.

Claude Code 대화창에서 "내 졸업 요건 확인해줘"라고 치면 알아서 SSAINT에서 데이터를 가져와 분석해줍니다.

## 특징

- **로컬에서 동작**: 서버/DB/인프라 필요 없이 내 컴퓨터에서만 실행
- **보안**: 학번/비밀번호는 디스크에 저장되지 않음. OS 키체인 사용
- **가공 전 데이터 제공**: 데이터 해석/추천 로직은 Claude에게 맡김
- **2개 도구**: 학적/수강/성적 + 졸업사정표

## 도구

| 도구 | 반환 데이터 | 소요 시간 |
|---|---|---|
| `get_usaint_snapshot` | 학적 정보, 학기별 수강 과목, 저성적(C/D/F) 과목, 복수전공/부전공/교직 플래그 | ~9초 |
| `get_graduation_status` | 졸업 요건 상세 항목 + 카테고리별 충족 여부 + 잔여 학점 | ~6초 |

두 도구는 병렬로 동시 호출 가능 (Claude가 알아서 처리).

## 요구사항

- Python 3.10 이상
- macOS / Windows / Linux (GUI). Linux headless는 keyring 백엔드 필요
- Claude Code CLI ([설치 가이드](https://docs.claude.com/en/docs/claude-code))

## 설치

저장소 클론 후 개발 모드로 설치:

```bash
git clone https://github.com/yourssu/soongpt-mcp.git
cd soongpt-mcp
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e .
```

## 최초 로그인

터미널에서 아래 명령어 실행 후 학번/비밀번호 입력:

```bash
soongpt-mcp-login
```

성공 시 rusaint 세션 JSON이 OS 키체인(macOS Keychain / Windows Credential Manager / Linux Secret Service)에 저장됩니다. **학번과 비밀번호는 디스크에 저장되지 않습니다.**

## Claude Code에 연결

**User scope**(권장, 어디서든 사용):

```bash
claude mcp add -s user soongpt-mcp -- /절대/경로/soongpt-mcp/.venv/bin/python -m soongpt_mcp
```

> venv 안의 `python` 실행파일을 절대 경로로 지정해야 rusaint/mcp 의존성이 다 보입니다.

연결 확인:

```bash
claude mcp list
# soongpt-mcp: ... - ✓ Connected
```

## 사용 예시

Claude Code 대화창에서:

- **"내 수강 정보 가져와"** → `get_usaint_snapshot` 호출, 학적/수강 내역/저성적 과목 반환
- **"내 졸업 요건 확인해줘"** → 두 도구 병렬 호출, 부족 학점/미충족 항목 분석
- **"재수강하면 좋은 과목 추천해줘"** → 저성적 과목 기반 추천
- **"이번 학기 시간표 짜줘"** → 안 들은 전공필수 + 사용자 선호 기반 추천 (후속 작업)

## 보안

- 학번/비밀번호는 **로그인 시점에만 메모리에서 사용**, 디스크 저장 안 함
- 인증에 필요한 최소 정보(rusaint session JSON)만 OS 키체인에 저장
- MCP 도구 시그니처에 학번/비밀번호 매개변수 노출 X
- 로깅에 학번은 `[:4]****` 마스킹 처리
- Claude 대화창에 학번/비밀번호를 절대 직접 입력하지 마세요

## 문제 해결

| 증상 | 해결 |
|---|---|
| "로그인이 필요합니다" | 터미널에서 `soongpt-mcp-login` 실행 |
| "세션이 만료되었습니다" | u-saint 세션은 보통 1~2시간 후 만료. `soongpt-mcp-login` 재실행 |
| `soongpt-mcp-login: command not found` | `.venv` 활성화 안 됨. `source .venv/bin/activate` 후 재시도 |
| Linux headless에서 keyring 실패 | `SOONGPT_SESSION_JSON` 환경변수에 session JSON 직접 설정 |
| 도구가 Claude에 안 보임 | Claude Code 새 세션 시작 (MCP 도구는 세션 시작 시점에 로드) |

## 작동 방식

```
[Claude Code 대화창]
       ↓ MCP 도구 호출 (stdio)
[soongpt-mcp 서버 (Python)]
       ↓ keyring에서 세션 로드
       ↓ rusaint 라이브러리로 SSAINT 스크래핑
[숭실대 u-saint 서버]
       ↓ 데이터 반환
[soongpt-mcp 서버] → [Claude]이 데이터 해석/분석
```

## 기여

- [EATSTEAK/rusaint](https://github.com/EATSTEAK/rusaint) — SSAINT 스크래핑 라이브러리
- [soongpt-backend](https://github.com/yourssu/soongpt-backend) — 숭피티 웹 서비스 백엔드 (스크래핑 로직 참고)

## 라이선스

MIT
