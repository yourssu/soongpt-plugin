"""시간표 시각화 렌더러 테스트 (SPR-53).

skills/soongpt-timetable-visualize/render_timetable.py 를 importlib로 로드해
파싱 / HTML 생성 / 충돌 강조 / 내장 파서 폴백 / CLI 를 검증한다. render_timetable
모듈은 pytest.ini의 pythonpath(src) 때문에 soongpt_mcp를 import 할 수 있어
기본적으로 라이브러리 재사용 경로를 탄다. 폴백 경로는 lib 함수를 None으로
monkeypatch해서 검증한다.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SKILL_DIR = Path(__file__).resolve().parents[1] / "skills" / "soongpt-timetable-visualize"
RENDERER_PATH = SKILL_DIR / "render_timetable.py"


@pytest.fixture(scope="module")
def renderer():
    """render_timetable.py를 임포트해 모듈 객체 반환.

    dataclass 데코레이터가 sys.modules를 조회하므로 임포트 전에 등록해야 한다.
    """
    spec = importlib.util.spec_from_file_location(
        "soongpt_render_timetable", RENDERER_PATH
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _lecture(**overrides: object) -> dict:
    """find_lectures 반환 형태의 강의 dict."""
    base = {
        "code": "2150164203",
        "name": "알고리즘",
        "professor": "박은영",
        "department": "컴퓨터학부",
        "time_points": "3/3",
        "schedule_room": "월 10:30-12:00 (베어드홀 01101-김자헌)",
    }
    base.update(overrides)
    return base


# ── build_blocks (파싱 + 충돌 표시) ─────────────────────────────────────


def test_build_blocks_parses_slots(renderer) -> None:
    blocks, warnings = renderer.build_blocks([_lecture()])
    assert warnings == []
    assert len(blocks) == 1
    block = blocks[0]
    assert block.code == "2150164203"
    assert block.name == "알고리즘"
    assert block.day == "월"
    assert block.start_min == 630
    assert block.end_min == 720
    assert block.room == "베어드홀 01101"
    # 슬롯 교수(schedule_room 출처)가 강의 필드 professor보다 우선
    assert block.professor == "김자헌"
    assert block.is_conflict is False


def test_build_blocks_multi_day_expands(renderer) -> None:
    """'월 수 10:30-12:00' → 월/수 2개 블록."""
    blocks, _ = renderer.build_blocks(
        [_lecture(schedule_room="월 수 10:30-12:00 (베어드홀 01101-김자헌)")]
    )
    assert len(blocks) == 2
    assert {b.day for b in blocks} == {"월", "수"}


def test_build_blocks_skips_empty_schedule_room(renderer) -> None:
    blocks, warnings = renderer.build_blocks([_lecture(schedule_room="")])
    assert blocks == []
    assert warnings == []


def test_build_blocks_uncertain_warns(renderer) -> None:
    blocks, warnings = renderer.build_blocks([_lecture(schedule_room="온라인 강의")])
    assert blocks == []
    assert any("파싱 실패" in w for w in warnings)


def test_build_blocks_marks_conflict(renderer) -> None:
    """겹치는 시간대 강의 → 두 블록 모두 is_conflict."""
    blocks, _ = renderer.build_blocks(
        [
            _lecture(schedule_room="월 10:30-12:00 (베어드홀 01101-김자헌)"),
            _lecture(
                code="2150164204",
                name="자료구조",
                schedule_room="월 11:00-13:00 (숭덕 02108-박은영)",
            ),
        ]
    )
    by_code = {b.code: b for b in blocks}
    assert by_code["2150164203"].is_conflict is True
    assert by_code["2150164204"].is_conflict is True


def test_build_blocks_conflict_only_overlap_day(renderer) -> None:
    """강의 A가 월/화에 걸치고 B와 겹치는 건 월뿐이면 월 블록만 충돌."""
    blocks, _ = renderer.build_blocks(
        [
            _lecture(
                schedule_room=(
                    "월 10:30-12:00 (베어드홀 01101-김자헌)\n"
                    "화 10:30-12:00 (베어드홀 01101-김자헌)"
                )
            ),
            _lecture(
                code="2150164204",
                name="자료구조",
                schedule_room="월 11:00-13:00 (숭덕 02108-박은영)",
            ),
        ]
    )
    by_code_day = {(b.code, b.day): b for b in blocks}
    assert by_code_day[("2150164203", "월")].is_conflict is True
    assert by_code_day[("2150164203", "화")].is_conflict is False


def test_build_blocks_adjacent_boundary_not_conflict(renderer) -> None:
    """인접 경계(10:15 종료 ↔ 10:30 시작)는 충돌 아님."""
    blocks, _ = renderer.build_blocks(
        [
            _lecture(schedule_room="월 09:00-10:15 (베어드홀 01101-김자헌)"),
            _lecture(
                code="2150164204",
                name="자료구조",
                schedule_room="월 10:30-12:00 (숭덕 02108-박은영)",
            ),
        ]
    )
    assert all(not b.is_conflict for b in blocks)


def test_build_blocks_different_day_not_conflict(renderer) -> None:
    blocks, _ = renderer.build_blocks(
        [
            _lecture(schedule_room="월 10:30-12:00 (베어드홀 01101-김자헌)"),
            _lecture(
                code="2150164204",
                name="자료구조",
                schedule_room="화 10:30-12:00 (숭덕 02108-박은영)",
            ),
        ]
    )
    assert all(not b.is_conflict for b in blocks)


# ── 내장 파서 폴백 (soongpt_mcp 미설치 환경) ────────────────────────────


@pytest.fixture
def fallback_renderer(renderer, monkeypatch):
    """lib 파서/충돌 검사를 비활성화해 내장 파서 폴백 경로를 강제."""
    monkeypatch.setattr(renderer, "_lib_parse_lectures", None)
    monkeypatch.setattr(renderer, "_lib_find_conflicts", None)
    return renderer


def test_fallback_parses_slots(fallback_renderer) -> None:
    blocks, warnings = fallback_renderer.build_blocks([_lecture()])
    assert warnings == []
    assert len(blocks) == 1
    assert blocks[0].day == "월"
    assert blocks[0].start_min == 630
    assert blocks[0].room == "베어드홀 01101"
    assert blocks[0].professor == "김자헌"


def test_fallback_detects_conflict(fallback_renderer) -> None:
    blocks, _ = fallback_renderer.build_blocks(
        [
            _lecture(schedule_room="월 10:30-12:00 (베어드홀 01101-김자헌)"),
            _lecture(
                code="2150164204",
                name="자료구조",
                schedule_room="월 11:00-13:00 (숭덕 02108-박은영)",
            ),
        ]
    )
    assert all(b.is_conflict for b in blocks)


def test_fallback_uncertain_warns(fallback_renderer) -> None:
    blocks, warnings = fallback_renderer.build_blocks([_lecture(schedule_room="온라인")])
    assert blocks == []
    assert any("파싱 실패" in w for w in warnings)


def test_fallback_dedup_by_code(fallback_renderer) -> None:
    """동일 code 중복 입력 → 최초 항목만 유지, 자기 자신과의 충돌 없음 (lib과 일치)."""
    blocks, warnings = fallback_renderer.build_blocks(
        [
            _lecture(schedule_room="월 10:30-12:00 (베어드홀 01101-김자헌)"),
            _lecture(
                name="알고리즘(중복)",
                schedule_room="월 10:30-12:00 (베어드홀 01101-김자헌)",
            ),
        ]
    )
    assert len(blocks) == 1
    assert blocks[0].name == "알고리즘"
    assert blocks[0].is_conflict is False
    assert warnings == []


# ── 레이아웃 / 시간축 헬퍼 ──────────────────────────────────────────────


def test_layout_day_side_by_side(renderer) -> None:
    """겹치는 블록은 서로 다른 열에 배치 (나란히 안 가림)."""
    blocks, _ = renderer.build_blocks(
        [
            _lecture(schedule_room="월 10:30-12:00 (베어드홀 01101-김자헌)"),
            _lecture(
                code="2150164204",
                name="자료구조",
                schedule_room="월 11:00-13:00 (숭덕 02108-박은영)",
            ),
        ]
    )
    layout = renderer._layout_day(blocks)
    cols = [c for c, _ in layout]
    assert len(set(cols)) == 2  # 서로 다른 열
    assert max(cols) + 1 == 2  # 최대 열수 2


def test_layout_day_three_way_overlap(renderer) -> None:
    """3개가 모두 겹치면 각각 다른 열에 배치 (최대 열수 3)."""
    blocks, _ = renderer.build_blocks(
        [
            _lecture(schedule_room="월 10:30-12:00 (베어드홀 01101-김자헌)"),
            _lecture(
                code="2150164204",
                name="자료구조",
                schedule_room="월 11:00-12:30 (숭덕 02108-박은영)",
            ),
            _lecture(
                code="3161011001",
                name="머신러닝",
                schedule_room="월 11:30-13:00 (베어드홀 01201-김자헌)",
            ),
        ]
    )
    layout = renderer._layout_day(blocks)
    cols = [c for c, _ in layout]
    assert len(set(cols)) == 3  # 모두 서로 다른 열
    assert max(cols) + 1 == 3  # 최대 열수 3


def test_time_window_rounds_to_hour(renderer) -> None:
    blocks, _ = renderer.build_blocks(
        [
            _lecture(schedule_room="월 10:30-12:00 (베어드홀 01101-김자헌)"),
            _lecture(
                code="3161011001",
                name="머신러닝",
                schedule_room="화 21:30-22:15 (베어드홀 01201-김자헌)",
            ),
        ]
    )
    start, end = renderer._time_window(blocks)
    assert start == 10 * 60
    assert end == 23 * 60  # 22:15 → 다음 시간 경계


def test_time_window_empty_default(renderer) -> None:
    assert renderer._time_window([]) == (9 * 60, 18 * 60)


def test_render_days_includes_weekend_only_when_present(renderer) -> None:
    blocks, _ = renderer.build_blocks(
        [
            _lecture(schedule_room="월 10:30-12:00 (베어드홀 01101-김자헌)"),
            _lecture(
                code="3161011001",
                name="체육",
                schedule_room="토 09:00-10:15 (운동장-김자헌)",
            ),
        ]
    )
    assert renderer._render_days(blocks) == ["월", "토"]


# ── render_html (HTML 생성 + 충돌 강조) ─────────────────────────────────


def test_render_html_contains_grid(renderer) -> None:
    blocks, warnings = renderer.build_blocks([_lecture()])
    html_str = renderer.render_html(blocks, warnings)
    assert "<html" in html_str
    assert ">월<" in html_str
    assert "알고리즘" in html_str
    assert "slot" in html_str
    assert "시간 충돌 없음" in html_str


def test_render_html_marks_conflict(renderer) -> None:
    blocks, _ = renderer.build_blocks(
        [
            _lecture(schedule_room="월 10:30-12:00 (베어드홀 01101-김자헌)"),
            _lecture(
                code="2150164204",
                name="자료구조",
                schedule_room="월 11:00-13:00 (숭덕 02108-박은영)",
            ),
        ]
    )
    html_str = renderer.render_html(blocks, [])
    assert 'class="slot conflict"' in html_str
    assert "conflict-badge" in html_str
    assert "시간 충돌" in html_str
    assert "알고리즘" in html_str and "자료구조" in html_str
    # 요약의 <strong> 태그가 이스케이프되어 리터럴로 노출되지 않아야 한다 (회귀).
    assert "<strong>시간 충돌" in html_str
    assert "&lt;strong&gt;" not in html_str


def test_render_html_escapes_user_data(renderer) -> None:
    """과목명/교수명 등 외부 데이터는 HTML 이스케이프."""
    # 슬롯에 교수가 없으면(괄호 끝이 하이픈) lecture 레벨 professor가 표시된다.
    blocks, _ = renderer.build_blocks(
        [
            _lecture(
                name="<script>alert(1)</script>",
                professor="<b>교수</b>",
                schedule_room="월 10:30-12:00 (베어드홀 01101-)",
            )
        ]
    )
    html_str = renderer.render_html(blocks, [])
    assert "<script>alert(1)</script>" not in html_str
    assert "&lt;script&gt;" in html_str
    assert "<b>교수</b>" not in html_str
    assert "&lt;b&gt;교수&lt;/b&gt;" in html_str


def test_render_html_empty(renderer) -> None:
    html_str = renderer.render_html([], [])
    assert "표시할 강의가 없습니다" in html_str
    assert ">월<" in html_str


def test_render_html_warnings_section(renderer) -> None:
    html_str = renderer.render_html([], ["파싱 실패: 2150164203"])
    assert "파싱 경고" in html_str
    assert "파싱 실패: 2150164203" in html_str


# ── 입력 로딩 ───────────────────────────────────────────────────────────


def test_load_lectures_array(renderer, tmp_path: Path) -> None:
    path = tmp_path / "in.json"
    path.write_text('[{"code": "2150164203"}]', encoding="utf-8")
    assert renderer.load_lectures(path) == [{"code": "2150164203"}]


def test_load_lectures_wrapper(renderer, tmp_path: Path) -> None:
    path = tmp_path / "in.json"
    path.write_text('{"lectures": [{"code": "2150164203"}]}', encoding="utf-8")
    assert renderer.load_lectures(path) == [{"code": "2150164203"}]


def test_load_lectures_wrapper_missing_key(renderer, tmp_path: Path) -> None:
    path = tmp_path / "in.json"
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(TypeError):
        renderer.load_lectures(path)


def test_load_lectures_non_list(renderer, tmp_path: Path) -> None:
    path = tmp_path / "in.json"
    path.write_text('"not-a-list"', encoding="utf-8")
    with pytest.raises(TypeError):
        renderer.load_lectures(path)


# ── CLI (main) ──────────────────────────────────────────────────────────


def test_main_writes_html(renderer, tmp_path: Path) -> None:
    input_path = tmp_path / "candidate.json"
    input_path.write_text('[{"code": "2150164203", "name": "알고리즘", "schedule_room": "월 10:30-12:00 (베어드홀 01101-김자헌)"}]', encoding="utf-8")
    out_path = tmp_path / "out.html"
    rc = renderer.main([str(input_path), "--out", str(out_path)])
    assert rc == 0
    assert out_path.exists()
    assert "알고리즘" in out_path.read_text(encoding="utf-8")


def test_main_open_calls_browser(renderer, tmp_path: Path, monkeypatch) -> None:
    input_path = tmp_path / "candidate.json"
    input_path.write_text("[]", encoding="utf-8")
    out_path = tmp_path / "out.html"
    opened: list[str] = []
    monkeypatch.setattr(renderer.webbrowser, "open", lambda uri: opened.append(uri))
    rc = renderer.main([str(input_path), "--out", str(out_path), "--open"])
    assert rc == 0
    assert len(opened) == 1
    assert opened[0].startswith("file://")


def test_main_missing_file_returns_1(renderer, tmp_path: Path) -> None:
    rc = renderer.main([str(tmp_path / "nope.json")])
    assert rc == 1


def test_main_invalid_json_returns_1(renderer, tmp_path: Path) -> None:
    input_path = tmp_path / "bad.json"
    input_path.write_text("not json", encoding="utf-8")
    rc = renderer.main([str(input_path)])
    assert rc == 1
