---
name: soongpt-available-lectures
description: 숭실대 이번 학기 들을 수 있는 과목 통합 조회. 주전공·복수/부전공 학과 전체 + 교양선택 전체 분야 + 교양필수 전체 과목명을 find_lectures로 병렬 fetch해 로컬 캐시에 적재. "이번 학기 들을 수 있는 과목", "수업 후보 가져와", "강의 다 가져와", "/soongpt-available-lectures"에서 호출. 이후 시간표 후보 생성 워크플로우의 입력.
---

# SoongPT Available Lectures

이번 학기에 들을 수 있는 과목을 통합 조회해 로컬 캐시(`lectures_{year}_{semester}.json`)에 적재. 시간표 후보 생성 워크플로우의 사전 단계.

## 트리거

- **조회**: "이번 학기 들을 수 있는 과목", "수업 후보 가져와", "강의 다 가져와"
- **새로고침**: "강의 새로 가져와", "캐시 갱신해", "이번 학기 과목 다시"
- "/soongpt-available-lectures"

## 진입 절차 (필수)

순서대로 진행. 각 단계가 끝나야 다음 단계로 이동.

### 1. 프로필 확인 (필수)

- `get_user_profile()` 호출
- `profile == null`이거나 필수 필드가 비면 `get_usaint_snapshot()` 호출 (USAINT 세션 필요 — 최초엔 브라우저 로그인 폼 자동 오픈. 프로필+수강이력을 한 번에 확보)
- 그래도 아래 **필수 필드** 중 하나라도 비어있으면 사용자에게 직접 물어 `set_user_profile(field, value)`로 입력받는다:
  - `college` (단과대)
  - `department` (주전공 학과)
  - `grade` (학년)
  - `entered_year` (입학연도 — 교양 분야 학번 태그 필터링에 필수)
- `set_user_profile`은 사용자가 직접 값을 입력해야 하는 경우에만 사용한다 (자동 온보딩 수단 아님).

### 2. 캐시 확인

- `load_lectures_cache(year, semester)` 호출 (year/semester는 현재 학기 — 1~7월="1", 8~12월="2")
- 응답의 `_cache.source`:
  - `"cache"`: 캐시 히트. **5번(다음 단계 안내)으로 바로 이동**
  - `"stale"`: 7일 경과. 3번으로 (사용자에게 "새로고침?" 물어볼지, 그냥 stale라도 쓸지 선택)
  - `"miss"`: 파일 없음. 3번으로

사용자가 트리거에서 "새로" / "갱신" 뉘앙스를 포함했으면 source와 무관하게 3번으로.

### 3. 카테고리 세트 판단 + 병렬 fetch

스킬이 직접 `find_lectures`와 `list_optional_elective_categories`, `list_required_electives`를 **한 번에 병렬**로 호출. MCP 도구를 단일 메시지에 여러 개 담아 병렬 실행.

#### 3-0. find_lectures 묶음 크기 (~4개, 중요)

`find_lectures`를 **4개 초과**로 쏴야 하는 구간(주로 **3-B 교양 전체**; 3-A·3-C는 한 묶음에 다 들어오므로 그냥 한 번에 병렬)에서는 **한 번에 약 4개씩 묶음**으로 병렬 호출하고, 한 묶음이 끝나면 다음 묶음을 쏜다.

- **왜**: USAINT 포털(WebDynpro)이 같은 SSO 세션의 동시 요청을 서버에서 순차 처리한다. 18개를 한 번에 쏘면 마지막 것이 ~30초 대기하다 HTTP 타임아웃·WebDynpro 에러·SSO 세션 끊김 위험.
- **안전장치**: 서버가 `find_lectures`/`list_required_electives`/`list_optional_elective_categories`의 동시 송출을 **공유 Semaphore로 4개(기본값, `SOONGPT_COURSE_SCHEDULE_CONCURRENCY`로 조정)로 강제 제한**한다(SPR-67). 그래서 4개를 넘게 한 번에 쏴도 자동으로 대기열에 들어가 **안전**은 하다.
- **그런데도 묶음 단위가 낫다**: 묶음 단위로 쏘면 대기열을 거치지 않아 총 시간이 약간 줄고, 결과도 묶음별로 모아 처리하기 쉽다. 아래 "N회 병렬" 표기는 이 **~4개 묶음** 단위로 그룹화해 실행할 것. `list_optional_elective_categories`/`list_required_electives`는 1회씩이므로 묶음 대상이 아님 (각 맨 앞 묶음에 포함해 같이 쏘면 된다).

#### 3-A. 전공 계열 (2~6회 병렬)

- **주전공** (필수 1회):
  ```
  find_lectures(year, semester, category_type="major",
                collage=profile.college, department=profile.department,
                major=None)
  ```
  → `groups["major_primary"]`에 저장

