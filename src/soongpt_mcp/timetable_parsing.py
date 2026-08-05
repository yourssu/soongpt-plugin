"""강의 schedule_room 문자열 파싱 + 시간 충돌 검사 (순수 모듈).

세션/캐시/네트워크에 의존하지 않는 순수 함수만 담는다. 이 모듈의 출력은
시간표 조합 스킬(LLM)이 후보 생성을 위해 소비한다.

파싱 스펙 (검증된 잠금 범위 — 이 로직만 지원):
1. schedule_room 단일 포맷: `요일(들) HH:MM-HH:MM (강의실-교수)`.
   `\n`(리터럴 개행)으로 다중블록 연결. 정규식 1개로 파싱.
2. 시간 충돌 = 분 단위 비교 (요일 교집합 + 구간 겹침). 10/15/50/75분 간격
   혼재, 야간(22:15)까지.
3. 강의실/교수 분리 = 괄호 안 `rfind('-')`(마지막 하이픈). 강의실 내
   하이픈/괄호, 강의실 없음, 교수 결측 전부 처리.
4. 과목 그룹키(분반) = `code[:-2]`. name으로 묶지 말 것 (같은 name 다른 학과
   별개 수업 존재). 단 code 길이가 10이 아닐 때(구 과목코드 등)는
   `code[:-2]` 의미가 깨질 수 있어 parse_warnings로 경고.
5. dedup = code 전체 기준. 동일 과목이 여러 카테고리에서 중복 수집 정리.
6. 빈 schedule_room(온라인/학점 0) → parse_status="empty" (충돌 검사 패스).
7. 학점(time_points) = `"학점/시수"` 앞 숫자(float). 학점 0(사회봉사) 존재.
8. 파싱 실패 줄 → raw 보존 + parse_status="uncertain" → 충돌 검사 건너뜀.
9. field_tags = field를 줄바꿈으로 분해한 태그 줄 리스트. 교양선택 "전체"
   조회의 멀티라인 field(전 학번 분야)를 줄 단위로 정규화. 학번 매칭은 LLM.
"""
from __future__ import annotations

import re
from typing import Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, ValidationError

_WEEKDAY_ORDER = ("월", "화", "수", "목", "금", "토", "일")

# 포맷: 요일(들)[공백]HH:MM-HH:MM (강의실-교수)
# content는 greedy로 마지막 ')'까지 — 강의실 안 괄호(예: 01101-3(융합실습실)) 지원.
_SCHEDULE_ROOM_RE = re.compile(
    r"(?P<days>(?:[월화수목금토일]\s*)+)\s*"
    r"(?P<start>\d{1,2}:\d{2})\s*-\s*(?P<end>\d{1,2}:\d{2})\s*"
    r"\((?P<content>.+)\)$"
)

ParseStatus = Literal["ok", "uncertain", "empty"]


def _to_minutes(hhmm: str) -> int:
    """'HH:MM' → 자정 이후 분 (예: '10:30' → 630)."""
    hours, minutes = hhmm.split(":")
    return int(hours) * 60 + int(minutes)


def _format_minutes(minutes: int) -> str:
    """분 → 'HH:MM' (예: 630 → '10:30')."""
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


class TimeSlot(BaseModel):
    """단일 강의 블록의 시간/장소 슬롯."""

    model_config = ConfigDict(extra="forbid")

    days: list[str]
    start_min: int
    end_min: int
    room: str | None
    professor: str | None
    raw: str


class ParsedLecture(BaseModel):
    """강의 1개의 파싱 결과. LLM 판단 입력(target/field 등)을 pass-through.

    - parse_status: "ok"(정상 파싱) | "uncertain"(파싱 실패 줄 존재, 충돌 검사 제외)
      | "empty"(빈 schedule_room, 충돌 검사 패스)
    - subject_key = code[:-2] (분반 그룹키)
    - target/field/professor/division/department/category/sub_category: 원본
      Lecture에서 그대로 전달 — 수강 가능 판단(target 자연어 해석, field 학번
      매칭)과 이수구분 판단(category: "교필"/"전기-<학부명>"/"전필-<학부명>"/
      "전선-<학부명>"/"교선"/"교직")은 LLM 몫.
    - field_tags: field를 줄바꿈으로 분해한 태그 줄 리스트. 교양선택 "전체"
      조회의 전 학번 분야 태그를 LLM이 줄 단위로 매칭할 수 있게 정규화한 것.
      매칭 자체(어느 줄이 entered_year에 해당하는가)는 LLM이 판단한다.
    """

    model_config = ConfigDict(extra="forbid")

    code: str
    name: str | None
    subject_key: str
    credits: float | None
    slots: list[TimeSlot]
    parse_status: ParseStatus
    parse_warnings: list[str]
    raw: str | None
    # LLM 판단용 pass-through (원본 Lecture 필드)
    target: str | None = None
    field: str | None = None
    # field를 줄바꿈으로 분해한 학번별 태그 줄 리스트 ("[학번태그]분야명" 형태).
    # 교양선택(optional_elective) "전체" 조회 시 한 강의에 전 학번 분야가
    # 줄바꿈으로 몰아오므로, LLM이 entered_year에 해당하는 줄을 고를 때 쓴다.
    # 학번 **분해**만 이 모듈이, 학번 **매칭**(['20,'21~'22] 해석)은 LLM 몫.
    field_tags: list[str] = Field(default_factory=list)
    professor: str | None = None
    division: str | None = None
    department: str | None = None
    category: str | None = None
    sub_category: str | None = None


