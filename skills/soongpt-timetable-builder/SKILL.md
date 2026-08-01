---
name: soongpt-timetable-builder
description: 숭실대 시간표 완성 전체 흐름 오케스트레이터 — 인사+로그인+프로필·수강이력 확보(get_usaint_snapshot) → 졸업사정표 확인 → 인터뷰(soongpt-interview 위임) → 들을 수 있는 과목 통합 조회(soongpt-available-lectures 위임) → 시간표 후보 생성(확장 지점, 미구현) 순으로 진행. "시간표 짜줘", "시간표 완성해줘", "이번 학기 시간표 만들어줘" 같은 막연한 요청의 진입점. 특정 단계만 원하는 요청("인터뷰만 다시 할래" 등)은 선행 단계 확인 없이 해당 하위 스킬로 즉시 위임.
---

# SoongPT Timetable Builder

시간표를 완성하기까지 필요한 전체 단계를 순서대로 안내/위임하는 오케스트레이터. 각 단계의 실제 로직(질문, 조회)은 하위 스킬이 담당하고, 이 스킬은 **어떤 단계부터 시작할지, 어떤 단계를 스킵할지**만 판단한다.

## 트리거

- **전체 흐름 시작 (넓은 트리거)**: "시간표 짜줘", "시간표 완성해줘", "이번 학기 시간표 만들어줘", "시간표 짤 준비해줘"
- **재개**: "시간표 마저 짜자", "하던 거 이어서"
- **부분 요청 (즉시 하위 스킬 위임 — 아래 [부분 요청 처리](#부분-요청-처리-빠른-위임) 참고)**: "인터뷰만 다시 할래", "강의만 다시 가져와" 등
- "/soongpt-timetable-builder"

## 전체 흐름 개요

1. 인사 + 로그인 + 프로필·수강이력 확보
2. 졸업사정표 확인
3. 인터뷰 진행 — 위임: `soongpt-interview`
4. 들을 수 있는 과목 통합 조회 — 위임: `soongpt-available-lectures`
5. 시간표 후보 생성 — **확장 지점 (아직 미구현)**

이 스킬의 스코프는 1~4단계 라우팅까지다. 5단계는 안내만 하고 종료한다.

## 진입/스킵 결정 규칙표

각 단계는 "스킵 조건"을 만족하면 건너뛰고, 아니면 "진행 조건"에 해당하는 조치를 취한다.

| 단계 | 확인 도구 | 스킵 조건 | 진행 조건 | 비고 |
|---|---|---|---|---|
| 1. 프로필·수강이력 | `get_usaint_snapshot()` + `get_user_profile()` | 핵심 필드(`department`, `grade`, `entered_year`, `college`) 모두 채워짐 | 하나라도 누락 | 진입 시 인삿말 + USAINT 로그인 진행. 프로필·수강이력을 단일 SoT로 저장. `college`는 USAINT 제공 필드(SPR-55)지만 그래도 비면 사용자에게 물어 `set_user_profile`로 입력 |
| 2. 졸업사정표 | `get_graduation_status()` | `_cache.source == "cache"` (30일 이내) | `source`가 `"fresh"`(방금 새로 가져옴) — 그대로 사용, 별도 조치 불필요 | `force_refresh=True`는 사용자가 "새로고침"을 명시했을 때만 |
| 3. 인터뷰 | `get_interview(year, semester)` | `completion`의 3개 섹션(`semester_strategy`, `time_preferences`, `subject_preferences`) 모두 `true` | 하나라도 `false`/없음 | 위임 대상: `soongpt-interview`. 이어서/처음부터 여부는 하위 스킬이 판단 |
| 4. 강의 캐시 | `load_lectures_cache(year, semester)` | `_cache.source == "cache"` | `source`가 `"stale"`(새로고침 여부를 사용자에게 확인) 또는 `"miss"` | 위임 대상: `soongpt-available-lectures` |
| 5. 시간표 후보 생성 | (해당 스킬 없음) | — | — | [확장 지점](#5-시간표-후보-생성-확장-지점) 참고 |

`year`/`semester`는 현재 학기 기준: 1~7월="1", 8~12월="2".

## 진행 절차

### 1. 인사 + 로그인 + 프로필·수강이력 확보

- "시간표 짜자"로 진입하면 먼저 사용자에게 짧은 인삿말을 보낸다:
  > "시간표 짜는 거 도와줄게. 시작 전에 학교 정보(학적/수강이력)를 확인할게."
- 이어서 `get_usaint_snapshot()` 호출 — 세션이 없으면 **브라우저 로그인 폼이 자동으로 열린다**. 응답의 `_cache.source`:
  - `"cache"`: 30일 이내 스냅샷 재사용 — 프로필+수강이력이 이미 로컬에 있음 (즉시)
  - `"fresh"`: 방금 USAINT에서 가져와 프로필·수강이력을 저장함 (~9초)
- `get_user_profile()`로 핵심 필드(`department`, `grade`, `entered_year`, `college`)가 모두 채워졌는지 확인:
  - `department`/`grade`/`entered_year`은 스냅샷이 채워준다.
  - `college`(단과대)는 USAINT가 채워주는 필드(SPR-55)다. 그래도 비어 있으면(구 프로필·USAINT 데이터 누락 등) 사용자에게 물어 `set_user_profile("college", ...)`로 입력받는다.
- 프로필·수강이력(`takenCourses`(코드+강의명 subjects 인라인)/`lowGradeSubjectCodes`)은 스냅샷 호출 하나로 준비되므로, 그 외 `set_user_profile()` 보충 절차는 필요 없다. `subjectNames`(코드→강의명)는 이 subjects에서 자동 파생되어 응답에만 노출됨.
- 프로필 수정은 **사용자가 명시적으로 요청할 때만** `set_user_profile(field, value)` 사용.
- 수강이력을 "새로고침"해야 한다면 `get_usaint_snapshot(force_refresh=True)` 호출 (사용자가 명시했을 때만).

### 2. 졸업사정표 확인

- `get_graduation_status()` 호출 (기본적으로 캐시 우선, 미스/만료 시 자동 fetch)
- 결과를 3단계(인터뷰의 `subject_preferences` 섹션 컨텍스트)와 5단계 안내에 재사용할 수 있도록 기억해둔다

### 3. 인터뷰 (위임: `soongpt-interview`)

- `get_interview(year, semester)` 호출
- 스킵 조건 만족 시: 완료된 인터뷰 요약만 한두 줄 보여주고 4단계로 이동
- 아니면: `soongpt-interview` 스킬로 전체 위임. 해당 스킬이 자체 진입 절차(프로필/졸업사정표 재확인 포함)를 다시 수행하지만 캐시 기반이라 비용은 적다

### 4. 들을 수 있는 과목 조회 (위임: `soongpt-available-lectures`)

- `load_lectures_cache(year, semester)` 호출
- 스킵 조건 만족 시: 캐시 요약(총 강의 수 등)만 보여주고 5단계로
- `"stale"`이면 사용자에게 새로고침 여부를 물어본 뒤 결정, `"miss"`면 바로 위임
- 위임 시 `soongpt-available-lectures` 스킬이 전체 진입 절차(프로필 필수 필드 체크 포함)를 처음부터 수행

### 5. 시간표 후보 생성 (확장 지점)

- 아직 이 스킬 없음. 4단계 완료 후 다음과 같이 안내하고 종료:
  > "여기까지가 지금 준비된 단계야. 실제 시간표 후보를 짜주는 스킬은 아직 없어서, 여기서 마무리할게."
- 향후 시간표 후보 생성 스킬이 추가되면 4단계 완료 직후 자동으로 그 스킬에 연결한다. 스킬 이름/트리거는 해당 이슈에서 결정되므로 여기서는 연결 지점만 예약해둔다.

## 부분 요청 처리 (빠른 위임)

사용자가 전체 흐름이 아니라 특정 단계만 원하면, **선행 단계 확인 없이 즉시** 해당 하위 스킬로 위임한다. 프로필/졸업사정표 같은 선행조건은 위임받은 스킬이 각자 자체 진입 절차에서 처리하므로 오케스트레이터가 중복으로 먼저 체크하지 않는다.

| 사용자 요청 예시 | 위임 대상 |
|---|---|
| "인터뷰만 다시 할래", "선호도만 다시 입력할래" | `soongpt-interview` |
| "강의만 다시 가져와", "과목 조회만", "캐시 갱신해" | `soongpt-available-lectures` |

## 재개/이탈 상태 판단

전체 흐름 도중 세션이 끊겼다가 "이어서 하자" / "시간표 마저 짜자"로 돌아오면, 처음부터 순서대로 재조회해서 어디까지 끝났는지 판단한다:

1. `get_usaint_snapshot()` / `get_graduation_status()`로 1~2단계 상태 재확인 (캐시 기반이라 비용 적음)
2. `get_interview(year, semester)`의 `completion`으로 3단계 상태 확인
3. `load_lectures_cache(year, semester)`의 `_cache.source`로 4단계 상태 확인
4. 위 결과 중 "진입/스킵 결정 규칙표"의 진행 조건에 처음 걸리는 단계부터 이어서 진행

## 비고

- 이 스킬은 라우팅/오케스트레이션만 담당한다. 인터뷰 질문 내용, 강의 조회 세부 로직은 각각 `soongpt-interview`, `soongpt-available-lectures` 스킬 문서를 따른다.
- 시간표 후보 생성 로직 자체는 이 스킬의 스코프 밖이며 별도 이슈로 분리되어 있다.