- **주전공 타전공인정과목** (필수 1회):
  ```
  find_lectures(year, semester, category_type="recognized_other_major",
                collage=profile.college, department=profile.department,
                major=None)
  ```
  → `groups["recognized_other_major_primary"]`에 저장

- **복수전공** (`profile.double_major` 있을 때만):
  ```
  find_lectures(year, semester, category_type="major",
                collage=<복수전공 단과대>, department=profile.double_major,
                major=None)
  ```
  → `groups["major_double"]`에 저장
  - **단과대 획득**: `profile`에 복수/부전공 단과대 필드가 없으므로 `load_department_map(year)` 매핑 `{학과명: 단과대}`에서 역조회. `mapping[profile.double_major]`로 단과대 획득
  - 매핑에 키가 없으면 사용자에게 "복수전공 학과 {X}의 단과대가 어디야?" 직접 질문
  - `load_department_map`은 **복수전공 또는 부전공이 있을 때만** 3번 진입 시 **한 번만** 선행 호출하고 두 카테고리가 같은 매핑 결과를 재사용 (둘 다 없으면 호출 불필요)

- **부전공** (`profile.minor` 있을 때만):
  - 복수전공과 동일 패턴: `load_department_map` 역조회로 단과대 확보(실패 시 fallback 질문) →
    ```
    find_lectures(year, semester, category_type="major",
                  collage=<부전공 단과대>, department=profile.minor,
                  major=None)
    ```
  → `groups["major_minor"]`에 저장

- **연계·융합전공** (`profile.connected_major` 있을 때만):
  - rusaint 0.16.3은 **연계전공(`connected_major`)과 융합전공(`united_major`)을 별도 분류**로 제공. USAINT는 프로필 `connected_major`에 연계/융합을 통합 추출하므로 런타임에 어느 쪽인지 알 수 없음 → **양쪽 모두 시도**
  ```
  find_lectures(year, semester, category_type="connected_major",
                major=profile.connected_major)
  find_lectures(year, semester, category_type="united_major",
                major=profile.connected_major)
  ```
  → 각각 `groups["connected_major"]`, `groups["united_major"]`에 저장
  - **일반적으로 한쪽이 실패**: 사용자 이수가 연계면 보통 `united_major`가, 융합이면 `connected_major`가 USAINT WebDynpro 예외(`Cannot find ... option in ...CONNECT_MAJO/UNMA...`)를 던짐. 빈 배열이 아니라 **예외**이며 3-D의 카테고리별 error 처리로 흡입(정상 무시). 정상 한쪽은 강의 배열을 반환. **두 쪽 모두 성공(예: 과목이 양쪽에 걸쳐 개설)하면 둘 다 저장**
  - (런타임 검증은 임의 학과명으로 라우팅 건전성만 확인했으므로, 실제 이수자의 응답 패턴은 추후 검증 필요)

#### 3-B. 교양 전체 (선택 분야 + 필수 과목명 열거)

##### 3-B-1. 교양선택 전체 분야 (10~15회 병렬)

1. 분야 목록 조회:
   ```
   list_optional_elective_categories(year, semester)
   ```
2. 입학연도 기준 분야 필터링:
   - `profile.entered_year >= 2023` → `"[‘23이후]"` 분야만 유지
   - `profile.entered_year < 2023` → `"[‘23이전]"` 분야만 유지
   - 입학연도 불분명하거나 다른 학번 태그가 있으면 일단 전부 유지 (안전)
3. 필터링된 각 분야별로 `find_lectures` 병렬 호출 (약 4개씩 묶음 — 3-0 묶음 규칙):
   ```
   find_lectures(year, semester, category_type="optional_elective",
                 category="<분야명>")
   ```
   → `groups["optional_elective_<분야명>"]`에 저장

##### 3-B-2. 교양필수 전체 과목명 (필수)

1. 과목명 목록 조회:
   ```
   list_required_electives(year, semester)
   ```
2. 반환된 **모든 과목명** 각각에 대해 `find_lectures` 병렬 호출 (약 4개씩 묶음 — 3-0 묶음 규칙, 이 단계가 호출 수가 가장 많음):
   ```
   find_lectures(year, semester, category_type="required_elective",
                 lecture_name="<과목명>")
   ```
   → `groups["required_elective_<과목명>"]`에 저장
   - optional_elective의 `[‘NN이후]` 학번 태그와 달리 교양필수 과목명은 **연도 태그가 없으므로 입학연도 필터링 없이 전부** 조회
   - 과목명은 분야 접두(예: `[SW와AI]AI개발과실전`)와 일반명(예: `한반도평화와통일`)이 혼재. `list_required_electives`가 반환한 문자열을 그대로 `lecture_name`으로 사용 (수강대상 제한은 target 필드로 별도 활용)
   - **빈 결과 처리**: 이번 학기 미개설 과목은 `find_lectures`가 빈 결과(`count: 0`)를 반환하므로, 그룹은 캐시에 저장하되 **결과 요약(4단계)에서 count 0 그룹은 제외**한다. 예외가 나면 기존 3-D의 error 흡입 규칙으로 빈 그룹 저장 (정상 무시)

