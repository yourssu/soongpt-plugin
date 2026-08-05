---
name: soongpt-timetable-visualize
description: 조립 중/완성된 시간표를 브라우저에서 한 눈에 보는 정적 HTML로 시각화. 현재 대화에서 확정된 강의 후보를 lecture dict 목록 JSON으로 정리해 render_timetable.py(표준 라이브러리 전용 스탠드얼론 스크립트)로 요일×시간 그리드를 렌더링하고 기본 브라우저로 오픈. 시간 충돌 강의는 빨간 테두리로 강조. "시간표 보여줘", "시간표 시각화", "시간표 어떻게 생겼어", "이거 그려봐", "/soongpt-timetable-visualize"에서 호출.
---

# SoongPT Timetable Visualize

조립 중(부분 후보)이나 최종 확정된 시간표를 **정적 HTML 그리드**로 그려서 브라우저에서 바로 보여준다. 시간 충돌이 있는 강의는 빨간 테두리로 시각적으로 강조한다.

렌더링은 스킬 폴더 안의 `render_timetable.py`가 담당한다 (표준 라이브러리만 사용 — 어떤 python3로든 실행 가능, 플러그인 venv 경로 불필요).

## 트리거

- "시간표 보여줘", "시간표 시각화", "시간표 어떻게 생겼어"
- 시간표 후보를 정하고 나서 "이거 그려봐", "한 눈에 보고 싶어", "시각화해줘"
- "/soongpt-timetable-visualize"

## 입력 (중요한 제약)

- 이 스킬의 입력은 **`find_lectures` 반환과 동일한 lecture dict 목록**이다.
- 시간표 조립 파일 형식은 아직 미정이므로 **임의로 새 형식을 정의하지 않는다**. 조립 파일 형식이 확정되면 이 입력만 그에 맞게 조정한다.
- 표시할 후보는 대화 맥락에 이미 있는 강의 데이터(`find_lectures`/`load_lectures_cache` 결과 중 사용자가 선택/확정한 lecture dict)를 그대로 모아서 사용한다. 단, **builder·composer는 `load_lectures_cache(include_lectures=False)` 메타 모드로 호출하므로** 대화 맥락에는 lecture dict이 없다 — 렌더링 전에 `load_lectures_cache(year, semester, codes=<후보 lecture_codes>, include_groups=False)`로 **해당 강의 상세만**(groups 메타 없이 lectures만) 확보해야 한다. 전체 상세(`load_lectures_cache(year, semester)` 기본값)는 673KB로 파일 스필을 일으키므로 쓰지 않는다.

### lecture dict 스키마

`find_lectures` 반환 항목 그대로. 아래 필드만 있으면 렌더링에 충분하다.

| 필드 | 타입 | 필수 | 설명 |
|---|---|---|---|
| `code` | str | ✅ | 과목 코드 (분반 구분 포함) |
| `name` | str | — | 과목명 |
| `professor` | str | — | 교수명 |
| `department` | str | — | 학과 |
| `time_points` | str | — | `"학점/시수"` (예: `"3/3"`) |
| `schedule_room` | str | — | `"요일 HH:MM-HH:MM (강의실-교수)"` 블록을 리터럴 `\n`으로 연결. 비면(온라인/학점 0) 그리드에 미표시 |

예시:

```json
[
  {
    "code": "2150164203",
    "name": "알고리즘",
    "professor": "박은영",
    "department": "컴퓨터학부",
    "time_points": "3/3",
    "schedule_room": "월 수 10:30-12:00 (베어드홀 01101-김자헌)"
  },
  {
    "code": "2150164204",
    "name": "자료구조",
    "department": "컴퓨터학부",
    "time_points": "3/3",
    "schedule_room": "월 11:00-13:00 (숭덕 02108-박은영)"
  }
]
```

## 진입 절차 (필수)

순서대로 진행. 각 단계가 끝나야 다음 단계로 이동.

### 1. 표시할 강의 후보 확정

- 대화 맥락에서 사용자가 조립 중/확정한 시간표의 lecture dict 목록을 수집한다.
- 맥락에 후보가 없거나 어느 과목을 보여줄지 모호하면 **사용자에게 직접 확인**한다 ("어떤 과목들 보여줄까?").
- 필요한 원본 데이터(예: 강의 캐시)가 아직 없으면 **표시할 후보의 code 목록**으로 `load_lectures_cache(year, semester, codes=<lecture_codes>, include_groups=False)`를 호출해 해당 강의 상세만 확보한다. code 목록은 `load_timetable_candidates`의 `lecture_codes`(저장된 확정/조립 후보) 또는 대화 맥락에서 얻는다. 응답의 `lectures`(매칭 lecture dict 배열)를 입력 JSON에 그대로 쓴다. `include_groups=False`를 주면 시각화에 불필요한 전체 그룹 메타(~30-40KB)가 빠지고 `lectures`만 온다. **`load_lectures_cache(year, semester)` 전체 상세는 쓰지 않는다** — 673KB로 컨텍스트를 초과해 파일 스필이 난다.

