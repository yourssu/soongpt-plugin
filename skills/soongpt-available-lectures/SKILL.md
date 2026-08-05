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
- `profile == null`이거나 필수 필드가 비면 `get_usaint_snapshot()` 호출 (USAINT 세션 필요 — 최초엔 브라우저 로그인 폼 자동 오픈. 프로필+수강이력을 한 번에 확보). **snapshot을 실제로 호출하기 전에** 사용자에게 "브라우저에 유세인트 로그인 창이 뜰 수 있어요" **사전 안내**를 전달한다 (SPR-115) — 프로필이 이미 채워져 snapshot을 호출하지 않으면 안내 불필요. 아래 SPR-85(실패 시 "창이 뜬 뒤" 대응)와 보완 관계
- **`get_usaint_snapshot()`이 로그인 필요/타임아웃 에러로 실패하면**: 시스템 오류가 아니라 **사용자 로그인 절차**다. 같은 조회를 자동으로 반복 재시도하지 말 것 (LLM 무한 재시도 유발, SPR-85):
  1. 사용자에게 "브라우저의 **웹 로그인 폼에서 로그인** 후 다시 시도해 달라"고 안내
  2. 사용자가 로그인을 마쳤다고 확인하면 `get_usaint_snapshot()`을 **한 번** 다시 호출 (재시도 시 로그인 폼이 새로 열림)
  3. 그래도 실패하면 자동 재시도를 반복하지 않고, 사용자에게 상태를 알린 뒤 중단
- 그래도 아래 **필수 필드** 중 하나라도 비어있으면 사용자에게 직접 물어 `set_user_profile(field, value)`로 입력받는다:
  - `college` (단과대)
  - `department` (주전공 학과)
  - `grade` (학년)
  - `entered_year` (입학연도 — 교양선택 학번 분야 판별에 필수. fetch 단계가 아닌 downstream composer에서 `field_tags`와 매칭)
- `set_user_profile`은 사용자가 직접 값을 입력해야 하는 경우에만 사용한다 (자동 온보딩 수단 아님).

### 2. 캐시 확인

- `load_lectures_cache(year, semester, include_lectures=False)` 호출 (year/semester는 현재 학기 — 1~7월="1", 8~12월="2")
- 응답의 `_cache.source`:
  - `"cache"`: 캐시 히트. **5번(다음 단계 안내)으로 바로 이동**
  - `"stale"`: 7일 경과. 3번으로 (사용자에게 "새로고침?" 물어볼지, 그냥 stale라도 쓸지 선택)
  - `"miss"`: 파일 없음. 3번으로
- `include_lectures=False` (SPR-76): 이 호출은 캐시 히트 여부만 확인하면 되므로 그룹 메타(카테고리·count·error·codes)만 받는다. 상세 강의는 3번 fetch가 서버에 저장하고, 3-D/4번에서 같은 메타 모드로 재확인한다.

사용자가 트리거에서 "새로" / "갱신" 뉘앙스를 포함했으면 source와 무관하게 3번으로.

### 3. 카테고리 세트 판단 + 병렬 fetch

스킬이 직접 `find_lectures`와 `list_required_electives`를 **한 번에 병렬**로 호출. MCP 도구를 단일 메시지에 여러 개 담아 병렬 실행. (교양선택은 "전체" 1회 호출이라 `list_optional_elective_categories`를 fetch에 쓰지 않음 — 3-B-1)

#### 3-0. find_lectures 묶음 크기 (~4개, 중요)

`find_lectures`를 **4개 초과**로 쏴야 하는 구간(주로 **3-B 교양 전체**; 3-A·3-C는 한 묶음에 다 들어오므로 그냥 한 번에 병렬)에서는 **한 번에 약 4개씩 묶음**으로 병렬 호출하고, 한 묶음이 끝나면 다음 묶음을 쏜다.

