# soongpt-plugin

숭실대 USAINT(u-saint) 데이터를 Claude Code / Codex에서 다루게 해주는 **플러그인**(로컬 MCP 서버 + 시간표 스킬을 한 번에 번들). [rusaint](https://github.com/EATSTEAK/rusaint) 라이브러리 기반.

Claude Code 대화창에서 "내 졸업 요건 확인해줘"라고 치면 알아서 USAINT에서 데이터를 가져와 분석해줍니다.

## 요구사항

- Python 3.10 이상
- macOS / Windows / Linux (GUI). Linux headless는 폴백 환경변수 사용 (아래 "문제 해결" 참고)
- Claude Code CLI ([설치 가이드](https://docs.claude.com/en/docs/claude-code)) 또는 Codex ([설치 가이드](https://github.com/openai/codex))

## 설치

### Claude Code로 설치

Claude Code 대화창에서 아래 명령으로 MCP 서버 + 6개 스킬(`soongpt-interview`, `soongpt-available-lectures`, `soongpt-timetable-builder`, `soongpt-timetable-composer`, `soongpt-timetable-visualize`, `soongpt-guide`)을 한 번에 설치합니다:

```
/plugin marketplace add yourssu/soongpt-plugin
/plugin install soongpt@yourssu
/reload-plugins
```

`pip install`이나 가상환경(venv)을 직접 만들 필요는 없습니다. 첫 실행 시 Claude Code가 `${CLAUDE_PLUGIN_DATA}` 안에 격리된 Python 가상환경을 자동으로 만들고 의존성을 설치합니다(최초 1회, 보통 몇 초 내). 이후에는 만들어둔 환경을 그대로 재사용해서 즉시 실행됩니다.

> 아래 "수동 설치"로 이미 `claude mcp add`를 등록해서 쓰고 있었다면, 플러그인 설치 전에 `claude mcp remove soongpt-mcp -s user`로 기존 등록을 지워주세요. 같은 이름의 서버가 여러 scope에 동시에 등록되면 충돌 경고가 뜹니다.

### Codex로 설치

이 저장소는 [Codex](https://github.com/openai/codex) 플러그인 매니페스트(`.codex-plugin/plugin.json`)도 포함하고 있어서, MCP 서버와 6개 스킬을 Codex에서도 그대로 쓸 수 있습니다. (Codex용 MCP 등록 파일은 관례적인 `.mcp.json`이 아니라 `codex-mcp.json`으로 이름 붙였습니다 — `.mcp.json`은 Claude Code가 project-scope MCP 서버 파일로 예약해둔 이름이라, 이 저장소를 Claude Code에서 열면 플러그인이 제공하는 서버보다 우선순위가 높은 별도 서버로 잘못 인식됩니다.)

```
codex plugin marketplace add yourssu/soongpt-plugin
codex plugin add soongpt@yourssu
```

## 업데이트

`.claude-plugin/plugin.json`의 `version` 필드가 실제 배포 버전입니다. 이 값이 올라간 커밋이 main에 머지되어야 사용자가 업데이트를 받을 수 있으므로, 새 커밋이 쌓여도 버전을 bump하지 않으면 아래 명령으로도 업데이트가 인식되지 않습니다.

### Claude Code

터미널에서:

```
claude plugin marketplace update yourssu
claude plugin update soongpt@yourssu
```

`marketplace update`로 마켓플레이스 스냅샷을 최신 커밋으로 갱신한 뒤, `plugin update`로 설치된 플러그인을 최신 버전으로 업데이트합니다. 완료 후 **Claude Code를 재시작**해야 새 MCP 서버 코드가 로드됩니다 (MCP 서버는 세션 시작 시 실행되는 프로세스).

Claude Code 대화창 안에서는 `/plugin` 명령으로도 마켓플레이스 탐색·설치·업데이트가 가능합니다(대화형 메뉴에서 항목을 직접 선택/확인해야 함). 현재 설치된 버전은 `/plugin` 또는 `claude plugin list`로 확인할 수 있습니다.

### Codex

```
codex plugin marketplace upgrade yourssu
codex plugin add soongpt@yourssu
```

`marketplace upgrade`로 마켓플레이스 스냅샷을 최신 커밋으로 갱신한 뒤, `plugin add`로 플러그인을 다시 설치해 업데이트합니다. 설치 상태는 `codex plugin list`로 확인할 수 있습니다.

### 수동 설치 (이 저장소를 직접 개발할 때)

이 저장소 자체를 열어서 코드를 수정/기여하는 경우엔 기존처럼 로컬 venv로 설치합니다:

```bash
git clone https://github.com/yourssu/soongpt-plugin.git
cd soongpt-plugin
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e .
```

**로컬 수정사항 실행/테스트**:

```bash
# venv 안에서 MCP 서버(stdio)를 직접 실행 — Claude Code 플러그인 없이도 동작 확인
python -m soongpt_mcp

# 또는 플러그인과 동일한 부트스트랩(자동 venv 생성·의존성 설치)을 재현하려면
bash scripts/run-server.sh
```

> 일반 사용자는 위 `/plugin install` 흐름으로 충분하므로 `claude mcp add`로 서버를 직접 등록할 필요가 없습니다. 플러그인 설치 전에 **기존에 `claude mcp add`로 단독 MCP 서버를 등록해 둔 분**은 앞선 안내(위 27행)대로 `claude mcp remove soongpt-mcp -s user`로 먼저 지워주세요 — 같은 이름이 여러 scope에 겹치면 충돌 경고가 뜹니다.

## 특징

- **로컬에서 동작**: 서버/DB/인프라 필요 없이 내 컴퓨터에서만 실행
- **터미널 명령어 불필요**: 첫 사용 시 자동으로 브라우저가 열려 로그인 폼을 제공
- **보안**: 학번/비밀번호는 디스크에 저장되지 않음. OS 키체인만 사용
- **가공 전 데이터 제공**: 데이터 해석/추천 로직은 Claude에게 맡김
- **사용자 프로필 영속화**: 매번 USAINT를 호출하지 않고 학적 컨텍스트를 로컬에 저장
- **18개 도구**: 학적/수강/성적, 졸업사정표(30일 캐싱), 강의시간표 검색(서버 측 캐시 자동 저장), 교양선택 분야 목록, 교양필수 과목명 목록, 강의 캐시 로드, 시간표 파싱/충돌 검사, 시간표 후보 로드/저장/삭제, 학과-단과대 매핑(1년 캐싱 + 번들 seed), 프로필 조회/설정/갱신, 인터뷰 조회/설정/목록

## 도구

| 도구 | 반환 데이터 | 소요 시간 |
|---|---|---|
| `get_usaint_snapshot` | 학적 정보, 학기별 수강 과목(코드+강의명 subjects 인라인), 저성적(C/D/F) 과목, 복수전공/부전공/교직 플래그 (30일 캐시, 프로필 자동 저장). `subjectNames`는 subjects에서 자동 파생 | 캐시 hit 즉시 / 미스 ~9초 |
| `get_graduation_status` | 졸업 요건 상세 + 카테고리별 충족 여부 + 잔여 학점 (30일 캐시, `force_refresh` 옵션) | 캐시 hit 즉시 / 미스 ~6초 |
| `find_lectures` | 특정 학기/카테고리 강의 검색 (강의계획서 옵션). 기본 `save_to_cache=True`로 결과를 서버 측에서 캐시에 자동 그룹 저장 (SPR-75) | 일반 카테고리 ~3초 / 교양선택 "전체"는 수초~30초대 |
| `list_optional_elective_categories` | 해당 학기 교양선택 분야 목록 (학번별 '[‘NN이후]' 분류 포함) | ~3초 |
| `list_required_electives` | 해당 학기 교양필수 과목명 목록 (분야 접두 `[SW와AI]` 등 포함) | ~3초 |
| `load_lectures_cache` | 저장된 강의 캐시 로드 (7일 TTL, `_cache.source`로 hit/stale/miss 구분) | 즉시 |
| `parse_lectures_cache` | 강의 캐시를 시간표 파싱 결과로 변환 — parsed + subject_groups(분반 그룹) + stats | 즉시 |
| `check_timetable_conflicts` | 단일 후보 강의 리스트의 시간 충돌 검사 (30개 초과 시 오류) | 즉시 |
| `load_timetable_candidates` | 저장된 시간표 후보 로드 (TTL 없음, `_cache.source`로 hit/miss 구분) | 즉시 |
| `save_timetable_candidate` | 후보 1건 저장 — code 존재 검증, 같은 name이면 replace | 즉시 |
| `clear_timetable_candidates` | 저장된 시간표 후보 삭제 ("다시 짜자") | 즉시 |
| `load_department_map` | 학과-단과대 매핑 (로컬 캐시 → 번들 seed → 자동 빌드, `force_refresh` 옵션) | 캐시/seed hit 즉시 / 미스 ~10-20초 |
| `get_user_profile` | 저장된 사용자 프로필 (없으면 안내) | 즉시 |
| `set_user_profile` | 단일 필드 부분 업데이트 (학번/이름/단과대/학과/학년/트랙/입학연도/복수전공/연계융합전공/부전공/교직이수여부/교직전공) | 즉시 |
| `refresh_user_profile` | USAINT basicInfo로 학과/학년/입학연도/복수전공/연계융합전공/부전공/교직이수 재동기화 (사용자 수정값 보존 옵션) | ~2-3초 |
| `get_interview` | 특정 학기 인터뷰 결과 (3개 섹션) + completion 맵 | 즉시 |
| `set_interview` | 인터뷰 섹션 텍스트 저장 (자연어 요약, 덮어쓰기) | 즉시 |
| `list_interviews` | 모든 학기 인터뷰 메타 목록 | 즉시 |

여러 도구는 병렬로 동시 호출 가능 (Claude가 알아서 처리).

## 사용자 프로필

학적 컨텍스트(학번/이름/단과대/주전공/학년/트랙/입학연도/복수전공/연계융합전공/부전공/교직이수여부/교직전공)와 수강이력(학기별 수강과목, 저성적 과목)을 한 파일에 저장해 매번 USAINT를 호출하지 않아도 됩니다. **프로필 + 수강이력의 단일 SoT는 학기별 스냅샷(`snapshot_{year}_{semester}.json`)** — `get_usaint_snapshot()`이 fetch 결과를 저장하고, 프로필 수정도 이 파일에 반영됩니다. 학기별로 분리되어 전과/학년 증가/세부전공 변경 시 과거 학기 컨텍스트가 보존됩니다.

**저장 위치**: `${CLAUDE_PLUGIN_DATA}/snapshot_{year}_{semester}.json` (없으면 `~/.local/share/soongpt-mcp/snapshot_{year}_{semester}.json`)

**마이그레이션**: SPR-46 이전의 `profile_{year}_{semester}.json`(및 SPR-30의 `profile.json`)은 스냅샷 파일이 없을 때 자동 읽기 fallback → 다음 저장 시 스냅샷 파일로 이전.

**캐시 TTL**: 수강이력은 학기 중 거의 불변하므로 30일. 만료/없으면 `get_usaint_snapshot()`이 USAINT에서 다시 fetch하고, `force_refresh=True`로 강제 새로고침할 수 있습니다.

**과목명 매핑(SPR-47)**: `takenCourses`의 `subjects`가 코드+강의명을 인라인으로 들고 있으며(진실 소스), 응답의 `subjectNames`({코드: 강의명})는 매 호출마다 `subjects`에서 자동 파생됩니다(별도 저장 X). 재수강 대체과목 추천 코드 등 수강 이력이 없는 코드는 `subjectNames`에 없으니 코드 그대로 폴백 표시하세요.

**USAINT가 채우는 필드** (9개): `department`, `college`(단과대, SPR-55), `grade`, `entered_year`, `double_major`(복수전공), `connected_major`(연계·융합전공), `minor`(부전공), `teaching_certification`(교직이수 여부), `teaching_major`(교직 전공명) — `get_usaint_snapshot()`/`refresh_user_profile`로 동기화
**사용자 입력 필드** (3개): `student_id`, `name`, `track` — `set_user_profile`로 직접 입력 (사용자가 명시적으로 수정을 요청할 때)

### 워크플로우

```
1. 처음: "시간표 짜자"
   → get_usaint_snapshot() 호출 → USAINT에서 프로필(9필드)+수강이력 저장 (미스 시 ~9초)
   → 학번/이름/트랙처럼 USAINT가 못 채우는 필드는 필요할 때 set_user_profile로 입력

2. 이후 재진입: get_usaint_snapshot()이 30일 캐시로 즉시 응답 (USAINT 재호출 없음)

3. 휴학/복학/전과 후: "내 프로필 업데이트해줘"
   → refresh_user_profile(preserve_user_overrides=True)
   → USAINT 9개 필드 갱신, 사용자가 입력한 학번/이름 등은 보존

4. 수강이력 새로고침: "수강이력 새로 가져와"
   → get_usaint_snapshot(force_refresh=True)
```

> 참고: 동시 호출 시 마지막 write가 이길 수 있습니다. MCP 클라이언트가 보통 직렬 호출하지만, 병렬 실행 시 lost update 가능성이 있습니다. 파일 쓰기는 atomic rename으로 크래시 중 손상을 방지합니다.

## 학과-단과대 매핑

복수/부전공 학과의 단과대를 자동으로 찾기 위한 `{학과명: 단과대}` 매핑. 숭실대 학과 구조는 연 1~2회 변경되므로 1년 TTL로 캐싱.

**3-tier 로딩 순서**:
1. **로컬 캐시** (즉시): `${CLAUDE_PLUGIN_DATA}/department_map_{year}.json` (폴백 `~/.local/share/soongpt-mcp/`)
2. **번들 seed** (즉시): 패키지에 커밋된 정적 파일 `src/soongpt_mcp/data/department_map_{year}.json`
3. **자동 빌드** (10~20초): USAINT 강의시간표에서 모든 단과대를 순회하며 빌드 → 로컬 캐시에 저장

`_cache.source` 응답 필드로 출처 추적 (`cache` | `bundled` | `fresh`).

**메인테이너 워크플로우** (연 1회, 학기 시작 전): seed 파일이 없거나 stale 할 때 신규 사용자 첫 호출이 10~20초 걸리므로, 메인테이너가 직접 갱신해 커밋합니다.

```bash
# 1. fresh 빌드 (USAINT 세션 필요)
#    MCP 클라이언트에서 load_department_map(year=2026, force_refresh=True) 호출

# 2. 생성된 로컬 캐시를 repo의 seed 위치로 복사
cp ${CLAUDE_PLUGIN_DATA}/department_map_2026.json \
   src/soongpt_mcp/data/department_map_2026.json

# 3. 커밋 — 이후 모든 신규 사용자는 0초 seed 사용
```

학과가 신설/통폐합되면 사용자가 `force_refresh=True`로 즉시 갱신 가능 (안전망).

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

### 교양필수 과목명 조회

`list_required_electives(year, semester)`는 해당 학기에 개설된 교양필수 과목명 목록을 반환합니다. 과목명은 `[SW와AI]AI개발과실전`처럼 분야 접두가 붙은 것과 `한반도평화와통일`처럼 일반명이 섞여 있습니다. optional_elective의 `[‘NN이후]' 학번 태그와 달리 연도 태그가 없으므로 입학연도 필터링 없이 반환된 과목명 전체를 사용합니다. 각 과목명을 그대로 `find_lectures(category_type="required_elective", lecture_name=<과목명>)`에 넘겨 해당 과목의 강의를 조회합니다.

```python
# 반환 스키마
{
  "lecture_names": ["[SW와AI]AI개발과실전", "한반도평화와통일", ...],
  "count": 31,
  "fetchTime": "2.70s"
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
[soongpt MCP 서버 (Python)]
       ↓ keyring에서 세션 로드
       ↓ 없음/만료 → 자동 localhost 웹 서버 + 브라우저 오픈
       ↓ 사용자 학번/비번 입력 → rusaint 인증 → 세션 JSON → keyring 저장
       ↓ rusaint 라이브러리로 USAINT 스크래핑
[숭실대 u-saint 서버]
       ↓ 데이터 반환
[soongpt MCP 서버] → [Claude]이 데이터 해석/분석
```

## 기여

- [EATSTEAK/rusaint](https://github.com/EATSTEAK/rusaint) — USAINT 스크래핑 라이브러리
- [soongpt-backend](https://github.com/yourssu/soongpt-backend) — 숭피티 웹 서비스 백엔드 (스크래핑 로직 참고)

> **버전 동기화**: 릴리스 시 `pyproject.toml`(`[project] version`)과 `.claude-plugin/plugin.json`(`version`) 버전을 함께 올려 일치시켜야 합니다.

## 라이선스

MIT