class Conflict(BaseModel):
    """두 강의 사이 시간 충돌 1건. days/start_min/end_min은 겹치는 구간."""

    model_config = ConfigDict(extra="forbid")

    code_a: str
    name_a: str | None
    code_b: str
    name_b: str | None
    days: list[str]
    start_min: int
    end_min: int
    slot_a_raw: str
    slot_b_raw: str
    message: str


def parse_schedule_room(schedule_room: str) -> list[TimeSlot]:
    """schedule_room 문자열을 TimeSlot 목록으로 파싱.

    `\n`(리터럴 개행)으로 연결된 블록을 순회해 포맷에 맞는 것만 슬롯으로 만든다.
    파싱 실패 블록과 비정상 시간 구간(종료<=시작, 예: 23:00-00:30 자정 경유)은
    건너뛴다 — 호출자(parse_lectures)가 raw 블록 수와 슬롯 수를 비교해
    parse_status="uncertain"을 판정한다.
    """
    slots: list[TimeSlot] = []
    for block in schedule_room.split("\n"):
        block = block.strip()
        if not block:
            continue
        match = _SCHEDULE_ROOM_RE.match(block)
        if match is None:
            continue

        days = [d for d in re.findall(r"[월화수목금토일]", match.group("days"))]
        content = match.group("content").strip()
        room: str | None
        professor: str | None
        hyphen = content.rfind("-")
        if hyphen >= 0:
            room = content[:hyphen].strip() or None
            professor = content[hyphen + 1 :].strip() or None
        else:
            room = content or None
            professor = None

        start_min = _to_minutes(match.group("start"))
        end_min = _to_minutes(match.group("end"))
        if end_min <= start_min:
            continue

        slots.append(
            TimeSlot(
                days=days,
                start_min=start_min,
                end_min=end_min,
                room=room,
                professor=professor,
                raw=block,
            )
        )
    return slots


def extract_credits(time_points: str | None) -> float | None:
    """'학점/시수' 문자열에서 학점(앞 숫자) 추출. 예: '3/3' → 3.0, '0/1' → 0.0.

    None/빈 문자열/숫자 시작이 아니면 None (합산에서 제외).
    """
    if not time_points:
        return None
    match = re.match(r"\s*(\d+(?:\.\d+)?)", str(time_points))
    if match is None:
        return None
    return float(match.group(1))


def parse_field_tags(field: str | None) -> list[str]:
    """field를 줄바꿈으로 분해해 학번별 태그 줄 리스트로 반환.

    교양선택(optional_elective) "전체" 조회 시 한 강의의 field에 전 학번 분야가
    줄바꿈(`\\n`)으로 연결돼 온다(예: "['23이후]문화·예술\\n['20,'21~'22]...").
    각 줄은 "[학번태그]분야명" 형태. 빈 줄/공백은 제거한다.

    None/빈 문자열 → []. 단일 줄 field(예: 전공의 "[4차]") → 1요소 리스트.
    시그니처는 `str | None`지만 `str(field)`로 감싸 raw.get 결과가 예상치 못한
    타입(list 등)으로 넘어와도 방어한다.

    학번 **분해**(줄 나누기)만 수행 — "[학번태그]" ↔ entered_year **매칭**은
    LLM이 판단한다(태그 범위 해석: '23이후/'20,'21~'22/'16-'18/'15이전 등).
    """
    if not field:
        return []
    return [line.strip() for line in str(field).split("\n") if line.strip()]