- **왜**: USAINT 포털(WebDynpro)이 같은 SSO 세션의 동시 요청을 서버에서 순차 처리한다. 18개를 한 번에 쏘면 마지막 것이 ~30초 대기하다 HTTP 타임아웃·WebDynpro 에러·SSO 세션 끊김 위험.
- **안전장치**: 서버가 `find_lectures`/`list_required_electives`/`list_optional_elective_categories`의 동시 송출을 **공유 Semaphore로 4개(기본값, `SOONGPT_COURSE_SCHEDULE_CONCURRENCY`로 조정)로 강제 제한**한다(SPR-67). 그래서 4개를 넘게 한 번에 쏴도 자동으로 대기열에 들어가 **안전**은 하다.
- **그런데도 묶음 단위가 낫다**: 묶음 단위로 쏘면 대기열을 거치지 않아 총 시간이 약간 줄고, 결과도 묶음별로 모아 처리하기 쉽다. 아래 "N회 병렬" 표기는 이 **~4개 묶음** 단위로 그룹화해 실행할 것. `list_required_electives`는 1회씩이므로 묶음 대상이 아님 (각 맨 앞 묶음에 포함해 같이 쏘면 된다).

#### 3-0b. 서버 측 자동 저장 (SPR-75) — 별도 save 불필요

`find_lectures`는 기본(`save_to_cache=True`)으로 **fetch 시점에 서버가 결과를 캐시에 즉시 그룹 저장**한다. 그래서:

- **취합 → save_lectures_cache 호출이 없다.** `save_lectures_cache` 도구는 제거됨. fetch가 끝나면 그 그룹은 이미 캐시에 들어 있다.
- **그룹 키도 서버가 자동 생성**한다 (아래 각 카테고리의 `groups["..."]` 표기는 자동 생성될 키 안내 — 스킬이 직접 키를 정하지 않는다).
- **모든 fetch는 `summary=True`를 붙인다** (SPR-76). 서버가 응답에서 lectures 상세를 생략하고 `count`/`fetchTime`/`_cache`(group_key/saved)만 반환한다 — 컨텍스트를 크게 아낀다. 결과는 이미 캐시에 저장되므로 상세가 필요하면 `load_lectures_cache(year, semester)`(기본 상세)로 재조회하면 된다. **예외: `find_by_lecture`/`find_by_professor` 확인용 조회는 summary 여부와 무관하게 서버가 항상 상세(lectures 포함)를 반환한다** (SPR-110) — 검색 결과의 code·시간을 직접 읽는 게 목적이라 요약 모드가 없다. **특정 강의 몇 개만 상세가 필요하면 `load_lectures_cache(year, semester, codes=[...], include_groups=False)`(SPR-88/SPR-92)로 해당 강의만 받는다** — 전체 상세(최대 673KB) 대신 후보 몇 개만 컨텍스트에 올라 파일 스필을 막고, 그룹 메타(~30-40KB)도 생략한다. 특히 교양선택 "전체"(약 337강의)는 요약 모드 없이는 응답이 ~220KB에 달하므로 반드시 `summary=True`다.
- **실패 카테고리는 예외가 그대로 온다** (연계/융합처럼 한쪽 실패가 정상인 경우 포함). 예외 후에는 `load_lectures_cache(year, semester, include_lectures=False)`로 해당 그룹의 `error` 필드를 확인해 재조회 여부를 판단한다. (기존 성공 그룹이 있으면 error 그룹으로 대체되지 않으니 데이터는 보존된다. `_cache.saved=False`는 "확인용 조회라 저장을 건너뜀"의 경우다.)
- **확인용 조회는 저장 제외**: `find_by_lecture`/`find_by_professor`/`include_details=True`는 `save_to_cache=False`를 주거나 (사실 서버가 강제로 저장을 건너뜀) 그냥 두면 된다. **응답은 summary 여부와 무관하게 항상 상세다 (SPR-110)** — 서버가 확인용 조회의 요약 모드를 무시하고 lectures(code)를 반환한다. 캐시 저장은 안 되지만 검색 결과에서 code·시간을 직접 읽는다.

#### 3-A. 전공 계열 (2~6회 병렬)

- **주전공** (필수 1회):
  ```
  find_lectures(year, semester, category_type="major",
                collage=profile.college, department=profile.department,
                major=None, summary=True)
  ```
  → 서버가 `groups["major_<collage>_<department>"]` 키로 자동 저장 (예: `major_IT대학_컴퓨터학부`)

- **주전공 타전공인정과목** (필수 1회):
  ```
  find_lectures(year, semester, category_type="recognized_other_major",
                collage=profile.college, department=profile.department,
                major=None, summary=True)
  ```
  → 서버가 `groups["recognized_other_major_<collage>_<department>"]` 키로 자동 저장