### 2. 입력 JSON 파일 작성

- 1단계의 dict 목록을 **배열** 그대로 저장한다. 최상위 `{"lectures": [...]}` 래퍼도 허용되지만 배열이 기본.
- 경로는 **실행마다 유니크한 임시 파일**로 생성한다. 고정 경로(`/tmp/soongpt_timetable_*.json`)를 재사용하지 않는다 — 이전 실행이 남긴 입력 JSON을 새 데이터로 오인하거나 덮어쓰는 혼동을 막는다.
- 유니크 경로 생성 방법 (둘 중 하나):
  - **tempfile (권장)**: 아래 명령으로 새 빈 파일 경로를 받아 그 경로에 JSON을 쓴다. 생성 위치는 **OS 기본 임시 디렉터리**(macOS는 `$TMPDIR` → `/var/folders/...`, Linux는 `/tmp`)다.
    ```
    python3 -c "import tempfile; print(tempfile.mkstemp(prefix='soongpt_timetable_', suffix='.json')[1])"
    ```
  - **시각 기반 이름**: `<OS 기본 임시 디렉터리>/soongpt_timetable_<이름>_<YYYYMMDDHHMMSS>.json` (예: `/tmp/soongpt_timetable_candidate_20260805120430.json`)
- 어느 쪽이든 **이번 실행에서 만든 그 경로**를 3단계 렌더러에 그대로 넘긴다.
- 임시 파일은 어느 경로에 생기든 OS가 주기적으로 정리하므로, 남은 파일을 직접 지울 필요는 없다.
- 저장소 안에 넣지 않는다.
- **민감 정보 금지**: 학번/비밀번호/세션값은 절대 JSON에 넣지 않는다 (lecture dict 필드만).

### 3. 렌더러 실행

이 SKILL.md가 있는 폴더의 `render_timetable.py`를 실행한다:

```
python3 <이 SKILL.md 폴더 절대 경로>/render_timetable.py <2단계에서 만든 유니크 입력 JSON 경로> --open
```

- **어떤 python3로든** 실행 가능 (표준 라이브러리만 사용). `soongpt_mcp`가 설치된 환경이면 정확한 파서/충돌 검사를 재사용하고, 아니면 동일 스펙의 내장 파서로 폴백한다.
- **출력 경로**: `--out` 미지정 시 사용자 캐시 디렉토리(`$XDG_CACHE_HOME/soongpt/timetable.html` 또는 `~/.cache/soongpt/timetable.html`)에 저장하고, 저장 경로를 stdout으로 출력한다. **cwd(사용자 프로젝트/저장소 루트)를 오염시키지 않는다**.
- `--open`: 렌더링 후 기본 브라우저로 `file://` 오픈. headless/SSH 환경에서 브라우저가 안 열리면 `--open`을 빼고 `--out <경로>`로 파일만 만들고 사용자에게 경로를 알려준다.
- `--out <경로>`로 출력 파일 위치를 바꿀 수 있다 (명시 시 그 경로에 저장 — cwd 포함).
- 실행 실패 시 stderr 메시지를 보고 입력 JSON을 점검한다 (code 누락, JSON 문법 오류 등).

### 4. 결과 확인 + 안내

- 브라우저에서 확인되도록 안내하고, 결과 요약을 짧게 전한다 (수업 블록 수, 충돌 유무).
- **시간 충돌이 있으면** HTML에서 강조된(빨간 테두리) 과목쌍을 사용자에게 짚어주고, 후보를 조정할지 물어본다.

## 충돌 강조 동작

- 같은 요일 + 겹치는 시간대 강의는 **빨간 테두리 + "충돌" 배지**로 강조.
- 겹치는 블록은 시간축에 **나란히 배치**되어 서로 가리지 않는다.
- HTML 하단에 충돌 목록(요일/시각/과목쌍)을 텍스트로 출력.
- 파싱 실패/빈 `schedule_room` 강의는 그리드에 미표시되고 "파싱 경고" 목록으로 안내 (사용자에게 해당 과목은 시간 정보가 없어 표시하지 못했다고 전한다).
- 충돌 판정 로직은 `timetable_parsing.find_conflicts`(또는 동일 스펙의 내장 구현)와 일치하므로 `check_timetable_conflicts` 도구와 모순되지 않는다.

## 비고

- 이 스킬은 렌더링 전용. 시간표 후보 생성/조립은 별도 스킬에서 담당하며, 형식이 확정되면 이 스킬의 입력 JSON 작성 방식만 그에 맞게 바꾼다.
- `render_timetable.py`는 `soongpt_mcp` 패키지와 분리된 스탠드얼론 스크립트로, 플러그인 설치 환경뿐 아니라 스크립트만 복사해도 동작한다.
