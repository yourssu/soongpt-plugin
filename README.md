# soongpt-mcp

숭실대 SSAINT(u-saint) 데이터를 Claude Code(CLI)에서 가져오는 **로컬 MCP 서버**. [rusaint](https://github.com/EATSTEAK/rusaint) 라이브러리 기반.

Claude Code 대화창에서 "내 졸업 요건 확인해줘"라고 치면 알아서 SSAINT에서 데이터를 가져와 분석해줍니다.

## 특징

- **로컬에서 동작**: 서버/DB/인프라 필요 없이 내 컴퓨터에서만 실행
- **터미널 명령어 불필요**: 첫 사용 시 자동으로 브라우저가 열려 로그인 폼을 제공
- **보안**: 학번/비밀번호는 디스크에 저장되지 않음. OS 키체인만 사용
- **가공 전 데이터 제공**: 데이터 해석/추천 로직은 Claude에게 맡김
- **3개 도구**: 학적/수강/성적, 졸업사정표, 강의시간표 검색

## 도구

| 도구 | 반환 데이터 | 소요 시간 |
|---|---|---|
| `get_usaint_snapshot` | 학적 정보, 학기별 수강 과목, 저성적(C/D/F) 과목, 복수전공/부전공/교직 플래그 | ~9초 |
| `get_graduation_status` | 졸업 요건 상세 항목 + 카테고리별 충족 여부 + 잔여 학점 | ~6초 |
| `find_lectures` | 특정 학기/카테고리 강의 검색 (강의계획서 옵션) | ~3초 |

여러 도구는 병렬로 동시 호출 가능 (Claude가 알아서 처리).

## 요구사항

- Python 3.10 이상
- macOS / Windows / Linux (GUI). Linux headless는 폴백 환경변수 사용 (아래 참고)
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

## 로그인 (자동)

별도의 로그인 명령어는 필요 없습니다. Claude 대화창에서 처음으로 데이터를 요청하는 순간 자동으로 진행됩니다.

1. 예: "내 졸업 요건 확인해줘"
2. MCP 서버가 세션 없음을 감지 → **자동으로 기본 브라우저 열림** (localhost)
3. 브라우저 폼에 학번 / uSaint 비밀번호 입력 → 제출
4. 인증 성공 → 세션 JSON이 OS 키체인에 저장 → 브라우저 탭에 성공 메시지
5. 원래 요청이 갱신된 세션으로 자동 재실행 → 결과 반환

세션이 만료된 경우(보통 1~2시간)에도 동일하게 자동 재로그인이 진행됩니다.

### 비밀번호 저장 여부

- **학번/비밀번호**: 어디에도 저장되지 않음. 인증 직후 메모리에서 삭제
- **세션 JSON** (rusaint가 만들어주는 SSO 토큰 + 쿠키 묶음): OS 키체인에만 저장. 만료 시 자동 폐기

## 사용 예시

Claude Code 대화창에서:

- **"내 수강 정보 가져와"** → `get_usaint_snapshot` 호출, 학적/수강 내역/저성적 과목 반환
- **"내 졸업 요건 확인해줘"** → 두 도구 병렬 호출, 부족 학점/미충족 항목 분석
- **"재수강하면 좋은 과목 추천해줘"** → 저성적 과목 기반 추천
- **"이번 학기 전공필수 강의 보여줘"** → `find_lectures` 로 시간표 검색

## 보안

- 학번/비밀번호는 **로그인 시점에만 메모리에서 사용**, 디스크/로그 저장 금지
- 인증에 필요한 최소 정보(rusaint session JSON)만 OS 키체인에 저장
- MCP 도구 시그니처에 학번/비밀번호 매개변수 노출 X
- 로컬 웹 서버는 `127.0.0.1` 전용 바인딩 (외부 접근 차단)
- 1회성 CSRF 토큰으로 로컬 다른 프로세스의 폼 제출 공격 차단
- Claude 대화창에 학번/비밀번호를 절대 직접 입력하지 마세요

## 문제 해결

| 증상 | 해결 |
|---|---|
| 최초 도구 호출 시 브라우저가 안 열려요 | 브라우저 자동 오픈 실패. 터미널 stderr에 출력된 URL을 직접 복사해서 여세요. 또는 SSH/headless 환경이라 아래 폴백 사용 |
| "로그인 대기 시간 초과" | 5분 안에 폼 제출 안 함. Claude에게 다시 요청하세요 |
| "세션이 만료되었습니다" | 자동으로 재로그인이 진행되지만, 실패한 경우. Claude에게 다시 요청하면 됩니다 |
| Linux headless에서 브라우저 폼 불가 | `SOONGPT_SESSION_JSON` 환경변수에 rusaint 세션 JSON 직접 설정 (별도 환경에서 발급 필요) |
| 도구가 Claude에 안 보임 | Claude Code 새 세션 시작 (MCP 도구는 세션 시작 시점에 로드) |
| 포트 충돌 (8765~8770 사용 중) | 자동으로 다른 빈 포트로 fallback. 문제 시 stderr의 "로그인 URL" 라인 확인 |

## 작동 방식

```
[Claude Code 대화창]
       ↓ MCP 도구 호출 (stdio)
[soongpt-mcp 서버 (Python)]
       ↓ keyring에서 세션 로드
       ↓ 없음/만료 → 자동 localhost 웹 서버 + 브라우저 오픈
       ↓ 사용자 학번/비번 입력 → rusaint 인증 → 세션 JSON → keyring 저장
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
