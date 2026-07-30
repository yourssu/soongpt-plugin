# soongpt-mcp

숭실대 USAINT(u-saint) 데이터를 Claude Code(CLI)에서 가져오는 **로컬 MCP 서버**. [rusaint](https://github.com/EATSTEAK/rusaint) 라이브러리 기반.

Claude Code 대화창에서 "내 졸업 요건 확인해줘"라고 치면 알아서 USAINT에서 데이터를 가져와 분석해줍니다.

## 특징

- **로컬에서 동작**: 서버/DB/인프라 필요 없이 내 컴퓨터에서만 실행
- **터미널 명령어 불필요**: 첫 사용 시 자동으로 브라우저가 열려 로그인 폼을 제공
- **보안**: 학번/비밀번호는 디스크에 저장되지 않음. OS 키체인만 사용
- **가공 전 데이터 제공**: 데이터 해석/추천 로직은 Claude에게 맡김
- **사용자 프로필 영속화**: 매번 USAINT를 호출하지 않고 학적 컨텍스트를 로컬에 저장
- **13개 도구**: 학적/수강/성적, 졸업사정표(30일 캐싱), 강의시간표 검색, 교양선택 분야 목록, 강의 캐시 로드/저장, 학과-단과대 매핑(1년 캐싱 + 번들 seed), 프로필 조회/설정/갱신, 인터뷰 조회/설정/목록

## 도구

| 도구 | 반환 데이터 | 소요 시간 |
|---|---|---|
| `get_usaint_snapshot` | 학적 정보, 학기별 수강 과목, 저성적(C/D/F) 과목, 복수전공/부전공/교직 플래그 | ~9초 |
| `get_graduation_status` | 졸업 요건 상세 + 카테고리별 충족 여부 + 잔여 학점 (30일 캐시, `force_refresh` 옵션) | 캐시 hit 즉시 / 미스 ~6초 |
| `find_lectures` | 특정 학기/카테고리 강의 검색 (강의계획서 옵션) | ~3초 |
| `list_optional_elective_categories` | 해당 학기 교양선택 분야 목록 (학번별 '[‘NN이후]' 분류 포함) | ~3초 |
| `load_lectures_cache` | 저장된 강의 캐시 로드 (7일 TTL, `_cache.source`로 hit/stale/miss 구분) | 즉시 |
| `save_lectures_cache` | 스킬이 find_lectures N회 결과를 취합해 캐시로 적재 | 즉시 |
| `load_department_map` | 학과-단과대 매핑 (로컬 캐시 → 번들 seed → 자동 빌드, `force_refresh` 옵션) | 캐시/seed hit 즉시 / 미스 ~10-20초 |
| `get_user_profile` | 저장된 사용자 프로필 (없으면 안내) | 즉시 |
| `set_user_profile` | 단일 필드 부분 업데이트 (학번/이름/단과대/학과/학년/트랙/입학연도/복수전공/연계융합전공/부전공/교직이수여부/교직전공) | 즉시 |
| `refresh_user_profile` | USAINT basicInfo로 학과/학년/입학연도/복수전공/연계융합전공/부전공/교직이수 재동기화 (사용자 수정값 보존 옵션) | ~2-3초 |
| `get_interview` | 특정 학기 인터뷰 결과 (3개 섹션) + completion 맵 | 즉시 |
| `set_interview` | 인터뷰 섹션 텍스트 저장 (자연어 요약, 덮어쓰기) | 즉시 |
| `list_interviews` | 모든 학기 인터뷰 메타 목록 | 즉시 |

여러 도구는 병렬로 동시 호출 가능 (Claude가 알아서 처리).

## 사용자 프로필

학적 컨텍스트(학번/이름/단과대/주전공/학년/트랙/입학연도/복수전공/연계융합전공/부전공/교직이수여부/교직전공)를 로컬 JSON에 저장하여 매번 `get_usaint_snapshot`을 호출하지 않아도 됩니다. 학기별 스냅샷(`profile_{year}_{semester}.json`)으로 관리되어 전과/학년 증가/세부전공 변경 시 과거 학기 컨텍스트가 보존됩니다.

**저장 위치**: `${CLAUDE_PLUGIN_DATA}/profile_{year}_{semester}.json` (없으면 `~/.claude/state/soongpt-planner/profile_{year}_{semester}.json`)

**레거시 마이그레이션**: SPR-30의 단일 `profile.json`이 있으면 현재 학기 파일이 없을 때 자동 읽기 fallback → 다음 save 시 새 경로로 이전.

**USAINT가 채우는 필드** (8개): `department`, `grade`, `entered_year`, `double_major`(복수전공), `connected_major`(연계·융합전공), `minor`(부전공), `teaching_certification`(교직이수 여부), `teaching_major`(교직 전공명) — `refresh_user_profile`로 동기화 (학적 기본 정보만 가볍게 조회, ~2-3초)
**사용자 입력 필드** (4개): `student_id`, `name`, `college`, `track` — `set_user_profile`로 직접 입력

### 워크플로우

```
1. 처음: "내 프로필 설정해줘"
   → refresh_user_profile() 호출 → USAINT에서 8개 필드 추출 저장
   → set_user_profile("student_id", "20240001") 로 학번 입력
   → set_user_profile("name", "홍길동") 으로 이름 입력
   → set_user_profile("grade", 3) 처럼 정수 필드는 숫자로 (문자열 "3"도 자동 변환됨)

2. 휴학/복학/전과 후: "내 프로필 업데이트해줘"
   → refresh_user_profile(preserve_user_overrides=True)
   → USAINT 8개 필드 갱신, 사용자가 입력한 학번/이름 등은 보존

3. 이후: 저장된 프로필 기반으로 시간표 추천/졸업 분석 (매번 USAINT 호출 X)
```

> 참고: 동시 호출 시 마지막 write가 이길 수 있습니다. MCP 클라이언트가 보통 직렬 호출하지만, 병렬 실행 시 lost update 가능성이 있습니다. 파일 쓰기는 atomic rename으로 크래시 중 손상을 방지합니다.

## 학과-단과대 매핑

복수/부전공 학과의 단과대를 자동으로 찾기 위한 `{학과명: 단과대}` 매핑. 숭실대 학과 구조는 연 1~2회 변경되므로 1년 TTL로 캐싱.

**3-tier 로딩 순서**:
1. **로컬 캐시** (즉시): `${CLAUDE_PLUGIN_DATA}/department_map_{year}.json` (폴백 `~/.claude/state/soongpt-planner/`)
2. **번들 seed** (즉시): 패키지에 커밋된 정적 파일 `src/soongpt_mcp/data/department_map_{year}.json`
3. **자동 빌드** (10~20초): USAINT 강의시간표에서 모든 단과대를 순회하며 빌드 → 로컬 캐시에 저장

`_cache.source` 응답 필드로 출처 추적 (`cache` | `bundled` | `fresh`).

**메인테이너 워크플로우** (연 1회, 학기 시작 전): seed 파일이 없거나 stale 할 때 신규 사용자 첫 호출이 10~20초 걸리므로, 메인테이너가 직접 갱신해 커밋하는 것이 권장됨.

```bash
# 1. fresh 빌드 (USAINT 세션 필요)
#    MCP 클라이언트에서 load_department_map(year=2026, force_refresh=True) 호출

# 2. 생성된 로컬 캐시를 repo의 seed 위치로 복사
cp ${CLAUDE_PLUGIN_DATA}/department_map_2026.json \
   src/soongpt_mcp/data/department_map_2026.json

# 3. 커밋 — 이후 모든 신규 사용자는 0초 seed 사용
```

학과가 신설/통폐합되면 사용자가 `force_refresh=True`로 즉시 갱신 가능 (안전망).

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

### 교양선택 분야 조회

`list_optional_elective_categories(year, semester)`는 해당 학기에 개설된 교양선택 분야 목록을 반환합니다. 분야명은 학번에 따라 `[‘23이후]과학·기술` 형태로 태그되어 있으므로, 사용자의 입학연도(`profile.entered_year`) 기준으로 해당하는 분야만 걸러내는 것은 호출자(스킬/LLM)의 책임입니다.

```python
# 반환 스키마
{
  "categories": ["[‘23이후]과학·기술", "[‘23이후]인문·예술", ...],
  "count": 12,
  "fetchTime": "2.84s"
}

# 카테고리가 채워진 후 (후속 PR)
{
  "groups": {
    "major": {"lectures": [...], "count": N, "error": null},
    "chapel": {"lectures": [...], "count": M, "error": null}
  },
  "totalCount": N + M,
  ...
}
```

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
       ↓ rusaint 라이브러리로 USAINT 스크래핑
[숭실대 u-saint 서버]
       ↓ 데이터 반환
[soongpt-mcp 서버] → [Claude]이 데이터 해석/분석
```

## 기여

- [EATSTEAK/rusaint](https://github.com/EATSTEAK/rusaint) — USAINT 스크래핑 라이브러리
- [soongpt-backend](https://github.com/yourssu/soongpt-backend) — 숭피티 웹 서비스 백엔드 (스크래핑 로직 참고)

## 라이선스

MIT