def parse_lectures(lectures: list[dict[str, Any]]) -> list[ParsedLecture]:
    """원본 강의 dict 목록을 ParsedLecture 목록으로 변환 (dedup by code).

    - schedule_room이 없는 dict나 code가 없는 dict는 건너뛴다.
    - 동일 code가 여러 번 나오면 최초 항목을 유지 (다른 카테고리 중복 수집 정리).
    - subject_key = code[:-2] (10자리 가정; 길이가 다르면 parse_warnings 경고).
    """
    seen: dict[str, ParsedLecture] = {}
    for raw in lectures:
        code = raw.get("code")
        if not code:
            continue
        code = str(code)
        if code in seen:
            continue

        schedule_room = raw.get("schedule_room") or ""
        raw_blocks = [b.strip() for b in schedule_room.split("\n") if b.strip()]
        slots = parse_schedule_room(schedule_room)

        warnings: list[str] = []
        if not raw_blocks:
            status: ParseStatus = "empty"
        elif len(slots) != len(raw_blocks):
            status = "uncertain"
            warnings.append(
                f"schedule_room 파싱 실패 줄 존재 (raw {len(raw_blocks)}블록 중 "
                f"{len(slots)}블록만 파싱됨): {code}"
            )
        else:
            status = "ok"

        subject_key = code[:-2]
        if len(code) != 10:
            warnings.append(
                f"code 길이 {len(code)}자 (10자리 아님) — subject_key=code[:-2] "
                f"의미가 깨질 수 있음: {code}"
            )

        seen[code] = ParsedLecture(
            code=code,
            name=raw.get("name"),
            subject_key=subject_key,
            credits=extract_credits(raw.get("time_points")),
            slots=slots,
            parse_status=status,
            parse_warnings=warnings,
            raw=schedule_room or None,
            target=raw.get("target"),
            field=raw.get("field"),
            field_tags=parse_field_tags(raw.get("field")),
            professor=raw.get("professor"),
            division=raw.get("division"),
            department=raw.get("department"),
            category=raw.get("category"),
            sub_category=raw.get("sub_category"),
        )
    return list(seen.values())


def build_subject_groups(parsed: list[ParsedLecture]) -> dict[str, list[str]]:
    """subject_key → [해당 subject_key의 모든 code] 인덱스.

    dedup 후 parsed 기준으로 계산한 편의 인덱스. 컴포저는 이 인덱스로 분반 그룹을
    잡고, 각 code로 parsed에서 ParsedLecture를 조회한다.
    """
    groups: dict[str, list[str]] = {}
    for lecture in parsed:
        groups.setdefault(lecture.subject_key, []).append(lecture.code)
    return groups


def coerce_conflict_lecture(data: dict[str, Any]) -> ParsedLecture:
    """충돌 검사 입력을 ParsedLecture로 완화 변환 (check_timetable_conflicts용).

    parse_lectures_cache의 전체 parsed dict는 엄격 검증으로 그대로 통과시킨다
    (하위 호환). LLM이 최소 필드(code/name/credits/slots/parse_status)만 넘겨도
    동작하도록, 누락 가능한 선택 필드는 다음 기본값으로 채운다:

      - subject_key: code[:-2] (parse_lectures와 동일 규칙 — 분반 중복 판정용)
      - parse_status: 기본 "ok", 단 slots=[]면 "empty" (빈 강의는 충돌 검사 제외)
      - parse_warnings / raw: [] / None (충돌 검사 로직에서 미사용)
      - slots[].room / professor: None (충돌 검사 로직에서 미사용)
      - slots[].raw: "월 10:30-12:00" 형태로 재구성 (충돌 메시지 표시용)

    주의: 이 lax 경로는 충돌 검사 전용이다. pass-through 필드(target/field/
    professor/division/department/category/sub_category/field_tags)는 기본값으로
    유실된다 — 충돌 검사 결과를 후속 조합 입력에 재사용하지 말 것. 전체 parsed
    dict를 넘기면 모든 필드가 그대로 보존된다.

    code/slots 누락 시 ValueError, 타입 오류 시 TypeError, 잘못된 parse_status
    시 ValueError를 던진다 (조용한 "충돌 없음" 오판 방지).
    """
    try:
        return ParsedLecture.model_validate(data)
    except ValidationError:
        pass

    if not isinstance(data, dict):
        raise TypeError(f"강의 항목은 dict여야 합니다: {data!r}")
    code = str(data.get("code") or "")
    if not code:
        raise ValueError("강의 항목에 code가 필요합니다")

    slots_data = data.get("slots")
    if slots_data is None:
        raise ValueError(f"code {code}: 충돌 검사에 slots가 필요합니다")
    if not isinstance(slots_data, list):
        raise TypeError(f"code {code}: slots는 list여야 합니다")

    slots: list[TimeSlot] = []
    for slot in slots_data:
        if not isinstance(slot, dict):
            raise TypeError(f"code {code}: 각 slot은 dict여야 합니다: {slot!r}")
        days_raw = slot.get("days")
        if not isinstance(days_raw, list) or not days_raw:
            raise ValueError(f"code {code}: 각 slot에 days(요일 list)가 필요합니다")
        if slot.get("start_min") is None or slot.get("end_min") is None:
            raise ValueError(f"code {code}: 각 slot에 start_min/end_min이 필요합니다")
        days = [str(d) for d in days_raw]
        start_min = int(slot["start_min"])
        end_min = int(slot["end_min"])
        raw = slot.get("raw")
        if not raw:
            raw = f"{''.join(days)} {_format_minutes(start_min)}-{_format_minutes(end_min)}"
        slots.append(
            TimeSlot(
                days=days,
                start_min=start_min,
                end_min=end_min,
                room=slot.get("room"),
                professor=slot.get("professor"),
                raw=str(raw),
            )
        )

    subject_key = data.get("subject_key")
    if not subject_key:
        subject_key = code[:-2]  # parse_lectures와 동일 규칙 (길이 무관 code[:-2])

    valid_statuses = get_args(ParseStatus)
    explicit_status = data.get("parse_status")
    if explicit_status is not None and explicit_status not in valid_statuses:
        raise ValueError(
            f"code {code}: parse_status는 {'/'.join(valid_statuses)} 중 "
            f"하나여야 합니다 (전달: {explicit_status!r})"
        )
    parse_status: ParseStatus = (
        explicit_status
        if explicit_status is not None
        else ("empty" if not slots else "ok")
    )

    try:
        return ParsedLecture(
            code=code,
            name=data.get("name"),
            subject_key=subject_key,
            credits=data.get("credits"),
            slots=slots,
            parse_status=parse_status,
            parse_warnings=data.get("parse_warnings", []),
            raw=data.get("raw"),
        )
    except ValidationError as exc:
        raise ValueError(
            f"code {code}: 잘못된 강의 dict입니다 (name/credits 등 타입 확인): {exc}"
        ) from exc