#### 3-C. 단일 카테고리 (2~3회 병렬)

- **채플** (필수 1회 — `profile.grade` 기반 단일 호출, 병렬 2회 아님):
  - **채플 종류 분리 배경**: USAINT 채플은 `"비전채플"`(2학년+)과 `"소그룹채플"`(1학년)로 나뉘며, `lecture_name="채플"`은 **무효값**이라 WebDynpro 예외(`Cannot find 채플 option`)가 난다. 그래서 **정확한 채플명 둘 중 하나**로 조회해야 한다. 어느 한쪽만 필요하므로 grade로 한 번만 호출한다 (둘 다 부를 필요 없음).
  - **grade 분기**:
    - `grade == 1`:
      ```
      find_lectures(year, semester, category_type="chapel",
                    lecture_name="소그룹채플")
      ```
    - `grade >= 2` **또는 grade 불명/None 폴백**:
      ```
      find_lectures(year, semester, category_type="chapel",
                    lecture_name="비전채플")
      ```
  → `groups["chapel"]` (단일 그룹 — 호출한 한 종류만 담김)
  - **grade 불명 폴백 = 비전채플**: grade는 진입 절차 1번 필수 필드라 통상 확보돼 있지만, 누락 시엔 기존 다수 사용자(2학년+) 기본값인 비전채플로.
  - **채플 종류의 데이터 격리**: grade==1이면 캐시 `chapel`엔 소그룹채플만, grade>=2면 비전채플만 들어간다. composer는 grade에 맞는 한 종류만 있다고 가정하고 동작한다.
  - 사용자가 특정 채플명(예: "한국인채플")을 명시하면 grade 무관 그 이름으로 조회.

- **숭실사이버대** (필수 1회):
  ```
  find_lectures(year, semester, category_type="cyber")
  ```
  → `groups["cyber"]`에 저장

- **교직** (`profile.teaching_certification == True`일 때만):
  ```
  find_lectures(year, semester, category_type="education")
  ```
  → `groups["education"]`에 저장
  - `teaching_certification`이 `False`/`None`이면 이 블록 생략

#### 3-D. 취합 + 저장

모든 find_lectures 결과를 아래 형태의 `groups` 딕셔너리로 취합:
```python
{
  "major_primary": {
    "category_type": "major",
    "params": {"collage": "IT대학", "department": "컴퓨터학부", "major": None},
    "lectures": [...],
    "count": N,
    "error": null
  },
  "recognized_other_major_primary": {
    "category_type": "recognized_other_major",
    "params": {"collage": "IT대학", "department": "컴퓨터학부", "major": None},
    "lectures": [...],
    "count": N,
    "error": null
  },
  "major_double": {  # double_major 있을 때만
    "category_type": "major",
    "params": {"collage": "<복수전공 단과대>", "department": "<double_major>", "major": None},
    "lectures": [...],
    "count": N,
    "error": null
  },
  "major_minor": {  # minor 있을 때만
    "category_type": "major",
    "params": {"collage": "<부전공 단과대>", "department": "<minor>", "major": None},
    "lectures": [...],
    "count": N,
    "error": null
  },
  "connected_major": {  # connected_major 있을 때만
    "category_type": "connected_major",
    "params": {"major": "<connected_major>"},
    "lectures": [...],
    "count": N,
    "error": null
  },
  "united_major": {  # connected_major 있을 때만 (연계/융합 양쪽 시도)
    "category_type": "united_major",
    "params": {"major": "<connected_major>"},
    "lectures": [],  # 이수가 연계면 이쪽은 예외 → 아래 error로 흡입(정상 무시)
    "count": 0,
    "error": "WebDynpro: Cannot find ... option in UNMA (연계 이수 시 융합 쪽은 예외 — 정상 무시)"
  },
  "optional_elective_[‘23이후]과학·기술": {
    "category_type": "optional_elective",
    "params": {"category": "[‘23이후]과학·기술"},
    "lectures": [...],
    "count": M,
    "error": null
  },
  "required_elective_[SW와AI]AI개발과실전": {
    "category_type": "required_elective",
    "params": {"lecture_name": "[SW와AI]AI개발과실전"},
    "lectures": [...],
    "count": N,
    "error": null
  },
  "required_elective_한반도평화와통일": {
    "category_type": "required_elective",
    "params": {"lecture_name": "한반도평화와통일"},
    "lectures": [...],
    "count": K,
    "error": null
  },
  "chapel": {  # grade==1 → "소그룹채플", grade>=2/불명 → "비전채플" (단일 호출, 한 종류만)
    "category_type": "chapel",
    "params": {"lecture_name": "비전채플"},  # 또는 "소그룹채플" — 실제 grade 분기로 정해진 값
    "lectures": [...],
    "count": K,
    "error": null
  },
  "cyber": {
    "category_type": "cyber",
    "params": {},
    "lectures": [...],
    "count": L,
    "error": null
  },
  "education": {  # teaching_certification 있을 때만
    "category_type": "education",
    "params": {},
    "lectures": [...],
    "count": J,
    "error": null
  },
  ...
}
```