- **복수전공** (`profile.double_major` 있을 때만):
  ```
  find_lectures(year, semester, category_type="major",
                collage=<복수전공 단과대>, department=profile.double_major,
                major=None, summary=True)
  ```
  → 서버가 `groups["major_<복수전공 단과대>_<double_major>"]` 키로 자동 저장
  - **단과대 획득**: `profile`에 복수/부전공 단과대 필드가 없으므로 `load_department_map(year)` 매핑 `{학과명: 단과대}`에서 역조회. `mapping[profile.double_major]`로 단과대 획득
  - 매핑에 키가 없으면 사용자에게 "복수전공 학과 {X}의 단과대가 어디야?" 직접 질문
  - `load_department_map`은 **복수전공 또는 부전공이 있을 때만** 3번 진입 시 **한 번만** 선행 호출하고 두 카테고리가 같은 매핑 결과를 재사용 (둘 다 없으면 호출 불필요)

- **부전공** (`profile.minor` 있을 때만):
  - 복수전공과 동일 패턴: `load_department_map` 역조회로 단과대 확보(실패 시 fallback 질문) →
    ```
    find_lectures(year, semester, category_type="major",
                  collage=<부전공 단과대>, department=profile.minor,
                  major=None, summary=True)
    ```
  → 서버가 `groups["major_<부전공 단과대>_<minor>"]` 키로 자동 저장

- **연계·융합전공** (`profile.connected_major` 있을 때만):
  - rusaint 0.16.3은 **연계전공(`connected_major`)과 융합전공(`united_major`)을 별도 분류**로 제공. USAINT는 프로필 `connected_major`에 연계/융합을 통합 추출하므로 런타임에 어느 쪽인지 알 수 없음 → **양쪽 모두 시도**
  ```
  find_lectures(year, semester, category_type="connected_major",
                major=profile.connected_major, summary=True)
  find_lectures(year, semester, category_type="united_major",
                major=profile.connected_major, summary=True)
  ```
  → 각각 `groups["connected_major"]`, `groups["united_major"]` 키로 자동 저장
  - **일반적으로 한쪽이 실패**: 사용자 이수가 연계면 보통 `united_major`가, 융합이면 `connected_major`가 USAINT WebDynpro 예외(`Cannot find ... option in ...CONNECT_MAJO/UNMA...`)를 던짐. 빈 배열이 아니라 **예외**이며 서버가 캐시에 error 그룹으로 기록하고 예외를 그대로 올린다(정상 무시). 정상 한쪽은 강의 배열을 반환. **두 쪽 모두 성공(예: 과목이 양쪽에 걸쳐 개설)하면 둘 다 저장**
  - (런타임 검증은 임의 학과명으로 라우팅 건전성만 확인했으므로, 실제 이수자의 응답 패턴은 추후 검증 필요)

#### 3-B. 교양 전체 (선택 분야 + 필수 과목명 열거)

##### 3-B-1. 교양선택 전체 (1회)

교양선택은 학번별 분야로 쪼개 N회 부르지 않고 **"전체" 1회 호출**로 끝낸다. 실측
결과 한 강의의 `field`에 전 학번 분야가 줄바꿈(`\n`)으로 몰아있어, 어차피 같은 강의가
여러 분야에 중복 속한다. 분야별 분산 호출(5~25회)보다 "전체" 1회가 더 완전하고 호출
수가 압도적으로 적어(1 vs 5~25) 총 소요 시간·실패 위험 면에서 유리하다 — 다만 1회
호출 자체가 가볍다는 뜻은 아니다. **fetchTime은 학기·시간대에 따라 수초~30초대로 크게
변동**한다 (2026-2학기 실측 16.7초, 이전 측정엔 30초대 기록; 약 337강의 + 전 학번
분야 태그 반환). **학번 분야 판별은 downstream (composer)으로 이동** — fetch 단계
에서는 학번 필터링을 하지 않는다. 1회 호출이라 3-0 묶음 규칙 대상이 아니다.

- **사전 안내**: `find_lectures` 호출 **전에** 먼저 사용자에게 "교양선택 전체 조회는
  20초 내외 걸릴 수 있어요" 수준의 문구로 대기 시간을 미리 알린다. (위 fetchTime
  수초~30초대 변동, 실측 16.7초와 일관 — 단일 호출임에도 길 수 있음을 안내)

