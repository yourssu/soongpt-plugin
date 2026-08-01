---
name: soongpt-timetable-composer
description: 숭실대 시간표 후보 조합 — 인터뷰 선호 + 강의 캐시로 필수과목·미이수·재수강을 안내하고 Base/Flex로 후보 A/B/C를 조립해 저장/재개. "후보 이어서", "다시 짜자", "후보만 다시", "/soongpt-timetable-composer"에서 호출. "시간표 짜줘" 계열은 soongpt-timetable-builder가 5단계로 위임.
---

# SoongPT Timetable Composer

시간표 후보를 조합·영속화하는 스킬. 기반 도구(parse_lectures_cache / check_timetable_conflicts / load·save·clear_timetable_candidates)로 **계산**을 하고, **판단(필수 판별·대상 해석·선호 반영)은 LLM**이 담당한다. 특수 케이스(Co-op 중복 슬롯·학점 0 등)는 사용자와의 대화로 처리한다.

> 핵심 원칙: **"계산은 도구로, 판단은 LLM으로"**. 안내 순서(① 필수 → ② 미이수 → ③ 재수강)는 기본값일 뿐 사용자가 순서를 바꿀 수 있다. **안내 순서 ≠ 우선순위**.

## 트리거 (한정 — builder 경합 방지)

- "후보 이어서", "후보만 다시", "다시 짜자", "저장한 시간표 후보 보여줘"
- "/soongpt-timetable-composer"
- **"시간표 짜줘" / "시간표 완성해줘" 계열은 `soongpt-timetable-builder` 소유**다. builder가 전체 흐름 1~4단계를 거친 뒤 5단계에서 이 스킬로 위임한다. 이 스킬이 "시간표 짜줘"를 직접 가로채지 않는다.

## 진입 절차 (필수)

순서대로 진행한다.

### 1. 인터뷰 확인

- `get_interview(year, semester)` 호출 (year/semester는 현재 학기 — 1~7월="1", 8~12월="2")
- `completion`의 3개 섹션(`semester_strategy`, `time_preferences`, `subject_preferences`)이 모두 `true`가 아니면 **`soongpt-interview`로 위임** 후 돌아온다.

### 2. 강의 캐시 확인

- `load_lectures_cache(year, semester)` 호출
- `_cache.source`:
  - `"cache"`: 3번으로
  - `"stale"`: 사용자에게 새로고침 여부를 물어본다. 새로고침 → `soongpt-available-lectures` 위임 후 복귀. 아니면 stale 데이터로 계속
  - `"miss"`: `soongpt-available-lectures`로 위임 후 복귀

### 3. 데이터 원본 확보 (재개 mismatch 판정용 스냅샷)

