---
name: soongpt-guide
description: soongpt-mcp 플러그인 사용법 안내 스킬 — 14개 MCP 도구 개요, 온보딩(프로필 설정)→시간표 완성→졸업요건 확인 워크플로우, 사용자 질문 패턴별 대응 도구/스킬 매핑, 자동 로그인 흐름 설명. "/soongpt-guide", "soongpt 도움말", "플러그인 사용법", "이거 어떻게 써" 등에서 호출. 도구를 직접 호출하지 않고 안내만 담당.
---

# SoongPT Guide

soongpt-mcp 플러그인으로 뭘 할 수 있는지, 지금 상황에서 어떤 도구·스킬을 쓰면 되는지 대화형으로 안내하는 스킬. 실제 USAINT 조회나 다른 스킬의 진입 절차는 이 스킬이 직접 수행하지 않는다.

## 트리거

- "/soongpt-guide"
- "soongpt 도움말", "플러그인 사용법", "이거 어떻게 써", "이 플러그인 뭐 할 수 있어", "뭐부터 하면 돼?"

## 이 스킬의 역할

- **안내 전용**: 이 스킬 자체는 MCP 도구를 호출하지 않는다. "이런 상황엔 이 도구/스킬을 쓰면 된다"를 설명하는 게 전부.
- 사용자 질문이 사용법을 묻는 게 아니라 실제 데이터/작업 요청이면(예: "내 졸업요건 확인해줘"), 설명만 하고 끝내지 말고 해당 도구나 스킬을 정상적으로 호출해 결과까지 응답한다. 이 스킬은 "설명이 필요한 순간"에 개입하는 안내판일 뿐, 실제 요청을 가로막는 관문이 아니다.
- 다른 스킬(`soongpt-interview`, `soongpt-available-lectures`, `soongpt-timetable-builder`)로 넘어갈 때는 어떤 트리거 문구를 쓰면 되는지만 안내한다. 각 스킬의 세부 진행 절차(질문 내용, 캐시 판단 등)는 해당 스킬 문서를 따르며 여기서 재설명하지 않는다.

## 플러그인 한눈에 보기

숭실대 USAINT 데이터를 로컬에서 가져오는 MCP 서버. **14개 도구 + 3개 워크플로우 스킬**로 구성.

| 그룹 | 도구 | 하는 일 | 보통 호출되는 방식 |
|---|---|---|---|
| 학적/졸업 | `get_usaint_snapshot` | 학적정보, 학기별 수강과목(코드+강의명 subjects 인라인), 저성적(C/D/F) 과목, 복수전공/부전공/교직 플래그 (30일 캐시 + 프로필 자동 저장) | 직접 — "내 수강정보 가져와", "재수강 후보 뭐 있어" |
| 학적/졸업 | `get_graduation_status` | 졸업요건 상세 + 카테고리별 충족 여부 + 잔여 학점 (30일 캐시) | 직접 — "졸업요건 확인해줘", "몇 학점 남았어" |
| 강의검색 | `find_lectures` | 특정 학기/카테고리 강의 검색 | 직접도 가능하지만 보통 `soongpt-available-lectures`가 여러 카테고리를 한 번에 병렬 호출 |
| 강의검색 | `list_optional_elective_categories` | 해당 학기 교양선택 분야 목록 (학번별 분류 포함) | 위와 동일 |
| 강의검색 | `list_required_electives` | 해당 학기 교양필수 과목명 목록 (분야 접두 포함) | 위와 동일 |
| 강의캐시 | `load_lectures_cache` / `save_lectures_cache` | 통합 조회한 강의 목록 캐시 로드/저장 (7일 TTL) | `soongpt-available-lectures` 내부에서 사용 |
| 매핑 | `load_department_map` | 학과-단과대 매핑 (복수/부전공 단과대 자동 조회, 1년 캐시) | 스킬 내부 — 복수/부전공 처리 시 |
| 프로필 | `get_user_profile` | 저장된 프로필 조회 | 직접 — "내 프로필 뭐야" |
| 프로필 | `set_user_profile` | 단일 필드 수정 (학번/이름/단과대/학과/학년/트랙 등) | 사용자가 명시적 수정 요청 시 |
| 프로필 | `refresh_user_profile` | USAINT 학적정보로 학과/학년/입학연도 등 8개 필드 재동기화 | 복학·전과 후 (프로필만 갱신) |
| 인터뷰 | `get_interview` / `set_interview` / `list_interviews` | 이번 학기 선호(3섹션) 조회/저장, 전체 학기 목록 | `soongpt-interview` 내부에서 사용 |

**스킬 3개** (모두 리포 `skills/` 하위):
- `soongpt-interview` — 이번 학기 전략/선호 3섹션 인터뷰
- `soongpt-available-lectures` — 이번 학기 들을 수 있는 과목 통합 조회
- `soongpt-timetable-builder` — 위 둘을 포함한 시간표 완성 전체 흐름 오케스트레이터

## 일반 워크플로우

### 1. 온보딩 (최초 1회)