```
find_lectures(year, semester, category_type="optional_elective",
              category="전체", summary=True)
```
→ 서버가 `groups["optional_elective_all"]` 키로 자동 저장 (단일 그룹)

- `list_optional_elective_categories`는 이 흐름에서 **호출하지 않는다** (도구 자체는
  분야 목록 안내용으로 보존). 학번 분야 매칭은 composer가 `field_tags`(`field`를
  줄바꿈으로 분해한 태그 줄 리스트)와 `profile.entered_year`로 처리한다.
- **이 응답은 요약 모드(`summary=True`)로 받는다** (약 337강의 — 상세 응답은
  220KB). 서버가 lectures를 생략하고 `count`/`_cache`만 반환하므로 컨텍스트를
  아끼고, 상세는 캐시에 저장된 상태로 composer가
  `parse_lectures_cache(category_prefixes=["교선"])` 부분 조회 결과를
  학번 매칭으로 추려 올리는 방식으로 처리(→ composer 스킬).

##### 3-B-2. 교양필수 전체 과목명 (필수)

1. 과목명 목록 조회:
   ```
   list_required_electives(year, semester)
   ```
   - **사전 안내**: 조회 결과 과목명이 N개임을 확인한 뒤, **fetch 시작 전** 먼저
     사용자에게 "교양필수 N개 과목 조회는 2분 내외 걸릴 수 있어요" 수준의 문구로 대기
     시간을 안내한다. (실측: 31과목 기준 벽시계 2분 3초 — N이 클수록 안내 필수)
2. 반환된 **모든 과목명** 각각에 대해 `find_lectures` 병렬 호출 (약 4개씩 묶음 — 3-0 묶음 규칙, 이 단계가 호출 수가 가장 많음):
   ```
   find_lectures(year, semester, category_type="required_elective",
                 lecture_name="<과목명>", summary=True)
   ```
   → 서버가 `groups["required_elective_<과목명>"]` 키로 자동 저장
   - **묶음 사이 진행 알림**: 3-0 묶음 규칙(약 4개씩)대로 묶음을 쏠 때마다, 해당
     묶음 완료 후 사용자에게 "교양필수 X/N 과목 조회 완료" 형태로 진행 상황을
     알린다 (X = 완료된 과목 누계). 마지막 묶음이 끝나면 "교양필수 N/N 과목 조회
     완료"로 전체 완료를 안내한다.
   - optional_elective의 `[‘NN이후]` 학번 태그와 달리 교양필수 과목명은 **연도 태그가 없으므로 입학연도 필터링 없이 전부** 조회
   - 과목명은 분야 접두(예: `[SW와AI]AI개발과실전`)와 일반명(예: `한반도평화와통일`)이 혼재. `list_required_electives`가 반환한 문자열을 그대로 `lecture_name`으로 사용 (수강대상 제한은 target 필드로 별도 활용)
   - **빈 결과 처리**: 이번 학기 미개설 과목은 `find_lectures`가 빈 결과(`count: 0`)를 반환하므로, 그룹은 캐시에 빈 그룹으로 저장되되 **결과 요약(4단계)에서 count 0 그룹은 제외**한다. 예외가 나면 서버가 error 그룹을 기록하고 예외를 올린다 (정상 무시)

#### 3-C. 단일 카테고리 (2~3회 병렬)

