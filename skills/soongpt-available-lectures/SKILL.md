---
name: soongpt-available-lectures
description: 숭실대 이번 학기 들을 수 있는 과목 통합 조회. 주전공·복수/부전공 학과 전체 + 교양선택 전체 분야를 find_lectures로 병렬 fetch해 로컬 캐시에 적재. "이번 학기 들을 수 있는 과목", "수업 후보 가져와", "강의 다 가져와", "/soongpt-available-lectures"에서 호출. 이후 시간표 후보 생성 워크플로우의 입력.
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
- `profile == null`이면 `refresh_user_profile()` 호출 (USAINT 세션 필요 — 최초엔 브라우저 로그인 폼 자동 오픈)
- 프로필이 여전히 비어있거나 아래 **필수 필드** 중 하나라도 비어있으면 사용자에게 보충 요청 후 중단:
  - `college` (단과대)
  - `department` (주전공 학과)
  - `grade` (학년)
  - `entered_year` (입학연도 — 교양 분야 학번 태그 필터링에 필수)
- 보충은 `set_user_profile(field, value)`로 사용자가 직접 입력

### 2. 캐시 확인

- `load_lectures_cache(year, semester)` 호출 (year/semester는 현재 학기 — 1~7월="1", 8~12월="2")
- 응답의 `_cache.source`:
  - `"cache"`: 캐시 히트. **5번(다음 단계 안내)으로 바로 이동**
  - `"stale"`: 7일 경과. 3번으로 (사용자에게 "새로고침?" 물어볼지, 그냥 stale라도 쓸지 선택)
  - `"miss"`: 파일 없음. 3번으로

사용자가 트리거에서 "새로" / "갱신" 뉘앙스를 포함했으면 source와 무관하게 3번으로.

### 3. 카테고리 세트 판단 + 병렬 fetch

스킬이 직접 `find_lectures`와 `list_optional_elective_categories`를 **한 번에 병렬**로 호출. MCP 도구를 단일 메시지에 여러 개 담아 병렬 실행.

#### 3-A. 전공 계열 (2~4회 병렬)

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
  - **주의**: `profile.double_major`의 단과대를 모르면 사용자에게 물어봄. 프로필에 별도 필드가 없으므로 런타임에만 알면 됨
  - 이 필드는 SPR-35 해결 후 추가됨. 그 전까지는 이 블록을 건너뛰고 주전공만

- **부전공** (`profile.minor` 있을 때만):
  - 위와 동일 패턴, `department=profile.minor`, `groups["major_minor"]`
  - SPR-35에서 `minor` 필드 추가 전까지 건너뜀

- **연계·융합전공** (`profile.connected_major` 있을 때만):
  ```
  find_lectures(year, semester, category_type="connected_major",
                major=profile.connected_major)
  ```
  → `groups["connected_major"]`에 저장
  - rusaint 0.16.3은 연계/융합을 `connected_major` 하나로 통합 제공하므로 단일 카테고리 호출로 충분
  - SPR-35에서 `connected_major` 필드 추가 전까지 건너뜀

#### 3-B. 교양선택 전체 분야 (10~15회 병렬)

1. 분야 목록 조회:
   ```
   list_optional_elective_categories(year, semester)
   ```
2. 입학연도 기준 분야 필터링:
   - `profile.entered_year >= 2023` → `"[‘23이후]"` 분야만 유지
   - `profile.entered_year < 2023` → `"[‘23이전]"` 분야만 유지
   - 입학연도 불분명하거나 다른 학번 태그가 있으면 일단 전부 유지 (안전)
3. 필터링된 각 분야별로 `find_lectures` 병렬 호출:
   ```
   find_lectures(year, semester, category_type="optional_elective",
                 category="<분야명>")
   ```
   → `groups["optional_elective_<분야명>"]`에 저장

#### 3-C. 단일 카테고리 (2~3회 병렬)

- **채플** (필수 1회):
  ```
  find_lectures(year, semester, category_type="chapel",
                lecture_name="채플")
  ```
  → `groups["chapel"]`에 저장
  - 기본 `lecture_name="채플"`로 폭넓게 검색 (모든 채플 변형 매칭)
  - 사용자가 특정 채플명(예: "비전채플", "한국인채플")을 지정하면 해당 이름으로 조회

- **숭실사이버대** (필수 1회):
  ```
  find_lectures(year, semester, category_type="cyber")
  ```
  → `groups["cyber"]`에 저장

- **교직** (`profile.teaching_certification == True`일 때만, SPR-36 선행):
  ```
  find_lectures(year, semester, category_type="education")
  ```
  → `groups["education"]`에 저장
  - `teaching_certification` 필드가 추가되기 전(SPR-36)까지는 건너뜀

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
  "optional_elective_[‘23이후]과학·기술": {
    "category_type": "optional_elective",
    "params": {"category": "[‘23이후]과학·기술"},
    "lectures": [...],
    "count": M,
    "error": null
  },
  "chapel": {
    "category_type": "chapel",
    "params": {"lecture_name": "채플"},
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
- 카테고리별 count (예: "주전공 45건, 타전공인정 12건, 교양선택 12분야 187건, 채플 3건, 사이버대 20건, 교직 8건")
- 실패한 카테고리 있으면 표시

### 5. 다음 단계 안내

- 캐시 적재 완료. 시간표 후보 생성 워크플로우(별도 스킬)로 자연스럽게 연결
- 사용자가 원하면 "시간표 짜줘" 같은 트리거로 다음 스킬 진입

## 프로필 부족 케이스 처리

| 상황 | 스킬 응답 |
|---|---|
| `college` 비어있음 | "주전공 학과의 단과대가 어디야? (예: IT대학, 인문대학)" → `set_user_profile("college", ...)` |
| `department` 비어있음 | 프로필부터 먼저 설정하도록 `/soongpt-interview`나 `refresh_user_profile`로 유도 |
| `entered_year` 비어있음 | "입학연도 알려줘 (교양 분야 필터링에 필요)" → `set_user_profile("entered_year", ...)` |
| `double_major`/`minor`의 단과대 모름 | "복수전공 학과 {X}의 단과대가 어디야?" |

## 캐시 무효화

- **TTL 7일**: `load_lectures_cache`의 `source: "stale"`로 표시
- **사용자 명시 새로고침**: "새로 가져와" / "갱신" 트리거 시, 캐시와 무관하게 3번부터 재실행 후 덮어쓰기
- **강의 데이터 변경**: 학기 중 강의 시간/교실 변경 시 사용자가 새로고침 선언으로 대응

## 비고

- **SPR-35 선행**: `double_major`, `minor`, `connected_major` 필드 추가 전까지는 복수/부전공/연계융합 계열 블록 건너뜀. 나머지 카테고리(주전공 + 타전공인정 + 교양 + 채플 + 사이버 + 교직)는 정상 조회
- **SPR-36 선행**: `teaching_certification` 필드가 추가되어야 교직(`education`) 카테고리 조회. 필드 추가 전에는 해당 블록 건너뜀
- **채플 lecture_name 기본값**: "채플"로 폭넓게 검색. 사용자가 특정 채플명(비전채플 등)을 지정하면 그 이름으로 조회
- **인터뷰 결과 소비**: `subject_preferences`에서 필수 과목/관심 분야 추출해 우선 순위 반영 — **이 이슈에서는 보류**, 후속 PR
- 스킬은 `find_lectures`와 `list_optional_elective_categories` MCP 도구만 소비. 오케스트레이션은 스킬(LLM)이 담당