- `get_usaint_snapshot()` 호출 — `takenCourses`(수강이력), `lowGradeSubjectCodes`(재수강 후보), `subjectNames`(코드→강의명) 확보. (builder 경유라면 이미 있음 — 캐시 기반 즉시)
- `get_graduation_status()` 호출 — **부족 학점(카테고리별 difference)만** 참고 (아래 [미이수 필수](#미이수-필수-절차-명문화) 절차 참고)
- 이 스킬이 나중에 저장할 `generation_params`에 쓸 기준값 두 개를 기억한다:
  - `interview_updated_at` = `get_interview()` 응답의 `interview.updated_at`
  - `lectures_cached_at` = `load_lectures_cache()` 응답의 `_cache.cached_at`

## 파싱 (1회만)

- `parse_lectures_cache(year, semester)` 호출 — **루프 밖에서 1회만** (후보 반복마다 재호출 금지).
- 응답의 `parsed`(ParsedLecture dict 목록), `subject_groups`(subject_key → code 목록), `stats`를 확보한다.
- **컨텍스트 절약**: 필수 과목 후보와 `subject_groups` 인덱스만 대화에 유지하고, 나머지 강의는 카테고리별 요약으로 줄인다.
- `stats.uncertain` / `stats.empty` 비율이 0이 아니면 사용자에게 투명하게 알린다:
  > "강의 {N}개 중 파싱 불확정 {U}개, 온라인(빈 시간) {E}개가 있어. 불확정은 시간 충돌 검사에서 제외돼."

`parsed[i]`의 필드 중 LLM이 꼭 쓰는 것:
- `code` / `name` / `subject_key`(code[:-2] 분반 그룹키) / `credits` / `slots`
- `parse_status`: `"ok"` | `"uncertain"` | `"empty"` — **충돌 검사는 `ok`만** 대상
- `category`: **이수구분** (2026-2 실강의 ~130건 스캔으로 값 형식 검증 완료) — `"전기-<학부명>"`(전공기초), `"전필-<학부명>"`(전공필수), `"전선-<학부명>"`(전공선택), `"교필"`(교양필수, schedule_room 빈 문자열=온라인형), `"교선"`(교양선택), `"교직"`(교직 이론), `"교직전공-<학부명>"`(교직 전공과목). `division`(null/"공통-재수강"/"팀티칭" 라벨)은 못 쓰니 **반드시 category 기준**. 필수 안내 필터(`"전기-"`/`"전필-"`/`"교필"` prefix)는 `교직전공-`을 제외한다
- `sub_category`: rusaint 원본 Lecture의 **실존 필드** — 복수/융합전공 이수구분(예: `"복선-컴퓨터"`, `"복필-컴퓨터/융선-ICT유통물류융합"`). 값이 없으면 `None` — 복수/융합전공 판단에 보조로만 쓰고, 없으면 `category`만 사용한다
- `target` (수강대상 자연어), `field` (학번 태그), `department`, `professor`

## 안내 순서 3단계 (기본값 — 사용자 순서 변경 허용)

안내는 ① 필수과목 → ② 미이수 필수 → ③ 재수강 순서로 진행한다. 사용자가 "재수강 먼저"처럼 순서를 바꾸면 따른다. **순서 변경은 우선순위 변경이 아니다** — 조립 단계에서는 세 유형 모두 Base(필수 박기)로 취급한다.

### ① 필수과목 (이번 학기에 들을 수 있는 필수)

- `parsed`에서 `category`가 `"전기-"`/`"전필-"`로 시작하거나 `"교필"`인 강의 중, `target` 자연어를 해석(LLM)해 수강 가능한 것만 후보로 제시한다.
  - 예: `target="컴퓨터학부 2학년"`인데 사용자가 3학년이면 → "수강대상이 2학년 전용인데 괜찮아?"처럼 **대상 불일치를 명시**한다. (아래 [수강제한 규칙](#수강제한-규칙))
- **교양필수(교필)는 SPR-51 캐시가 있을 때만** 안내한다. 캐시에 `required_elective_*` 그룹 키가 없으면(교양필수 조회가 아직 안 된 상태) 해당 부분을 생략하고:
  > "교양필수는 조회 준비 중이야. 나중에 '강의 다시 가져와' 하면 포함돼."
- `category="교필"` 강의는 schedule_room이 빈 문자열(온라인형) → `parse_status="empty"` → 시간 충돌 없이 자유 배치된다. (충돌 검사에서 제외되므로 별도 확인 불필요)

### ② 미이수 필수 (절차 명문화)

> **졸업사정표는 필수 과목 식별에 쓰지 않는다** — 카테고리별 학점 요약일 뿐 과목 단위 정보가 없다. 부족 학점만 참고한다.

1. **식별**: `parsed`(전기/전필/교필) ∩ `takenCourses` 코드 교차.
   - 수강이력(`takenCourses[].subjects[].code`)에 **코드가 없으면 "미이수 후보"** 로 안내한다.
   - 코드가 있으면 이미 이수한 것으로 보고 안내하지 않는다.
2. **코드 불일치 폴백**: 코드가 안 맞아 교차가 안 되는 경우(커리큘럼 개편으로 과목코드가 바뀌었을 가능성), `subjectNames`(수강이력의 {코드: 강의명})에서 **이름으로** 매칭한다. 이때 **반드시 `department` 일치를 확인**한다 — 동명이지만 다른 학과 과목을 오탐하지 않게.
3. **한계 명시**: 이 절차는 "근사 안내"다. 커리큘럼 개편으로 코드가 바뀐 과목은 정확히 잡아내지 못할 수 있음을 사용자에게 알린다:
   > "미이수 필수는 수강이력과 이번 학기 개설 과목을 대조한 **근사 안내**야. 커리큘럼 개편으로 코드가 바뀌면 정확하지 않을 수 있어. 확정은 졸업사정표나 학사 안내로 확인해줘."

### ③ 재수강

1. **식별**: `lowGradeSubjectCodes`(C/D/F 저성적) ∩ 개설 code.
2. **코드 불일치 폴백**: 교차가 안 되면 `subjectNames` ↔ `parsed` 이름 매칭 (**department 일치 확인 필수**).
3. **전체 분반 열거**: 재수강 대상으로 판단되면 해당 `subject_key`의 **전체 분반을 열거**한다 — 분반 1개만 제시하지 않는다. `subject_groups[subject_key]`로 모든 code를 찾아 시간/교수와 함께 보여준다.
4. **미개설 안내**: 이름 매칭도 안 되면:
   > "재수강 대상 과목이 이번 학기에 개설되지 않았거나 코드가 바뀌었을 수 있어. (과목명)"

## Base/Flex 조립

- **Base**: 위 3단계로 확정한 필수(① 필수과목 + ② 미이수 + ③ 재수강)를 먼저 박는다.
- **Flex**: 인터뷰 `time_preferences`/`subject_preferences`/`semester_strategy`를 반영해 여백을 채운다 (전공 비중, 원하는 시간대, 목표 학점 등).
- **분반 선택은 반드시 `subject_groups`(subject_key) 기준**으로 한다 — `name`으로 묶지 말 것(같은 name 다른 학과 별개 수업 존재).
- 같은 과목의 다른 분반/대체 과목이 있으면 같이 제시한다.

## 충돌 검사 루프 (N=5 상한)

- 후보마다 `check_timetable_conflicts(lectures=[...])`를 호출한다. **`parsed` 항목 dict 전체(코드만이 아니라 slots·parse_status 포함)를 넘긴다** — code만 넘기면 ParsedLecture 스키마 검증에 실패한다.
- **1회 1후보**만 전달한다 (30개 초과 시 도구가 ValueError 반환 — 전수 비교 금지).
- `has_blocking_conflict == True`면 충돌을 보고받고(Conflict의 days/start_min/end_min), 충돌 분반/과목을 교체한 뒤 재검사한다.
- **루프 상한 N=5** — 5회를 넘기면:
  > "5번 교체해도 충돌이 안 풀려. 조건을 완화해야 해 (예: 특정 과목 포기, 교수 선택 조건 완화)."
- `empty`(온라인) 과목의 `warnings`(불확정/제외 안내)를 **반드시 `conflicts_summary`에 포함**해 사용자와 후보에 남긴다.

## 수강제한 규칙

- `target`에 **(대상외수강제한)** 표기가 있으면 → 해당 후보에서 **제외**하거나 **명시적으로 경고**한다.
- 대상 불일치(제한 없음, 예: "2학년" 전용인데 3학년) → 전체 수강신청일에 안내하고 후보 유지 여부를 사용자에게 확인한다.

## 사용자 제시

- 후보는 **A/B/C** 형태로 제시한다:
  - 후보 이름(예: "안 A — 15학점") + 학점 합계(`total_credits`)
  - 빈시간(**대략적 안내로 한정** — "월 오전/화 오후 공강" 수준. 정확한 산술은 후속 도구가 담당)
  - 장단점 (충돌 유무, 대상 제한, 교수 선택 폭 등)
- 사용자가 선택/수정하면 그 결과를 다시 충돌 검사 → 재제시한다.

## 영속화

- 사용자가 후보를 **확정**하면 `save_timetable_candidate(year, semester, candidate, generation_params)`를 호출한다.
  - `candidate` 필드:
    - `name`: 후보 이름 (예: "안 A — 15학점")
    - `lecture_codes`: 선택 강의 `code` 목록 (분반 포함 10자리 — `parse_lectures_cache`의 code 그대로)
    - `total_credits`: 학점 합계 (float)
    - `has_blocking_conflict`: 마지막 충돌 검사 결과 (bool)
    - `conflicts_summary`: 충돌/불확정/empty warnings 요약 — **`check_timetable_conflicts`의 `warnings` 포함 필수**
    - `confirmed: True`
  - `generation_params`에 [진입 절차 3번](#3-데이터-원본-확보-재개-mismatch-판정용-스냅샷)의 스냅샷을 넣는다:
    ```
    generation_params={
      "interview_updated_at": <get_interview().interview.updated_at>,
      "lectures_cached_at": <load_lectures_cache()._cache.cached_at>
    }
    ```
- 같은 `name`으로 다시 저장하면 기존 후보가 **교체(replace)**된다 — 수정 반복 시 폐기 후보가 쌓이지 않는다. 반환의 `replaced`로 확인할 수 있다.
- **후보 이름은 수정 시에도 동일하게 유지하세요** — 같은 `name`으로 저장해야 기존 후보가 교체됩니다. 이름을 바꾸면 별도 후보로 쌓여 폐기 후보가 축적됩니다.
- `lecture_codes` 중 강의 캐시에 없는 code가 있으면 도구가 ValueError를 반환한다 — 그때는 `parse_lectures_cache`의 code를 다시 확인한다 (코드 전사 오류 가능성).
- **code 검증은 all-or-nothing으로 유지한다**: 잘못된 code가 섞이면 시간표가 깨진 채 저장되어 후반 단계에서 오탐만 키우기 때문. 일부만 통과/경고 처리로 약화하지 않는다.

## 재개 (mismatch 분기)

"후보 이어서" 등으로 재진입하면:

1. `get_interview(year, semester)` / `load_lectures_cache(year, semester)` / `load_timetable_candidates(year, semester)` 호출.
2. 후보가 없으면(`_cache.source == "miss"`) [진입 절차](#진입-절차-필수)부터 새로 시작.
3. 후보가 있으면 **mismatch 판정**:
   - `generation_params.interview_updated_at` ≠ `get_interview().interview.updated_at`
     **또는** `generation_params.lectures_cached_at` ≠ `load_lectures_cache()._cache.cached_at`
     → **mismatch** (키 부재는 `dict.get` 폴백 — 구버전 저장 데이터 대비)
   - 타임스탬프는 **ISO 문자열 그대로 비교하지 말고 datetime으로 파싱해 비교**한다 — Z/±오프셋/소수점 표기 차이로 생기는 오탐을 막는다.
   - **`get_interview()`가 null이면 mismatch로 간주** (인터뷰가 지워졌다는 것 자체가 변경 신호).
4. mismatch면:
   > "인터뷰/강의가 바뀌었어. 새로 짤까? 이어서 볼래?"
   - **새로 짜기**: `clear_timetable_candidates(year, semester)` 후 [파싱](#파싱-1회만)부터 다시.
   - **이어서 보기**: 기존 후보 표시 + mismatch 영향 안내 후 재개.
5. match면: 기존 후보(`candidates`)를 표시하고 이어서 진행 (수정/확정/다시 조합).

> **재저장 시 스냅샷 갱신 필수**: 후보를 수정·재저장할 때는 `generation_params`에 현재 인터뷰/강의 캐시 스냅샷을 **항상 다시 포함**하세요. 없이 저장하면 이전 스냅샷이 유지되어 다음 재개에서 계속 mismatch로 판정됩니다.

## 비고

- 이 스킬은 도구로 계산하고 LLM으로 판단한다. 서브에이전트는 쓰지 않는다.
- Co-op(현장실습) 중복 슬롯, 학점 0(사회봉사) 같은 특수 케이스는 도구가 판정하지 않으니 사용자와 대화로 확인한다.
- 후보 캐시는 TTL이 없다 — `clear_timetable_candidates`로만 무효화된다.