- **채플** (필수 1회 — 실제 학년 기반 단일 호출, 병렬 2회 아님):
  - **채플 종류 분리 배경**: USAINT 채플은 `"비전채플"`(2학년+)과 `"소그룹채플"`(1학년)로 나뉘며, `lecture_name="채플"`은 **무효값**이라 WebDynpro 예외(`Cannot find 채플 option`)가 난다. 그래서 **정확한 채플명 둘 중 하나**로 조회해야 한다. 어느 한쪽만 필요하므로 실제 학년으로 한 번만 호출한다 (둘 다 부를 필요 없음).
  - **실제 학년(actual_grade) 분기** — `profile.actual_grade`(보정 전 실제 학년, PT-87 +1학기 보정과 무관) 사용. `actual_grade`가 없으면 `profile.grade`로 폴백:
    - `actual_grade == 1` (폴백 시 `profile.grade == 1`):
      ```
      find_lectures(year, semester, category_type="chapel",
                    lecture_name="소그룹채플", summary=True)
      ```
    - `actual_grade >= 2` **또는 actual_grade/grade 불명·None 폴백**:
      ```
      find_lectures(year, semester, category_type="chapel",
                    lecture_name="비전채플", summary=True)
      ```
  → 서버가 `groups["chapel"]` 키로 자동 저장 (단일 그룹 — 호출한 한 종류만 담김)
  - **왜 actual_grade인가**: `profile.grade`는 PT-87 임시 보정(+1학기)이 들어가 1학년 2학기 학생이 grade=2로 올라 비전채플로 오라우팅될 수 있다 (SPR-71). 채플은 1학년 전체(1학기/2학기 절반씩)가 소그룹채플이므로 **보정 전 실제 학년**으로 분기한다.
  - **actual_grade 불명 폴백 = 비전채플**: actual_grade는 보통 프로필(`get_user_profile().actual_grade`)에 있지만 구버전 데이터/수동 입력 등으로 없을 수 있다. 이때 `profile.grade`로 폴백하되, **`profile.grade == 2`면 PT-87 보정(+1학기)으로 1학년 2학기 학생이 2로 올라간 것일 수 있다** (2학년 1학기와 구분 불가). 스냅샷 새로고침(`get_usaint_snapshot`)으로 actual_grade를 확보하거나, 안 되면 사용자에게 현재 학년을 확인한다. 그것도 안 되면 기존 다수 사용자(2학년+) 기본값인 비전채플로.
  - **채플 종류의 데이터 격리**: actual_grade==1이면 캐시 `chapel`엔 소그룹채플만, actual_grade>=2면 비전채플만 들어간다. composer는 실제 학년에 맞는 한 종류만 있다고 가정하고 동작한다. (다른 채플명으로 재fetch하면 서버가 기존 chapel 그룹을 대체한다)
  - 사용자가 특정 채플명(예: "한국인채플")을 명시하면 실제 학년 무관 그 이름으로 조회.

- **숭실사이버대** (필수 1회):
  ```
  find_lectures(year, semester, category_type="cyber", summary=True)
  ```
  → 서버가 `groups["cyber"]` 키로 자동 저장

- **교직** (`profile.teaching_certification == True`일 때만):
  ```
  find_lectures(year, semester, category_type="education", summary=True)
  ```
  → 서버가 `groups["education"]` 키로 자동 저장
  - `teaching_certification`이 `False`/`None`이면 이 블록 생략

#### 3-D. 자동 저장 확인 + 실패 재조회

fetch가 끝나면 각 그룹은 **이미 서버가 캐시에 저장**했다. 별도 취합/save 없이
`load_lectures_cache(year, semester, include_lectures=False)`로 아래처럼 저장된
그룹 키·count·error를 확인한다 (메타 모드 — 그룹별 codes 포함):

- **자동 생성되는 그룹 키 규칙** (서버가 생성 — 스킬은 이 키를 기대만 하면 된다):
  - `major_<collage>_<department>` — 주전공/복수전공/부전공 각각
  - `recognized_other_major_<collage>_<department>`
  - `optional_elective_all` — 교양선택 "전체" (단일 그룹)
  - `required_elective_<과목명>` — 교양필수 과목별
  - `chapel`, `connected_major`, `united_major`, `cyber`, `education`
- **error 그룹**: fetch에 실패한 카테고리는 `lectures: []`, `count: 0`, `error: "<메시지>"`
  로 캐시에 기록된다 (연계/융합 중 정상적으로 실패하는 한쪽 포함 — 정상 무시).
- **재조회**: `load_lectures_cache` 응답에서 `error`가 있는 그룹 중 재시도가 필요한
  것(예: 일시적 네트워크 오류로 실패한 필수 카테고리)은 해당 `find_lectures`를 다시
  호출한다. 같은 조회를 재fetch하면 기존 그룹이 대체된다.

### 4. 결과 요약 출력

`load_lectures_cache(year, semester, include_lectures=False)`로 저장된 groups 메타를
확인하고 사용자에게:
- 총 강의 수 = 응답의 **`total_lectures`** 필드 (모든 groups의 count 합). 응답의 최상위
  `count`는 **그룹 수**(len(groups))이므로 총 강의 수와 혼동하지 않는다 (SPR-78).