실패한 카테고리는 `error: "에러 메시지"`, `lectures: []`, `count: 0`으로 채우고 다른 카테고리는 정상 진행.

취합 후 `save_lectures_cache(year, semester, groups)` 호출.

### 4. 결과 요약 출력

사용자에게:
- 총 강의 수 (모든 groups의 count 합)
- 카테고리별 count (예: "주전공 45건, 타전공인정 12건, 교양선택 12분야 187건, 교양필수 31과목 142건, 채플 3건, 사이버대 20건, 교직 8건")
- 실패한 카테고리 있으면 표시

### 5. 다음 단계 안내

- 캐시 적재 완료. 시간표 후보 생성 워크플로우(별도 스킬)로 자연스럽게 연결
- 사용자가 원하면 "시간표 짜줘" 같은 트리거로 다음 스킬 진입

## 프로필 부족 케이스 처리

| 상황 | 스킬 응답 |
|---|---|
| `college` 비어있음 | "주전공 학과의 단과대가 어디야? (예: IT대학, 인문대학)" → `set_user_profile("college", ...)` |
| `department` 비어있음 | `get_usaint_snapshot()` 호출로 USAINT 학적정보에서 재확보 유도 |
| `entered_year` 비어있음 | "입학연도 알려줘 (교양 분야 필터링에 필요)" → `set_user_profile("entered_year", ...)` |
| `double_major`/`minor` 있고 단과대 모름 | `load_department_map(year)` → `mapping[학과명]` 역조회. 키 없으면 사용자에게 "복수/부전공 학과 {X}의 단과대가 어디야?" 질문 |

## 캐시 무효화

- **TTL 7일**: `load_lectures_cache`의 `source: "stale"`로 표시
- **사용자 명시 새로고침**: "새로 가져와" / "갱신" 트리거 시, 캐시와 무관하게 3번부터 재실행 후 덮어쓰기
- **강의 데이터 변경**: 학기 중 강의 시간/교실 변경 시 사용자가 새로고침 선언으로 대응

## 비고

- **카테고리 활성 조건** (프로필 값 기반):
  - 복수전공: `profile.double_major` 있을 때 (단과대는 `load_department_map` 역조회, 실패 시 사용자 질문)
  - 부전공: `profile.minor` 있을 때 (동일)
  - 연계·융합: `profile.connected_major` 있을 때 (`connected_major` + `united_major` 양쪽 시도, 한쪽은 예외로 정상 무시)
  - 교직: `profile.teaching_certification == True`일 때
- **채플 lecture_name (grade 기반 단일 호출)**: `"채플"`은 무효값(에러). `grade==1` → `"소그룹채플"`, `grade>=2`/불명 → `"비전채플"`로 **한 번만** 조회해 `groups["chapel"]`에 담는다 (병렬 2회 아님). 사용자가 특정 채플명(예: "한국인채플")을 명시하면 grade 무관 그 이름으로. 두 종류는 grade로 분리돼 캐시에 섞이지 않는다 → composer는 grade에 맞는 한 종류만 있다고 가정.
- **교양필수**: `list_required_electives`가 반환한 과목명 전체를 `find_lectures(category_type="required_elective", lecture_name=<과목명>)`로 각각 조회. 연도 필터링 없음 (optional_elective와 달리 과목명에 학번 태그 없음)
- **교양필수 그룹 키 재사용**: `groups["required_elective_<과목명>"]`의 `<과목명>`은 `list_required_electives`가 반환한 **원본 문자열을 그대로** 써야 한다 (대괄호 `[SW와AI]`, 괄호 `(...)`, `&` 등 특수문자 포함). 후속 소비자가 키를 재구성할 때는 캐시의 groups 키를 그대로 조회하거나, 해당 그룹의 `params.lecture_name`(원본 과목명)으로 역조회하라.
- **인터뷰 결과 소비**: `subject_preferences`에서 필수 과목/관심 분야 추출해 우선 순위 반영 — **이 이슈에서는 보류**, 후속 PR
- 스킬은 `find_lectures`, `list_optional_elective_categories`, `list_required_electives` MCP 도구만 소비. 오케스트레이션은 스킬(LLM)이 담당