- "시간표 짜자" 같은 전체 흐름 진입 시 `soongpt-timetable-builder`가 `get_usaint_snapshot()`을 호출해 **프로필과 수강이력을 한 번에 확보**한다. USAINT가 못 채우는 학번/이름/단과대/트랙은 필요할 때 사용자에게 직접 물어 `set_user_profile(field, value)`로 입력받는다.
- 이때 USAINT 세션이 없으면 자동 로그인 흐름이 뜬다 (아래 [자동 로그인 흐름](#자동-로그인-흐름) 참고).
- 휴학/복학/전과 후에는 "프로필 업데이트해줘"라고 하면 `refresh_user_profile(preserve_user_overrides=True)`로 USAINT 쪽 필드만 새로고침하고, 사용자가 직접 입력한 값은 보존한다.
- `set_user_profile`은 **사용자가 명시적으로 프로필 수정을 요청했을 때만** 사용한다.

### 2. 시간표 완성 흐름

- "시간표 짜줘" / "시간표 완성해줘" / "이번 학기 시간표 만들어줘"라고 하면 `soongpt-timetable-builder`로 넘어간다. 이 스킬이 프로필 확인 → 졸업사정표 확인 → 인터뷰(`soongpt-interview`에 위임) → 들을 수 있는 과목 통합 조회(`soongpt-available-lectures`에 위임) 순서로 알아서 진행하며, 이미 끝난 단계는 건너뛴다.
- 특정 단계만 다시 하고 싶으면("인터뷰만 다시 할래", "강의만 새로 가져와") 그 문구로 말하면 된다. 오케스트레이터가 선행 단계를 다시 확인하지 않고 바로 해당 하위 스킬로 위임한다.

### 3. 졸업요건 확인

- "졸업요건 확인해줘" / "몇 학점 남았어"라고 하면 `get_graduation_status()`가 응답한다 (30일 캐시, 사용자가 "새로고침"을 명시하면 `force_refresh=True`).
- 저성적 과목 재수강 추천이 필요하면 `get_usaint_snapshot()`의 저성적 과목 목록을 같이 활용해서 대답한다.

## 질문 패턴 → 대응 도구/스킬

| 사용자가 이렇게 물으면 | 대응 |
|---|---|
| "내 프로필 뭐로 되어있어?" | `get_user_profile()` |
| "내 프로필 설정해줘" / "처음이라 설정부터 할래" | 온보딩: `get_usaint_snapshot()`으로 프로필·수강이력 확보 → 필요 시 `set_user_profile()` |
| "복학했는데 프로필 업데이트해줘" | `refresh_user_profile(preserve_user_overrides=True)` |
| "내 졸업요건 확인해줘" / "졸업까지 몇 학점 남았어" | `get_graduation_status()` |
| "재수강하면 좋은 과목 추천해줘" | `get_usaint_snapshot()`의 저성적 과목 기반 추천 |
| "이번 학기 전공필수 강의 보여줘" | `find_lectures(category_type="major", ...)` |
| "이번 학기 교양선택 뭐 있어" | `list_optional_elective_categories()` |
| "이번 학기 교양필수 뭐 있어" | `list_required_electives()` |
| "이번 학기 들을 수 있는 과목 다 가져와" / "수업 후보 가져와" | 위임: `soongpt-available-lectures` |
| "시간표 인터뷰 하자" / "이번 학기 계획 세울래" / "내 선호 물어봐" | 위임: `soongpt-interview` |
| "시간표 짜줘" / "시간표 완성해줘" | 위임: `soongpt-timetable-builder` (전체 흐름 오케스트레이터) |
| "지난 학기 인터뷰 뭐라고 했었지" | `list_interviews()` / `get_interview(year, semester)` |

## 자동 로그인 흐름

- 별도 로그인 명령은 없다. 세션이 없거나 만료된 상태로 처음 USAINT 데이터를 요청하는 순간(예: `get_usaint_snapshot`, `get_graduation_status`, `refresh_user_profile` 최초 호출 시) MCP 서버가 세션 없음을 감지하고 **자동으로 기본 브라우저를 연다**.
- 브라우저 폼에 학번/uSaint 비밀번호를 입력해 제출하면 인증 성공 후 세션이 OS 키체인에 저장되고, 원래 요청이 갱신된 세션으로 자동 재실행되어 결과가 돌아온다.
- 학번/비밀번호는 어디에도 저장되지 않고 인증 직후 메모리에서 삭제된다. 디스크에 남는 건 세션 토큰뿐이며 그마저도 OS 키체인에만 저장된다.
- 세션 만료(보통 1~2시간) 시에도 동일하게 자동 재로그인이 진행된다.
- 브라우저가 자동으로 안 열리면 터미널 stderr에 출력된 로그인 URL을 직접 열면 되고, SSH/headless 환경에서는 `SOONGPT_SESSION_JSON` 환경변수로 세션을 직접 주입하는 우회 방법이 있다.

## 비고

- 도구 목록/설명의 최신 소스는 README.md의 '도구' 표다. 이 문서 내용이 README와 어긋나면 README 기준으로 갱신한다.
- 각 스킬의 세부 진행 절차(질문 순서, 캐시 판단 기준, 진입 조건 등)는 이 스킬에서 다시 설명하지 않고 해당 스킬 문서(`soongpt-interview`, `soongpt-available-lectures`, `soongpt-timetable-builder`)를 따른다.