- 카테고리별 count = 각 그룹의 `count` (예: "주전공 45건, 타전공인정 12건, 교양선택 337건(전 학번 분야 포함), 교양필수 31과목 142건, 채플 3건, 사이버대 20건, 교직 8건")
- `error`가 있는 실패 카테고리 있으면 표시 (연계/융합 정상 실패 한쪽 포함)

### 5. 다음 단계 안내

- 캐시 적재 완료. 시간표 후보 생성 워크플로우(별도 스킬)로 자연스럽게 연결
- 사용자가 원하면 "시간표 짜줘" 같은 트리거로 다음 스킬 진입

## 프로필 부족 케이스 처리

| 상황 | 스킬 응답 |
|---|---|
| `college` 비어있음 | "주전공 학과의 단과대가 어디야? (예: IT대학, 인문대학)" → `set_user_profile("college", ...)` |
| `department` 비어있음 | `get_usaint_snapshot()` 호출로 USAINT 학적정보에서 재확보 유도 |
| `entered_year` 비어있음 | "입학연도 알려줘 (교양선택 분야 판별에 필요)" → `set_user_profile("entered_year", ...)` |
| `double_major`/`minor` 있고 단과대 모름 | `load_department_map(year)` → `mapping[학과명]` 역조회. 키 없으면 사용자에게 "복수/부전공 학과 {X}의 단과대가 어디야?" 질문 |

## 캐시 무효화

- **TTL 7일**: `load_lectures_cache`의 `source: "stale"`로 표시
- **사용자 명시 새로고침**: "새로 가져와" / "갱신" 트리거 시, 캐시와 무관하게 3번부터 재실행. 재fetch는 같은 조회 그룹만 대체하는 병합 저장이라, 다른 그룹은 그대로 남는다 (캐시를 비우려면 stale 만료까지 대기하거나 변경된 카테고리만 재fetch)
- **강의 데이터 변경**: 학기 중 강의 시간/교실 변경 시 해당 카테고리만 재fetch로 갱신 (병합 저장)

## 비고

- **카테고리 활성 조건** (프로필 값 기반):
  - 복수전공: `profile.double_major` 있을 때 (단과대는 `load_department_map` 역조회, 실패 시 사용자 질문)
  - 부전공: `profile.minor` 있을 때 (동일)
  - 연계·융합: `profile.connected_major` 있을 때 (`connected_major` + `united_major` 양쪽 시도, 한쪽은 예외로 정상 무시)
  - 교직: `profile.teaching_certification == True`일 때
- **채플 lecture_name (실제 학년 기반 단일 호출)**: `"채플"`은 무효값(에러). `profile.actual_grade`(보정 전 실제 학년, 없으면 `profile.grade` 폴백) `== 1` → `"소그룹채플"`, `>= 2`/불명 → `"비전채플"`로 **한 번만** 조회해 `groups["chapel"]`에 담는다 (병렬 2회 아님). 사용자가 특정 채플명(예: "한국인채플")을 명시하면 실제 학년 무관 그 이름으로. 두 종류는 실제 학년으로 분리돼 캐시에 섞이지 않는다 → composer는 실제 학년에 맞는 한 종류만 있다고 가정.
- **교양필수**: `list_required_electives`가 반환한 과목명 전체를 `find_lectures(category_type="required_elective", lecture_name=<과목명>)`로 각각 조회. 연도 필터링 없음 (optional_elective와 달리 과목명에 학번 태그 없음)
- **교양필수 그룹 키 재사용**: `groups["required_elective_<과목명>"]`의 `<과목명>`은 `list_required_electives`가 반환한 **원본 문자열을 그대로** 써야 한다 (대괄호 `[SW와AI]`, 괄호 `(...)`, `&` 등 특수문자 포함). 후속 소비자가 키를 재구성할 때는 캐시의 groups 키를 그대로 조회하거나, 해당 그룹의 `params.lecture_name`(원본 과목명)으로 역조회하라.
- **인터뷰 결과 소비**: `subject_preferences`에서 필수 과목/관심 분야 추출해 우선 순위 반영 — **이 이슈에서는 보류**, 후속 PR
- 스킬은 `find_lectures`, `list_required_electives` MCP 도구를 소비. `list_optional_elective_categories`는 분야 목록 안내 용도로만 쓰고 3-B-1의 "전체" 1회 호출 흐름에서는 호출하지 않는다. 오케스트레이션은 스킬(LLM)이 담당