def _slots_overlap(sa: TimeSlot, sb: TimeSlot) -> bool:
    """요일 교집합 + 분 단위 구간 겹침. 인접 경계(종료==시작)는 충돌 아님."""
    if not (set(sa.days) & set(sb.days)):
        return False
    return sa.start_min < sb.end_min and sb.start_min < sa.end_min


def has_time_conflict(a: ParsedLecture, b: ParsedLecture) -> bool:
    """두 강의가 시간 충돌하는지. uncertain/empty는 검사에서 제외(False)."""
    if a.parse_status != "ok" or b.parse_status != "ok":
        return False
    return any(
        _slots_overlap(slot_a, slot_b) for slot_a in a.slots for slot_b in b.slots
    )


def _build_conflict(
    a: ParsedLecture, b: ParsedLecture, slot_a: TimeSlot, slot_b: TimeSlot
) -> Conflict:
    overlap_days = sorted(
        set(slot_a.days) & set(slot_b.days), key=_WEEKDAY_ORDER.index
    )
    start_min = max(slot_a.start_min, slot_b.start_min)
    end_min = min(slot_a.end_min, slot_b.end_min)
    message = (
        f"[{a.code}] {a.name or ''} ({slot_a.raw}) 과 "
        f"[{b.code}] {b.name or ''} ({slot_b.raw}) 가 "
        f"{'/'.join(overlap_days)} {_format_minutes(start_min)}-{_format_minutes(end_min)} 겹침"
    )
    if a.subject_key == b.subject_key:
        message += (
            " (같은 과목 분반 중복 선택 — 시간 충돌이 아닌 과목 중복으로 처리)"
        )
    return Conflict(
        code_a=a.code,
        name_a=a.name,
        code_b=b.code,
        name_b=b.name,
        days=overlap_days,
        start_min=start_min,
        end_min=end_min,
        slot_a_raw=slot_a.raw,
        slot_b_raw=slot_b.raw,
        message=message,
    )


def find_conflicts(lectures: list[ParsedLecture]) -> list[Conflict]:
    """후보 강의 목록에서 시간 충돌을 찾는다 (uncertain/empty 제외).

    Conflict 1건 = 겹치는 슬롯쌍 1개. 한 쌍이 여러 슬롯에서 겹치면(예: 월/화
    둘 다 겹침) 겹치는 슬롯쌍마다 별도 Conflict로 보고한다.
    """
    conflicts: list[Conflict] = []
    count = len(lectures)
    for i in range(count):
        for j in range(i + 1, count):
            a, b = lectures[i], lectures[j]
            if a.parse_status != "ok" or b.parse_status != "ok":
                continue
            for slot_a in a.slots:
                for slot_b in b.slots:
                    if _slots_overlap(slot_a, slot_b):
                        conflicts.append(_build_conflict(a, b, slot_a, slot_b))
    return conflicts
