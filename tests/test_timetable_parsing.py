"""강의 schedule_room 파싱 + 시간 충돌 검사 테스트 (이슈 A).

순수 함수 검증 위주 — 실제 schedule_room 샘플 문자열을 인라인으로 사용
(test_fetchers_subject_names.py 방식). 마지막 섹션에서 server 도구
(parse_lectures_cache / check_timetable_conflicts) 직접 호출 검증 추가.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from soongpt_mcp import lectures_cache as cache_mod
from soongpt_mcp import server
from soongpt_mcp.lectures_cache import LectureGroupEntry, LecturesCache
from soongpt_mcp.timetable_parsing import (
    CASE_A_MARKER,
    ParsedLecture,
    build_subject_groups,
    coerce_conflict_lecture,
    duplicate_slot_raws,
    extract_credits,
    field_tags_for_entered_year,
    filter_parsed_by_entered_year,
    filter_parsed_lectures,
    find_case_a_lectures,
    find_conflicts,
    has_time_conflict,
    parse_field_tags,
    parse_lectures,
    parse_schedule_room,
)


@pytest.fixture
def isolated_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """CLAUDE_PLUGIN_DATA → tmp_path."""
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path))
    return tmp_path


def _lecture(**overrides: object) -> dict:
    """rusaint _dump_lecture 출력 형태의 강의 dict."""
    base = {
        "code": "2150164203",
        "name": "알고리즘",
        "category": "전공",
        "sub_category": None,
        "field": "[4차]",
        "division": "전공필수",
        "professor": "박은영",
        "department": "컴퓨터학부",
        "time_points": "3/3",
        "schedule_room": "월 10:30-12:00 (베어드홀 01101-김자헌)",
        "target": "컴퓨터학부 2학년",
    }
    base.update(overrides)
    return base


# ── parse_schedule_room ────────────────────────────────────────────────


def test_parse_single_block() -> None:
    slots = parse_schedule_room("월 10:30-12:00 (베어드홀 01101-김자헌)")
    assert len(slots) == 1
    slot = slots[0]
    assert slot.days == ["월"]
    assert slot.start_min == 630
    assert slot.end_min == 720
    assert slot.room == "베어드홀 01101"
    assert slot.professor == "김자헌"
    assert slot.raw == "월 10:30-12:00 (베어드홀 01101-김자헌)"


def test_parse_multiple_days() -> None:
    slots = parse_schedule_room("화 목 10:30-12:00 (숭덕 02108-박은영)")
    assert len(slots) == 1
    assert slots[0].days == ["화", "목"]


def test_parse_room_with_paren_hyphen() -> None:
    """강의실 안 괄호/하이픈: rfind('-')로 교수와 분리."""
    slots = parse_schedule_room(
        "월 10:30-12:00 (베어드홀 01101-3(융합실습실)-김자헌)"
    )
    assert len(slots) == 1
    assert slots[0].room == "베어드홀 01101-3(융합실습실)"
    assert slots[0].professor == "김자헌"


def test_parse_no_room() -> None:
    """강의실 없음: '(-김종배)' → room None, professor 유지."""
    slots = parse_schedule_room("월 10:30-12:00 (-김종배)")
    assert len(slots) == 1
    assert slots[0].room is None
    assert slots[0].professor == "김종배"


def test_parse_no_professor() -> None:
    """교수 결측: '(숭덕 02108-)' → professor None, room 유지."""
    slots = parse_schedule_room("월 10:30-12:00 (숭덕 02108-)")
    assert len(slots) == 1
    assert slots[0].room == "숭덕 02108"
    assert slots[0].professor is None


def test_parse_multi_block_literal_newline() -> None:
    """멀티블록 = 리터럴 개행('\\n')으로 연결."""
    slots = parse_schedule_room(
        "월 10:30-12:00 (베어드홀 01101-김자헌)\n"
        "수 13:30-15:00 (베어드홀 01201-박은영)"
    )
    assert len(slots) == 2
    assert slots[0].days == ["월"]
    assert slots[1].days == ["수"]
    assert slots[1].end_min == 900


def test_parse_no_space_between_day_and_time() -> None:
    """요일과 시간 사이 공백 없는 케이스도 허용."""
    slots = parse_schedule_room("월10:30-12:00 (베어드홀 01101-김자헌)")
    assert len(slots) == 1
    assert slots[0].days == ["월"]
    assert slots[0].start_min == 630


def test_parse_night_time() -> None:
    """야간 22:15 종료 지원."""
    slots = parse_schedule_room("화 20:30-22:15 (베어드홀 01201-김자헌)")
    assert len(slots) == 1
    assert slots[0].end_min == 22 * 60 + 15


def test_parse_saturday_slot() -> None:
    """토요일 슬롯 파싱."""
    slots = parse_schedule_room("토 13:30-14:45 (베어드홀 01101-김자헌)")
    assert len(slots) == 1
    assert slots[0].days == ["토"]
    assert slots[0].start_min == 13 * 60 + 30
    assert slots[0].end_min == 14 * 60 + 45


def test_parse_three_days() -> None:
    """요일 3개 나열(월 수 금)."""
    slots = parse_schedule_room("월 수 금 10:30-12:00 (베어드홀 01101-김자헌)")
    assert len(slots) == 1
    assert slots[0].days == ["월", "수", "금"]


def test_parse_invalid_time_range_skipped() -> None:
    """end <= start(23:00-00:30 자정 경유) 비정상 구간은 스킵."""
    slots = parse_schedule_room("월 23:00-00:30 (베어드홀 01101-김자헌)")
    assert slots == []


def test_parse_failed_block_skipped() -> None:
    """포맷 불일치 블록은 슬롯으로 만들지 않는다 (uncertain은 호출자 판정)."""
    slots = parse_schedule_room("온라인 강의")
    assert slots == []


def test_parse_schedule_room_dedup_identical_slots() -> None:
    """(요일+시작+종료) 동일 슬롯 중복 → 1개만 유지 (SPR-82).

    러시아우트 원본이 같은 schedule_room 블록을 2회 주는 실제 케이스
    (교선 AI시대의정보보안 2150057301 "화 09:00-10:15" 중복).
    """
    slots = parse_schedule_room(
        "화 09:00-10:15 (벤처중소기업센터 10309 (이도영강의실)-장의진)\n"
        "화 09:00-10:15 (벤처중소기업센터 10309 (이도영강의실)-장의진)"
    )
    assert len(slots) == 1
    assert slots[0].days == ["화"]
    assert slots[0].start_min == 9 * 60
    assert slots[0].end_min == 10 * 60 + 15


def test_parse_schedule_room_dedup_keeps_distinct_slots() -> None:
    """중복과 별개 슬롯이 섞여 있어도 별개 슬롯은 보존."""
    slots = parse_schedule_room(
        "월 10:30-12:00 (베어드홀 01101-김자헌)\n"
        "월 10:30-12:00 (베어드홀 01101-김자헌)\n"
        "수 13:30-15:00 (베어드홀 01201-박은영)"
    )
    assert len(slots) == 2
    assert [s.days[0] for s in slots] == ["월", "수"]


def test_parse_schedule_room_dedup_first_when_room_differs() -> None:
    """같은 시간 다른 강의실/교수여도 (요일+시작+종료) 동일하면 첫 항목 유지.

    같은 시각에 두 강의실에 동시 존재할 수 없으므로 중복으로 간주해 제거한다
    (SPR-82 dedup 키 = 요일+시작+종료).
    """
    slots = parse_schedule_room(
        "화 09:00-10:15 (벤처중소기업센터 10309-장의진)\n"
        "화 09:00-10:15 (벤처중소기업센터 99999-김다른)"
    )
    assert len(slots) == 1
    assert slots[0].room == "벤처중소기업센터 10309"
    assert slots[0].professor == "장의진"


# ── extract_credits ────────────────────────────────────────────────────


def test_extract_credits_zero() -> None:
    assert extract_credits("0/1") == 0.0


def test_extract_credits_normal() -> None:
    assert extract_credits("3/3") == 3.0


def test_extract_credits_decimal() -> None:
    assert extract_credits("1.5/2") == 1.5


def test_extract_credits_missing() -> None:
    assert extract_credits(None) is None
    assert extract_credits("") is None
    assert extract_credits("학점없음") is None


# ── parse_field_tags (교양선택 멀티라인 field 분해) ─────────────────────


def test_parse_field_tags_none_empty() -> None:
    """None/빈 문자열 → 빈 리스트."""
    assert parse_field_tags(None) == []
    assert parse_field_tags("") == []


def test_parse_field_tags_single_line() -> None:
    """단일 줄 field(전공의 '[4차]') → 1요소."""
    assert parse_field_tags("[4차]") == ["[4차]"]


def test_parse_field_tags_multiline_all_years() -> None:
    """교양선택 '전체' 조회의 멀티라인 field — 전 학번 분야가 줄바꿈으로 연결.

    이슈 실측 예시: (외국인을위한)한류와대중문화의이해 의 field.
    각 줄은 '[학번태그]분야명' 형태. 분해만 하고 학번 매칭은 LLM 영역.
    """
    field = (
        "['23이후]문화·예술\n"
        "['20,'21~'22]의사소통/글로벌,기초역량-한국어의사소통\n"
        "['19]기초역량-한국어의사소통과국제어문\n"
        "['16-'18]기초역량(한국어의사소통-읽기와쓰기)\n"
        "['15이전]창의성과의사소통능력(핵심"
    )
    tags = parse_field_tags(field)
    assert len(tags) == 5
    assert tags[0] == "['23이후]문화·예술"
    assert tags[-1] == "['15이전]창의성과의사소통능력(핵심"


def test_parse_field_tags_strips_blank_lines() -> None:
    """빈 줄 / whitespace-only 줄 / 선행-후행 공백은 제거, 줄내 공백은 보존."""
    field = "\n  ['23이후]과학·기술  \n\n   \n['19]기초역량\n"
    tags = parse_field_tags(field)
    assert tags == ["['23이후]과학·기술", "['19]기초역량"]


def test_parse_field_tags_crlf_line_endings() -> None:
    """CRLF(\\r\\n) 라인 엔딩 — strip()이 \\r을 제거해 \\n만일 때와 동일 결과.

    rusaint/USAINT는 \\n만 보내므로 실결함은 아니나, 줄 종결자 처리가 strip()에
    암묵 의존 중 — split/splitlines 변경 시 회귀를 이 테스트가 잡는다.
    """
    field = "['23이후]과학·기술\r\n['19]기초역량\r\n"
    assert parse_field_tags(field) == ["['23이후]과학·기술", "['19]기초역량"]


# ── parse_lectures (평탄화 + subject_key + dedup + pass-through) ───────


def test_parse_lectures_ok_status() -> None:
    parsed = parse_lectures([_lecture()])
    assert len(parsed) == 1
    p = parsed[0]
    assert p.code == "2150164203"
    assert p.name == "알고리즘"
    assert p.subject_key == "21501642"
    assert p.credits == 3.0
    assert p.parse_status == "ok"
    assert p.parse_warnings == []
    assert len(p.slots) == 1


def test_parse_lectures_empty_schedule_room() -> None:
    """빈 schedule_room(온라인/학점 0) → parse_status='empty', 충돌 검사 패스."""
    parsed = parse_lectures([_lecture(schedule_room="", time_points="0/1")])
    assert parsed[0].parse_status == "empty"
    assert parsed[0].slots == []
    assert parsed[0].credits == 0.0


def test_parse_lectures_uncertain_preserves_raw() -> None:
    """파싱 실패 줄 → uncertain + raw 보존."""
    raw = "온라인 강의"
    parsed = parse_lectures([_lecture(schedule_room=raw)])
    assert len(parsed) == 1
    assert parsed[0].parse_status == "uncertain"
    assert parsed[0].raw == raw
    assert any("파싱 실패" in w for w in parsed[0].parse_warnings)


def test_parse_lectures_partial_failure_uncertain() -> None:
    """멀티블록 중 일부만 실패해도 uncertain (raw 보존)."""
    raw = "월 10:30-12:00 (베어드홀 01101-김자헌)\n온라인"
    parsed = parse_lectures([_lecture(schedule_room=raw)])
    assert parsed[0].parse_status == "uncertain"
    assert parsed[0].raw == raw


def test_parse_lectures_invalid_time_range_uncertain() -> None:
    """비정상 시간 구간(23:00-00:30) 포함 블록 → uncertain + 정상 슬롯만 유지."""
    raw = (
        "월 10:30-12:00 (베어드홀 01101-김자헌)\n"
        "화 23:00-00:30 (베어드홀 01201-박은영)"
    )
    parsed = parse_lectures([_lecture(schedule_room=raw)])
    assert parsed[0].parse_status == "uncertain"
    assert len(parsed[0].slots) == 1
    assert parsed[0].slots[0].days == ["월"]


def test_parse_lectures_dedup_by_code() -> None:
    """동일 code 중복 수집 → 최초 항목 유지."""
    parsed = parse_lectures(
        [_lecture(name="알고리즘"), _lecture(name="알고리즘(중복)")]
    )
    assert len(parsed) == 1
    assert parsed[0].name == "알고리즘"


def test_parse_lectures_dedup_slots_keeps_ok_status() -> None:
    """중복 슬롯 dedup: status는 ok 유지 + dedup 경고 + raw 원본 보존 (SPR-82).

    모든 블록이 파싱됐는데 (요일+시작+종료) 중복만 있는 경우 uncertain으로
    오판하면 안 된다 — 중복 제거는 데이터 품질 정리이지 파싱 실패가 아니다.
    """
    raw = (
        "화 09:00-10:15 (벤처중소기업센터 10309 (이도영강의실)-장의진)\n"
        "화 09:00-10:15 (벤처중소기업센터 10309 (이도영강의실)-장의진)"
    )
    parsed = parse_lectures(
        [
            _lecture(
                code="2150057301",
                name="AI시대의정보보안",
                category="교선",
                schedule_room=raw,
            )
        ]
    )
    p = parsed[0]
    assert p.parse_status == "ok"
    assert len(p.slots) == 1
    assert p.slots[0].days == ["화"]
    assert p.raw == raw  # 원본은 보존 (파싱 결과에서만 정리)
    assert any("동일 슬롯 중복 1건 제거" in w for w in p.parse_warnings)


def test_parse_lectures_dedup_does_not_mask_parse_failure() -> None:
    """중복 dedup이 실제 파싱 실패를 가리면 안 된다 — 실패 블록 있으면 uncertain."""
    raw = (
        "화 09:00-10:15 (벤처중소기업센터 10309 (이도영강의실)-장의진)\n"
        "화 09:00-10:15 (벤처중소기업센터 10309 (이도영강의실)-장의진)\n"
        "온라인"
    )
    parsed = parse_lectures([_lecture(code="2150057301", schedule_room=raw)])
    assert parsed[0].parse_status == "uncertain"
    assert any("파싱 실패" in w for w in parsed[0].parse_warnings)


def test_parse_lectures_dedup_multiple_removed() -> None:
    """동일 슬롯 3회 → 1개 유지 + '2건 제거' 경고.

    실측 케이스(민주주의와토론 2150107701: 6블록 중 동일 슬롯 중복쌍 2개)처럼
    제거 건수 카운트가 올바른지 검증한다.
    """
    raw = "\n".join(["화 09:00-10:15 (벤처중소기업센터 10309-장의진)"] * 3)
    parsed = parse_lectures(
        [_lecture(code="2150107701", name="민주주의와토론", schedule_room=raw)]
    )
    p = parsed[0]
    assert p.parse_status == "ok"
    assert len(p.slots) == 1
    assert any("동일 슬롯 중복 2건 제거" in w for w in p.parse_warnings)


def test_duplicate_slot_raws_direct() -> None:
    """공개 헬퍼 duplicate_slot_raws — 중복 슬롯의 raw 문자열 목록 반환."""
    a = parse_lectures(
        [_lecture(code="2150057301", schedule_room="화 09:00-10:15 (벤처중소기업센터 10309-장의진)")]
    )[0]
    assert duplicate_slot_raws(a) == []  # 정상 파싱(dedup 후)이면 빈 목록
    a.slots.append(a.slots[0])  # 중복 슬롯 수동 추가 (구버전/수동 데이터 시뮬레이션)
    dups = duplicate_slot_raws(a)
    assert len(dups) == 1
    assert "화 09:00-10:15" in dups[0]


def test_parse_lectures_same_name_different_code_separate() -> None:
    """같은 name 다른 code(학과)는 별개 수업."""
    parsed = parse_lectures(
        [
            _lecture(code="2150164203", name="머신러닝", department="컴퓨터학부"),
            _lecture(code="3161011001", name="머신러닝", department="AI융합학부"),
        ]
    )
    assert len(parsed) == 2


def test_parse_lectures_old_8digit_code_warns() -> None:
    """구 과목코드(8자리)는 subject_key=code[:-2] 의미가 깨질 수 있어 경고(무해)."""
    parsed = parse_lectures([_lecture(code="21501642", schedule_room="")])
    assert parsed[0].subject_key == "215016"
    assert parsed[0].parse_status == "empty"
    assert any("code 길이" in w for w in parsed[0].parse_warnings)


def test_parse_lectures_skips_code_less() -> None:
    parsed = parse_lectures([_lecture(), {"name": "코드없음"}])
    assert len(parsed) == 1


def test_pass_through_llm_inputs() -> None:
    """target/field/professor/division/department가 원본에서 누락 없이 전달."""
    parsed = parse_lectures(
        [
            _lecture(
                code="2150164203",
                name="알고리즘",
                target="컴퓨터학부 2학년 이상",
                field="[4차]",
                professor="박은영",
                division="전공필수",
                department="컴퓨터학부",
                schedule_room="월 10:30-12:00 (베어드홀 01101-김자헌)",
            )
        ]
    )
    p = parsed[0]
    assert p.target == "컴퓨터학부 2학년 이상"
    assert p.field == "[4차]"
    assert p.field_tags == ["[4차]"]  # 단일 줄 field → 1요소 분해
    assert p.professor == "박은영"
    assert p.division == "전공필수"
    assert p.department == "컴퓨터학부"
    # pass-through professor는 강의 필드이고, 슬롯 professor는 schedule_room 출처
    assert p.slots[0].professor == "김자헌"


def test_pass_through_category_sub_category() -> None:
    """category/sub_category가 원본에서 누락 없이 전달 (이수구분 판단용, 치명①)."""
    parsed = parse_lectures(
        [
            _lecture(
                code="2150164203",
                name="알고리즘",
                category="전필-컴퓨터학부",
                sub_category="[4차]",
                schedule_room="월 10:30-12:00 (베어드홀 01101-김자헌)",
            ),
            # category가 원본에 없으면 기본 None (명시적으로 None 전달)
            _lecture(
                code="3161011001",
                name="머신러닝",
                category=None,
                sub_category=None,
                schedule_room="화 13:30-15:00 (베어드홀 01201-박은영)",
            ),
        ]
    )
    by_code = {p.code: p for p in parsed}
    assert by_code["2150164203"].category == "전필-컴퓨터학부"
    assert by_code["2150164203"].sub_category == "[4차]"
    assert by_code["3161011001"].category is None
    assert by_code["3161011001"].sub_category is None


def test_pass_through_missing_fields_default_none() -> None:
    """pass-through 필드가 원본에 없으면 None (기본값)."""
    parsed = parse_lectures(
        [{"code": "2150164203", "name": "알고리즘", "schedule_room": ""}]
    )
    assert parsed[0].target is None
    assert parsed[0].field is None
    assert parsed[0].field_tags == []  # field 없으면 빈 리스트
    assert parsed[0].professor is None
    assert parsed[0].division is None
    assert parsed[0].department is None
    assert parsed[0].category is None
    assert parsed[0].sub_category is None


def test_parse_lectures_multiline_field_tags_for_optional_elective() -> None:
    """교양선택 '전체' 조회: 멀티라인 field → field_tags 로 줄 단위 정규화.

    field raw는 줄바꿈 그대로 보존(LLM fallback)하고, field_tags는 분해된 리스트.
    """
    field = (
        "['23이후]문화·예술\n"
        "['20,'21~'22]의사소통/글로벌,기초역량-한국어의사소통\n"
        "['19]기초역량-한국어의사소통과국제어문\n"
        "['16-'18]기초역량(한국어의사소통-읽기와쓰기)\n"
        "['15이전]창의성과의사소통능력(핵심"
    )
    parsed = parse_lectures(
        [
            _lecture(
                code="9901234567",
                name="한류와대중문화의이해",
                field=field,
                category="교선",
                schedule_room="월 15:00-16:30 (베어드홀 01101-김자헌)",
            )
        ]
    )
    p = parsed[0]
    assert "\n" in p.field  # raw는 줄바꿈 보존
    assert len(p.field_tags) == 5
    assert p.field_tags[0] == "['23이후]문화·예술"


# ── build_subject_groups ───────────────────────────────────────────────


def test_build_subject_groups_index() -> None:
    parsed = parse_lectures(
        [
            _lecture(code="2150164203"),
            _lecture(code="2150164204", name="알고리즘2"),
            _lecture(code="3161011001", name="머신러닝"),
        ]
    )
    groups = build_subject_groups(parsed)
    assert groups == {
        "21501642": ["2150164203", "2150164204"],
        "31610110": ["3161011001"],
    }


# ── has_time_conflict / find_conflicts ─────────────────────────────────


def _two(lec_a: dict, lec_b: dict) -> tuple[ParsedLecture, ParsedLecture]:
    a, b = parse_lectures([lec_a, lec_b])
    return a, b


def test_has_time_conflict_overlap() -> None:
    a, b = _two(
        _lecture(code="2150164203", schedule_room="월 10:30-12:00 (베어드홀 01101-김자헌)"),
        _lecture(code="2150164204", schedule_room="월 11:00-13:00 (베어드홀 01201-박은영)"),
    )
    assert has_time_conflict(a, b) is True


def test_has_time_conflict_different_day() -> None:
    a, b = _two(
        _lecture(code="2150164203", schedule_room="월 10:30-12:00 (베어드홀 01101-김자헌)"),
        _lecture(code="2150164204", schedule_room="화 10:30-12:00 (베어드홀 01201-박은영)"),
    )
    assert has_time_conflict(a, b) is False


def test_has_time_conflict_adjacent_boundary_not_conflict() -> None:
    """인접 경계: 10:15 종료 ↔ 10:30 시작은 충돌 아님."""
    a, b = _two(
        _lecture(code="2150164203", schedule_room="월 09:00-10:15 (베어드홀 01101-김자헌)"),
        _lecture(code="2150164204", schedule_room="월 10:30-12:00 (베어드홀 01201-박은영)"),
    )
    assert has_time_conflict(a, b) is False


def test_has_time_conflict_partial_day_overlap() -> None:
    """요일 교집합 + 구간 겹침 둘 다 필요."""
    a, b = _two(
        _lecture(code="2150164203", schedule_room="월 수 10:30-12:00 (베어드홀 01101-김자헌)"),
        _lecture(code="2150164204", schedule_room="수 11:00-12:00 (베어드홀 01201-박은영)"),
    )
    assert has_time_conflict(a, b) is True


def test_has_time_conflict_uncertain_skipped() -> None:
    a, b = _two(
        _lecture(code="2150164203", schedule_room="온라인"),
        _lecture(code="2150164204", schedule_room="월 10:30-12:00 (베어드홀 01201-박은영)"),
    )
    assert a.parse_status == "uncertain"
    assert has_time_conflict(a, b) is False


def test_has_time_conflict_empty_skipped() -> None:
    a, b = _two(
        _lecture(code="2150164203", schedule_room=""),
        _lecture(code="2150164204", schedule_room="월 10:30-12:00 (베어드홀 01201-박은영)"),
    )
    assert has_time_conflict(a, b) is False


def test_find_conflicts_reports_pair() -> None:
    a, b, c = parse_lectures(
        [
            _lecture(code="2150164203", name="알고리즘", schedule_room="월 10:30-12:00 (베어드홀 01101-김자헌)"),
            _lecture(code="2150164204", name="자료구조", schedule_room="월 11:00-13:00 (베어드홀 01201-박은영)"),
            _lecture(code="3161011001", name="머신러닝", schedule_room="화 13:30-15:00 (베어드홀 01301-김자헌)"),
        ]
    )
    conflicts = find_conflicts([a, b, c])
    assert len(conflicts) == 1
    conflict = conflicts[0]
    assert conflict.code_a == "2150164203"
    assert conflict.code_b == "2150164204"
    assert conflict.days == ["월"]
    assert conflict.start_min == 660  # max(630, 660) → 11:00
    assert conflict.end_min == 720  # min(720, 780) → 12:00
    assert conflict.message


def test_find_conflicts_skips_uncertain() -> None:
    a, b = _two(
        _lecture(code="2150164203", schedule_room="온라인"),
        _lecture(code="2150164204", schedule_room="월 10:30-12:00 (베어드홀 01201-박은영)"),
    )
    assert find_conflicts([a, b]) == []


def test_find_conflicts_multiple_slot_pairs() -> None:
    """한 쌍이 월/화 두 구간에서 겹치면 슬롯쌍마다 Conflict 보고."""
    a, b = _two(
        _lecture(
            code="2150164203",
            schedule_room=(
                "월 10:30-12:00 (베어드홀 01101-김자헌)\n"
                "화 10:30-12:00 (베어드홀 01101-김자헌)"
            ),
        ),
        _lecture(
            code="2150164204",
            schedule_room=(
                "월 11:00-13:00 (베어드홀 01201-박은영)\n"
                "화 11:00-13:00 (베어드홀 01201-박은영)"
            ),
        ),
    )
    conflicts = find_conflicts([a, b])
    assert len(conflicts) == 2
    assert {tuple(c.days) for c in conflicts} == {("월",), ("화",)}


def test_find_conflicts_same_subject_hint() -> None:
    """같은 subject_key(분반 중복 선택)면 메시지에 힌트 추가."""
    a, b = _two(
        _lecture(code="2150164203", schedule_room="월 10:30-12:00 (베어드홀 01101-김자헌)"),
        _lecture(code="2150164204", schedule_room="월 10:30-12:00 (베어드홀 01201-박은영)"),
    )
    assert a.subject_key == b.subject_key == "21501642"
    conflicts = find_conflicts([a, b])
    assert len(conflicts) == 1
    assert "과목 중복" in conflicts[0].message


# ── SPR-101: Case A (대상외수강제한) 스캔 ──────────────────────


def test_find_case_a_lectures_marker_presence() -> None:
    """target에 (대상외수강제한) 포함 여부로 code 목록을 반환."""
    a, b, c = parse_lectures(
        [
            _lecture(code="2150164203", name="알고리즘", target="컴퓨터학부 2학년"),
            _lecture(
                code="2150164205",
                name="이산수학",
                target="컴퓨터학부 2학년(대상외수강제한)",
            ),
            _lecture(
                code="2150164206",
                name="머신러닝",
                target="(대상외수강제한)(대상외수강제한)",
            ),
        ]
    )
    assert find_case_a_lectures([a, b, c]) == ["2150164205", "2150164206"]
    assert find_case_a_lectures([a]) == []
    assert find_case_a_lectures([]) == []


def test_find_case_a_lectures_repeated_marker_dedup() -> None:
    """SPR-90: 같은 문구 2회 반복((대상외수강제한)(대상외수강제한))도 1번만."""
    lecture = parse_lectures(
        [
            _lecture(
                code="2150164205",
                name="이산수학",
                target=f"컴퓨터학부 2학년{CASE_A_MARKER}{CASE_A_MARKER}",
            )
        ]
    )[0]
    assert find_case_a_lectures([lecture]) == ["2150164205"]


# ── SPR-87: 부분 조회 필터 (filter_parsed_lectures) ─────────────


def _parsed_multi_category() -> list[ParsedLecture]:
    """카테고리 다양성 샘플 — 부분 조회 필터 테스트용."""
    return parse_lectures(
        [
            _lecture(code="2150164203", name="전기기초", category="전기-컴퓨터학부"),
            _lecture(code="2150164204", name="전기심화", category="전기-컴퓨터학부"),
            _lecture(code="2150164301", name="전공필수", category="전필-컴퓨터학부"),
            _lecture(code="2150164401", name="교양필수", category="교필"),
            _lecture(code="2150078501", name="비전채플", category="채플"),
            _lecture(code="3161011001", name="교양선택", category="교선"),
            _lecture(code="9999999999", name="카테고리없음", category=None),
        ]
    )


def test_filter_parsed_lectures_no_filter_returns_all() -> None:
    """필터 미지정 → 전체 그대로 (하위 호환)."""
    parsed = _parsed_multi_category()
    assert filter_parsed_lectures(parsed) == parsed
    assert (
        filter_parsed_lectures(
            parsed, codes=None, subject_keys=None, category_prefixes=None
        )
        == parsed
    )


def test_filter_parsed_lectures_codes_exact() -> None:
    """codes → 정확 조회 (목록 순서 유지)."""
    parsed = _parsed_multi_category()
    result = filter_parsed_lectures(parsed, codes=["2150164203", "2150078501"])
    assert [p.code for p in result] == ["2150164203", "2150078501"]


def test_filter_parsed_lectures_subject_keys_all_divisions() -> None:
    """subject_keys → 분반 그룹 전체 (subject_key = code[:-2])."""
    parsed = _parsed_multi_category()
    result = filter_parsed_lectures(parsed, subject_keys=["21501642"])
    assert [p.code for p in result] == ["2150164203", "2150164204"]


def test_filter_parsed_lectures_category_prefixes() -> None:
    """category_prefixes → prefix 시작 일치 (전기- → 전기-컴퓨터학부)."""
    parsed = _parsed_multi_category()
    result = filter_parsed_lectures(parsed, category_prefixes=["전기-", "채플"])
    codes = [p.code for p in result]
    assert "2150164203" in codes
    assert "2150164204" in codes
    assert "2150078501" in codes
    # prefix 불일치 — 전필-/교필/교선은 걸러진다
    assert "2150164301" not in codes
    assert "2150164401" not in codes
    assert "3161011001" not in codes


def test_filter_parsed_lectures_union_of_conditions() -> None:
    """여러 조건 함께 전달 → 합집합(OR)."""
    parsed = _parsed_multi_category()
    result = filter_parsed_lectures(
        parsed,
        codes=["3161011001"],
        subject_keys=["21500785"],
        category_prefixes=["전필-"],
    )
    assert {p.code for p in result} == {
        "3161011001",
        "2150078501",
        "2150164301",
    }


def test_filter_parsed_lectures_category_none_excluded() -> None:
    """category가 None인 강의는 prefix 매칭에서 제외 (startsWith 오류 방지)."""
    parsed = _parsed_multi_category()
    result = filter_parsed_lectures(parsed, category_prefixes=["교"])
    codes = [p.code for p in result]
    assert "9999999999" not in codes
    assert "2150164401" in codes  # "교필" → prefix "교" 일치
    assert "3161011001" in codes  # "교선" → prefix "교" 일치


def test_filter_parsed_lectures_gyojik_jeongong_excluded() -> None:
    """교직전공- 은 필수 prefix(전기-/전필-/교필/채플)에 걸리지 않는다 (회귀 방지).

    composer 2번 단계가 `category_prefixes=["전기-", "전필-", "교필", "채플"]`로
    필수+채플을 조회할 때 `교직전공-<학부명>` 강의가 섞여 나오면 안 된다.
    """
    parsed = parse_lectures(
        [
            _lecture(code="2150164203", name="전기기초", category="전기-컴퓨터학부"),
            _lecture(code="2150999001", name="교직전공", category="교직전공-컴퓨터학부"),
            _lecture(code="2150078501", name="비전채플", category="채플"),
        ]
    )
    result = filter_parsed_lectures(
        parsed, category_prefixes=["전기-", "전필-", "교필", "채플"]
    )
    codes = [p.code for p in result]
    assert "2150164203" in codes
    assert "2150078501" in codes
    assert "2150999001" not in codes  # 교직전공- 은 제외


def test_filter_parsed_lectures_empty_strings_ignored() -> None:
    """빈 문자열 필터값은 유효하지 않은 입력으로 무시 — 전체 반환 (하위 호환)."""
    parsed = _parsed_multi_category()
    result = filter_parsed_lectures(
        parsed, codes=[""], subject_keys=[""], category_prefixes=[""]
    )
    assert result == parsed


# ── SPR-99: 교선 field_tags 학번(entered_year) 필터 ──────────────────


_GYOSEON_FIELD_ALL = (
    "['23이후]문화·예술\n"
    "['20,'21~'22]의사소통/글로벌\n"
    "['19]기초역량-한국어의사소통\n"
    "['16-'18]기초역량\n"
    "['15이전]창의성과의사소통능력"
)


def _parsed_gyoseon() -> list[ParsedLecture]:
    """교선 학번 필터 테스트용 — 전 학번 태그 + 신학번 전용 + 구학번 전용 + 전공."""
    return parse_lectures(
        [
            _lecture(
                code="3161011001",
                name="한류와대중문화",
                category="교선",
                field=_GYOSEON_FIELD_ALL,
            ),
            _lecture(
                code="3161011002",
                name="신학번전용",
                category="교선",
                field="['23이후]과학·기술",
            ),
            _lecture(
                code="3161011003",
                name="구학번전용",
                category="교선",
                field="['16-'18]기초역량\n['15이전]창의",
            ),
            _lecture(code="2150164203", name="전공", category="전기-컴퓨터학부"),
        ]
    )


def test_field_tags_for_entered_year_ranges() -> None:
    """태그 범위 해석 — 이후/이전/대역/단일/쉼표/컬리 인용부호/비학번 줄."""
    assert field_tags_for_entered_year(["['23이후]문화·예술"], 2023) == [
        "['23이후]문화·예술"
    ]
    assert field_tags_for_entered_year(["['23이후]문화·예술"], 2022) == []
    # 쉼표 다중 태그 — 하나라도 매칭하면 해당 줄 매칭
    assert field_tags_for_entered_year(["['20,'21~'22]의사소통"], 2020) == [
        "['20,'21~'22]의사소통"
    ]
    assert field_tags_for_entered_year(["['20,'21~'22]의사소통"], 2021) == [
        "['20,'21~'22]의사소통"
    ]
    assert field_tags_for_entered_year(["['20,'21~'22]의사소통"], 2019) == []
    assert field_tags_for_entered_year(["['19]기초역량"], 2019) == ["['19]기초역량"]
    assert field_tags_for_entered_year(["['19]기초역량"], 2020) == []
    # 대역(- / ~)
    assert field_tags_for_entered_year(["['16-'18]기초역량"], 2017) == [
        "['16-'18]기초역량"
    ]
    assert field_tags_for_entered_year(["['16-'18]기초역량"], 2019) == []
    assert field_tags_for_entered_year(["['15이전]창의"], 2014) == ["['15이전]창의"]
    assert field_tags_for_entered_year(["['15이전]창의"], 2016) == []
    # 컬리 인용부호 (USAINT 원본 첫 줄 — ‘ ) → ASCII 정규화로 매칭
    assert field_tags_for_entered_year(["[‘23이후]문화·예술"], 2023) == [
        "[‘23이후]문화·예술"
    ]
    # 학번 태그 없는 줄 — 매칭 아님
    assert field_tags_for_entered_year(["기독교과목", "[4차]"], 2023) == []


def test_filter_parsed_by_entered_year_trims_and_drops() -> None:
    """학번 매칭 강의 유지 + field_tags를 매칭 줄만으로, 비매칭은 제외.

    전 학번 태그 강의는 2023에 매칭되므로 유지, 구학번 전용은 2023에 안 열려
    제외. 학번 태그가 없는 전공([4차])은 판정 불가라 보수적으로 유지.
    """
    parsed = _parsed_gyoseon()
    out = filter_parsed_by_entered_year(parsed, 2023)
    codes = [p.code for p in out]
    assert "3161011001" in codes  # 전 학번 태그 → 2023 매칭
    assert "3161011002" in codes  # 신학번 전용 → 2023 매칭
    assert "3161011003" not in codes  # 구학번 전용 → 2023 비매칭 제외
    assert "2150164203" in codes  # [4차] → 학번 태그 없음, 보수 유지
    # field_tags는 매칭 줄만으로 정리 (컬리 원본은 그대로)
    g1 = next(p for p in out if p.code == "3161011001")
    assert g1.field_tags == ["['23이후]문화·예술"]
    # 학번 태그 없는 강의는 원본 field_tags 그대로 유지
    major = next(p for p in out if p.code == "2150164203")
    assert major.field_tags == ["[4차]"]


def test_filter_parsed_by_entered_year_older_year() -> None:
    """구학번 사용자(2017) — '16-'18 대역 줄 매칭, 신학번 전용은 제외."""
    parsed = _parsed_gyoseon()
    out = filter_parsed_by_entered_year(parsed, 2017)
    codes = [p.code for p in out]
    assert "3161011001" in codes
    assert "3161011002" not in codes  # 신학번 전용 → 2017 비매칭 제외
    assert "3161011003" in codes
    g3 = next(p for p in out if p.code == "3161011003")
    assert g3.field_tags == ["['16-'18]기초역량"]


def test_filter_parsed_by_entered_year_pure() -> None:
    """입력 parsed를 변경하지 않는다 (model_copy로 새 리스트 생성)."""
    parsed = _parsed_gyoseon()
    before = [p.field_tags[:] for p in parsed]
    filter_parsed_by_entered_year(parsed, 2023)
    assert [p.field_tags for p in parsed] == before


# ── server 도구: parse_lectures_cache / check_timetable_conflicts ──────


def _save_cache(isolated_root: Path, cached_at: datetime | None = None) -> None:
    cache = LecturesCache(
        year=2026,
        semester="1",
        groups={
            "major_primary": LectureGroupEntry(
                category_type="major",
                params={"collage": "IT대학", "department": "컴퓨터학부"},
                lectures=[
                    _lecture(
                        code="2150164203",
                        schedule_room="월 10:30-12:00 (베어드홀 01101-김자헌)",
                    ),
                    _lecture(
                        code="2150164204",
                        name="자료구조",
                        schedule_room="",
                    ),
                    _lecture(
                        code="9999999999",
                        name="파싱불가",
                        schedule_room="온라인 강의",
                    ),
                ],
                count=3,
                error=None,
            )
        },
        cached_at=cached_at or datetime.now(timezone.utc),
    )
    cache_mod.save_lectures_cache(cache)


@pytest.mark.asyncio
async def test_parse_lectures_cache_miss(isolated_root: Path) -> None:
    result = await server.parse_lectures_cache(2026, "1")
    assert result["_cache"]["source"] == "miss"
    assert result["parsed"] == []
    assert result["subject_groups"] == {}
    assert result["stats"]["total"] == 0
    assert "guidance" in result


@pytest.mark.asyncio
async def test_parse_lectures_cache_hit(isolated_root: Path) -> None:
    _save_cache(isolated_root)
    result = await server.parse_lectures_cache(2026, "1")
    assert result["_cache"]["source"] == "cache"
    assert result["stats"] == {"total": 3, "parsed_ok": 1, "uncertain": 1, "empty": 1}

    by_code = {p["code"]: p for p in result["parsed"]}
    assert by_code["2150164203"]["subject_key"] == "21501642"
    assert by_code["2150164203"]["target"] == "컴퓨터학부 2학년"
    assert by_code["2150164203"]["field_tags"] == ["[4차]"]  # 직렬화까지 보존
    assert by_code["2150164204"]["parse_status"] == "empty"
    assert by_code["9999999999"]["parse_status"] == "uncertain"
    assert by_code["9999999999"]["raw"] == "온라인 강의"

    assert result["subject_groups"]["21501642"] == ["2150164203", "2150164204"]
    assert result["subject_groups"]["99999999"] == ["9999999999"]


@pytest.mark.asyncio
async def test_parse_lectures_cache_stale_returns_data(isolated_root: Path) -> None:
    """stale도 데이터 반환 + source만 표시 (load_lectures_cache 관례 통일)."""
    _save_cache(
        isolated_root,
        cached_at=datetime.now(timezone.utc)
        - timedelta(days=cache_mod.CACHE_TTL_DAYS + 1),
    )
    result = await server.parse_lectures_cache(2026, "1")
    assert result["_cache"]["source"] == "stale"
    assert result["_cache"]["age_days"] >= cache_mod.CACHE_TTL_DAYS
    assert result["stats"]["total"] == 3
    assert len(result["parsed"]) == 3
    assert "guidance" in result
    assert "7일" in result["guidance"]


@pytest.mark.asyncio
async def test_parse_lectures_cache_miss_guidance_fill_cache(
    isolated_root: Path,
) -> None:
    """miss guidance는 캐시 채우기 안내 — find_lectures 자동 저장(SPR-75) 방식."""
    result = await server.parse_lectures_cache(2026, "1")
    assert result["_cache"]["source"] == "miss"
    assert "find_lectures" in result["guidance"]
    assert "save_lectures_cache" not in result["guidance"]


@pytest.mark.asyncio
async def test_check_timetable_conflicts_over_30_raises() -> None:
    """31개 입력 → ValueError (hard guard: O(N²)/전수 비교 방지)."""
    raw = [_lecture(code=f"21501642{i:02d}") for i in range(31)]
    lectures = [p.model_dump(mode="json") for p in parse_lectures(raw)]
    with pytest.raises(ValueError):
        await server.check_timetable_conflicts(lectures)


@pytest.mark.asyncio
async def test_check_timetable_conflicts_reports() -> None:
    a, b, c = parse_lectures(
        [
            _lecture(code="2150164203", schedule_room="월 10:30-12:00 (베어드홀 01101-김자헌)"),
            _lecture(code="2150164204", schedule_room="월 11:00-13:00 (베어드홀 01201-박은영)"),
            _lecture(code="3161011001", schedule_room="온라인"),
        ]
    )
    result = await server.check_timetable_conflicts(
        [p.model_dump(mode="json") for p in [a, b, c]]
    )
    assert result["has_blocking_conflict"] is True
    assert len(result["conflicts"]) == 1
    assert result["warnings"] and "불확정 강의 1개" in result["warnings"][0]


@pytest.mark.asyncio
async def test_check_timetable_conflicts_no_conflict() -> None:
    a, b = parse_lectures(
        [
            _lecture(code="2150164203", schedule_room="월 10:30-12:00 (베어드홀 01101-김자헌)"),
            _lecture(code="2150164204", schedule_room="화 10:30-12:00 (베어드홀 01201-박은영)"),
        ]
    )
    result = await server.check_timetable_conflicts(
        [p.model_dump(mode="json") for p in [a, b]]
    )
    assert result["has_blocking_conflict"] is False
    assert result["conflicts"] == []
    assert result["warnings"] == []


# ── SPR-101: Case A (대상외수강제한) warnings 표면화 ──────────────


@pytest.mark.asyncio
async def test_check_timetable_conflicts_case_a_warning() -> None:
    """target에 (대상외수강제한)이 있으면 warnings에 대상외 수강신청 목록 포함."""
    a, b = parse_lectures(
        [
            _lecture(code="2150164203", schedule_room="월 10:30-12:00 (베어드홀 01101-김자헌)"),
            _lecture(
                code="2150164205",
                name="이산수학",
                schedule_room="화 10:30-12:00 (베어드홀 01201-박은영)",
                target="컴퓨터학부 2학년(대상외수강제한)",
            ),
        ]
    )
    result = await server.check_timetable_conflicts(
        [p.model_dump(mode="json") for p in [a, b]]
    )
    case_a = [w for w in result["warnings"] if "대상외 수강신청" in w]
    assert len(case_a) == 1
    assert "이산수학" in case_a[0]
    assert "2150164205" in case_a[0]
    # Case A가 아닌 강의는 목록에 없어야 한다
    assert "2150164203" not in case_a[0]


@pytest.mark.asyncio
async def test_check_timetable_conflicts_no_case_a_marker() -> None:
    """target에 마커가 없으면 Case A warning 없음."""
    a, b = parse_lectures(
        [
            _lecture(code="2150164203", schedule_room="월 10:30-12:00 (베어드홀 01101-김자헌)"),
            _lecture(code="2150164204", schedule_room="화 10:30-12:00 (베어드홀 01201-박은영)"),
        ]
    )
    result = await server.check_timetable_conflicts(
        [p.model_dump(mode="json") for p in [a, b]]
    )
    assert not any("대상외 수강신청" in w for w in result["warnings"])


@pytest.mark.asyncio
async def test_check_timetable_conflicts_case_a_repeated_marker_dedup() -> None:
    """SPR-90: (대상외수강제한) 2회 반복 표기도 과목당 1번만 경고."""
    a, b = parse_lectures(
        [
            _lecture(code="2150164203", schedule_room="월 10:30-12:00 (베어드홀 01101-김자헌)"),
            _lecture(
                code="2150164205",
                name="이산수학",
                schedule_room="화 10:30-12:00 (베어드홀 01201-박은영)",
                target="컴퓨터학부 2학년(대상외수강제한)(대상외수강제한)",
            ),
        ]
    )
    result = await server.check_timetable_conflicts(
        [p.model_dump(mode="json") for p in [a, b]]
    )
    case_a = [w for w in result["warnings"] if "대상외 수강신청" in w]
    assert len(case_a) == 1
    assert case_a[0].count("이산수학") == 1


@pytest.mark.asyncio
async def test_check_timetable_conflicts_minimal_dict_target_preserved() -> None:
    """최소 dict도 target을 주면 lax 경로(coerce)에서 보존돼 Case A 경고 동작.

    code+slots만 넘기면 pass-through 필드는 유실되지만(도구 docstring 명시),
    target은 SPR-101 스캔을 위해 lax 경로에서도 보존한다.
    """
    result = await server.check_timetable_conflicts(
        [
            {
                "code": "2150164203",
                "name": "이산수학",
                "slots": [{"days": ["월"], "start_min": 630, "end_min": 720}],
                "target": "컴퓨터학부 2학년(대상외수강제한)",
            }
        ]
    )
    case_a = [w for w in result["warnings"] if "대상외 수강신청" in w]
    assert len(case_a) == 1
    assert "이산수학" in case_a[0]


# ── SPR-83: 최소 필드만 넘겨도 동작 (스키마 완화) ──────────────


def test_coerce_conflict_lecture_minimal_defaults() -> None:
    """최소 필드 dict → 선택 필드 기본값 채워 ParsedLecture 생성."""
    lecture = coerce_conflict_lecture(
        {
            "code": "2150164203",
            "name": "알고리즘",
            "credits": 3.0,
            "slots": [{"days": ["월"], "start_min": 630, "end_min": 720}],
            "parse_status": "ok",
        }
    )
    assert lecture.code == "2150164203"
    assert lecture.subject_key == "21501642"  # code[:-2] 기본값
    assert lecture.parse_warnings == []
    assert lecture.raw is None
    assert len(lecture.slots) == 1
    slot = lecture.slots[0]
    assert slot.room is None
    assert slot.professor is None
    assert slot.raw == "월 10:30-12:00"  # days/start/end로 재구성


def test_coerce_conflict_lecture_full_dict_unchanged() -> None:
    """parse_lectures_cache 전체 parsed dict는 엄격 검증 통과 (하위 호환)."""
    (lecture,) = parse_lectures(
        [
            _lecture(
                code="2150164203",
                schedule_room="월 10:30-12:00 (베어드홀 01101-김자헌)",
            )
        ]
    )
    coerced = coerce_conflict_lecture(lecture.model_dump(mode="json"))
    assert coerced == lecture


def test_coerce_conflict_lecture_broken_input_raises() -> None:
    """누락/타입 오류 → 명확한 예외 (조용한 '충돌 없음' 오판 방지)."""
    with pytest.raises(ValueError, match="code"):
        coerce_conflict_lecture({"name": "알고리즘"})
    with pytest.raises(ValueError, match="slots"):
        coerce_conflict_lecture({"code": "2150164203"})
    with pytest.raises(TypeError, match="slots"):
        coerce_conflict_lecture({"code": "2150164203", "slots": "월 10:30"})


@pytest.mark.asyncio
async def test_check_timetable_conflicts_minimal_dicts() -> None:
    """최소 필드(code/name/credits/slots/parse_status)만 넘겨도 충돌 검출."""
    result = await server.check_timetable_conflicts(
        [
            {
                "code": "2150164203",
                "name": "알고리즘",
                "credits": 3.0,
                "slots": [{"days": ["월"], "start_min": 630, "end_min": 720}],
                "parse_status": "ok",
            },
            {
                "code": "2150164204",
                "name": "자료구조",
                "credits": 3.0,
                "slots": [{"days": ["월"], "start_min": 660, "end_min": 780}],
                "parse_status": "ok",
            },
        ]
    )
    assert result["has_blocking_conflict"] is True
    assert len(result["conflicts"]) == 1
    conflict = result["conflicts"][0]
    assert conflict["days"] == ["월"]
    assert conflict["start_min"] == 660
    assert conflict["end_min"] == 720
    assert conflict["slot_a_raw"] == "월 10:30-12:00"  # raw 자동 재구성
    assert "10:30" in conflict["message"]
    assert result["warnings"] == []


@pytest.mark.asyncio
async def test_check_timetable_conflicts_minimal_no_conflict() -> None:
    """최소 필드 dict — 다른 요일이면 충돌 없음."""
    result = await server.check_timetable_conflicts(
        [
            {
                "code": "2150164203",
                "slots": [{"days": ["월"], "start_min": 630, "end_min": 720}],
            },
            {
                "code": "2150164204",
                "slots": [{"days": ["화"], "start_min": 630, "end_min": 720}],
            },
        ]
    )
    assert result["has_blocking_conflict"] is False
    assert result["conflicts"] == []
    assert result["warnings"] == []


@pytest.mark.asyncio
async def test_check_timetable_conflicts_explicit_subject_key_avoids_dup_hint() -> None:
    """마지막 2자리만 다른 별도 과목 — subject_key 명시 시 '과목 중복' 오판 회피.

    최소 필드만 넘기면 subject_key=code[:-2]로 파생돼 분반 중복 힌트가 붙지만
    (SPR-86 경계), 실제 subject_key를 명시하면 단순 시간 충돌로만 처리된다.
    """
    result = await server.check_timetable_conflicts(
        [
            {
                "code": "2150164203",
                "name": "알고리즘",
                "subject_key": "21501642",
                "slots": [{"days": ["월"], "start_min": 630, "end_min": 720}],
                "parse_status": "ok",
            },
            {
                "code": "2150164204",
                "name": "자료구조",
                "subject_key": "21501643",  # 분반이 아닌 별도 과목 — 다른 subject_key
                "slots": [{"days": ["월"], "start_min": 660, "end_min": 780}],
                "parse_status": "ok",
            },
        ]
    )
    assert result["has_blocking_conflict"] is True
    conflict = result["conflicts"][0]
    assert "과목 중복" not in conflict["message"]


# ── SPR-83 critic 수정: 빈 slots / 잘못된 parse_status / 오버라이드 / 혼합 ──


def test_coerce_conflict_lecture_empty_slots_is_empty() -> None:
    """slots=[] + parse_status 미지정 → 'empty' (조용한 '충돌 없음' 오판 방지)."""
    lecture = coerce_conflict_lecture({"code": "2150164203", "slots": []})
    assert lecture.parse_status == "empty"
    assert lecture.slots == []


def test_coerce_conflict_lecture_invalid_parse_status_raises() -> None:
    """parse_status에 유효하지 않은 값 → ValueError 재포장 (pydantic 노출 방지)."""
    with pytest.raises(ValueError, match="parse_status"):
        coerce_conflict_lecture(
            {
                "code": "2150164203",
                "parse_status": "yes",
                "slots": [{"days": ["월"], "start_min": 630, "end_min": 720}],
            }
        )


def test_coerce_conflict_lecture_explicit_subject_key() -> None:
    """subject_key 명시 시 code[:-2] 대신 명시값 유지."""
    lecture = coerce_conflict_lecture(
        {
            "code": "2150164203",
            "subject_key": "custom-key",
            "slots": [{"days": ["월"], "start_min": 630, "end_min": 720}],
        }
    )
    assert lecture.subject_key == "custom-key"


@pytest.mark.asyncio
async def test_check_timetable_conflicts_empty_slots_warns() -> None:
    """slots=[] 강의는 parse_status='empty'로 처리 → warnings에 보고."""
    result = await server.check_timetable_conflicts(
        [
            {"code": "2150164203", "slots": []},
            {
                "code": "2150164204",
                "slots": [{"days": ["월"], "start_min": 630, "end_min": 720}],
            },
        ]
    )
    assert result["has_blocking_conflict"] is False
    assert result["conflicts"] == []
    assert result["warnings"] and "불확정 강의 1개" in result["warnings"][0]
    assert "2150164203" in result["warnings"][0]


@pytest.mark.asyncio
async def test_check_timetable_conflicts_mixed_minimal_and_full() -> None:
    """최소 dict + 전체 parsed dict 혼합 리스트도 동작 (완화 하위 호환)."""
    full = parse_lectures(
        [
            _lecture(
                code="3161011001",
                schedule_room="화 09:30-11:00 (한경직기념관 101-홍길동)",
            )
        ]
    )[0]
    result = await server.check_timetable_conflicts(
        [
            full.model_dump(mode="json"),
            {
                "code": "2150164203",
                "name": "알고리즘",
                "slots": [{"days": ["화"], "start_min": 570, "end_min": 660}],
            },
        ]
    )
    assert result["has_blocking_conflict"] is True
    assert len(result["conflicts"]) == 1
    assert result["warnings"] == []


@pytest.mark.asyncio
async def test_check_timetable_conflicts_duplicate_slot_warning() -> None:
    """수동/구버전 데이터가 강의 내 중복 슬롯을 갖고 오면 warning으로 보고 (SPR-82).

    파싱 dedup 후 정상 흐름에선 발생하지 않는 방어 가드 — 중복 슬롯을 가진
    dict를 직접 만들면 동일 강의 내 중복이 warnings에 담겨야 한다.
    """
    a, b = parse_lectures(
        [
            _lecture(code="2150057301", name="AI시대의정보보안", schedule_room="화 09:00-10:15 (벤처중소기업센터 10309-장의진)"),
            _lecture(code="2150164204", name="자료구조", schedule_room="화 10:30-12:00 (베어드홀 01201-박은영)"),
        ]
    )
    # 파싱된 dict에 동일 슬롯을 수동으로 하나 더 추가 (구버전/수동 데이터 시뮬레이션)
    a_dict = a.model_dump(mode="json")
    a_dict["slots"].append(dict(a_dict["slots"][0]))

    result = await server.check_timetable_conflicts([a_dict, b.model_dump(mode="json")])
    assert result["has_blocking_conflict"] is False
    assert result["conflicts"] == []
    assert any("동일 슬롯 중복 1건" in w and "2150057301" in w for w in result["warnings"])
