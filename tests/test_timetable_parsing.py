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
    ParsedLecture,
    build_subject_groups,
    extract_credits,
    find_conflicts,
    has_time_conflict,
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


def test_parse_failed_block_skipped() -> None:
    """포맷 불일치 블록은 슬롯으로 만들지 않는다 (uncertain은 호출자 판정)."""
    slots = parse_schedule_room("온라인 강의")
    assert slots == []


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


def test_parse_lectures_dedup_by_code() -> None:
    """동일 code 중복 수집 → 최초 항목 유지."""
    parsed = parse_lectures(
        [_lecture(name="알고리즘"), _lecture(name="알고리즘(중복)")]
    )
    assert len(parsed) == 1
    assert parsed[0].name == "알고리즘"


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
    assert p.professor == "박은영"
    assert p.division == "전공필수"
    assert p.department == "컴퓨터학부"
    # pass-through professor는 강의 필드이고, 슬롯 professor는 schedule_room 출처
    assert p.slots[0].professor == "김자헌"


def test_pass_through_missing_fields_default_none() -> None:
    """pass-through 필드가 원본에 없으면 None (기본값)."""
    parsed = parse_lectures(
        [{"code": "2150164203", "name": "알고리즘", "schedule_room": ""}]
    )
    assert parsed[0].target is None
    assert parsed[0].field is None
    assert parsed[0].professor is None
    assert parsed[0].division is None
    assert parsed[0].department is None


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
    assert by_code["2150164204"]["parse_status"] == "empty"
    assert by_code["9999999999"]["parse_status"] == "uncertain"
    assert by_code["9999999999"]["raw"] == "온라인 강의"

    assert result["subject_groups"]["21501642"] == ["2150164203", "2150164204"]
    assert result["subject_groups"]["99999999"] == ["9999999999"]


@pytest.mark.asyncio
async def test_parse_lectures_cache_stale(isolated_root: Path) -> None:
    _save_cache(
        isolated_root,
        cached_at=datetime.now(timezone.utc)
        - timedelta(days=cache_mod.CACHE_TTL_DAYS + 1),
    )
    result = await server.parse_lectures_cache(2026, "1")
    assert result["_cache"]["source"] == "stale"
    assert result["parsed"] == []
    assert "guidance" in result


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
    assert result["warnings"] and "불확정 슬롯 1개" in result["warnings"][0]


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
