"""시간표 시각화 렌더러 테스트 (SPR-53).

skills/soongpt-timetable-visualize/render_timetable.py 를 importlib로 로드해
파싱 / HTML 생성 / 충돌 강조 / 내장 파서 폴백 / CLI 를 검증한다. render_timetable
모듈은 pytest.ini의 pythonpath(src) 때문에 soongpt_mcp를 import 할 수 있어
기본적으로 라이브러리 재사용 경로를 탄다. 폴백 경로는 lib 함수를 None으로
monkeypatch해서 검증한다.

SPR-117 추가: 터미널 텍스트 격자(render_text)와 졸업사정표 반영 표
(compute_graduation_rows/render_graduation_table) 및 --format text CLI 검증.
"""
from __future__ import annotations

import importlib.util
import json
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
    """기본 09:00-18:00 보장 + 수업이 벗어나면 확장 (SPR-60)."""
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
    assert start == 9 * 60  # 10:30보다 이른 기본 09:00 보장
    assert end == 23 * 60  # 22:15 → 다음 시간 경계, 18:00보다 늦어 확장


def test_time_window_guarantees_default_range(renderer) -> None:
    """수업이 기본 범위 안에만 있어도 09:00-18:00 그리드 보장 (SPR-60)."""
    blocks, _ = renderer.build_blocks(
        [_lecture(schedule_room="월 10:30-12:00 (베어드홀 01101-김자헌)")]
    )
    assert renderer._time_window(blocks) == (9 * 60, 18 * 60)


def test_time_window_default_range_at_exact_boundary(renderer) -> None:
    """수업이 정확히 기본 범위 경계(09:00-18:00)여도 흔들리지 않는다 (SPR-60).

    올림/내림 경계(정확히 시간 단위)에서 base 보장값과 만나 회귀할 수 있는
    지점을 검증한다.
    """
    blocks, _ = renderer.build_blocks(
        [_lecture(schedule_room="월 09:00-18:00 (베어드홀 01101-김자헌)")]
    )
    assert renderer._time_window(blocks) == (9 * 60, 18 * 60)


def test_time_window_empty_default(renderer) -> None:
    assert renderer._time_window([]) == (9 * 60, 18 * 60)


def test_render_days_always_shows_weekdays_with_weekend(renderer) -> None:
    """월~금은 항상 표시, 토는 수업이 있을 때만 추가 (SPR-60)."""
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
    assert renderer._render_days(blocks) == ["월", "화", "수", "목", "금", "토"]


def test_render_days_weekday_only_no_weekend(renderer) -> None:
    """주중 수업만 있어도 월~금 전체 표시 (SPR-60)."""
    blocks, _ = renderer.build_blocks(
        [_lecture(schedule_room="수 10:30-12:00 (베어드홀 01101-김자헌)")]
    )
    assert renderer._render_days(blocks) == ["월", "화", "수", "목", "금"]


def test_render_days_includes_sunday_when_present(renderer) -> None:
    """일요일 수업이 있으면 월~금 다음 일이 추가된다 (SPR-60)."""
    blocks, _ = renderer.build_blocks(
        [_lecture(schedule_room="일 10:30-12:00 (베어드홀 01101-김자헌)")]
    )
    assert renderer._render_days(blocks) == ["월", "화", "수", "목", "금", "일"]


# ── render_html (HTML 생성 + 충돌 강조) ─────────────────────────────────


def test_render_html_contains_grid(renderer) -> None:
    blocks, warnings = renderer.build_blocks([_lecture()])
    html_str = renderer.render_html(blocks, warnings)
    assert "<html" in html_str
    assert ">월<" in html_str
    assert "알고리즘" in html_str
    assert "slot" in html_str
    assert "시간 충돌 없음" in html_str


def test_render_html_shows_empty_weekday_columns(renderer) -> None:
    """수업이 없는 요일도 컬럼이 항상 렌더링되어야 한다 (SPR-60)."""
    blocks, _ = renderer.build_blocks(
        [
            _lecture(schedule_room="월 10:30-12:00 (베어드홀 01101-김자헌)"),
            _lecture(
                code="2150164204",
                name="자료구조",
                schedule_room="수 10:30-12:00 (숭덕 02108-박은영)",
            ),
        ]
    )
    html_str = renderer.render_html(blocks, [])
    # 월~금 5개 요일 헤더가 모두 있어야 함 (수업 없는 화/목도 포함)
    for day in ("월", "화", "수", "목", "금"):
        assert f">{day}<" in html_str
    # 수업이 없는 요일 컬럼은 empty-note 마커로 표시
    assert "empty-note" in html_str


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


# ── 출력 경로 결정 (SPR-80: cwd 오염 방지) ─────────────────────────────


def test_default_output_dir_prefers_plugin_data(renderer, monkeypatch) -> None:
    """$CLAUDE_PLUGIN_DATA를 1순위로 사용 (프로젝트 캐시 관례 일치)."""
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", "/fake/plugin-data")
    assert renderer.default_output_dir() == Path("/fake/plugin-data")


def test_default_output_dir_prefers_xdg(renderer, monkeypatch) -> None:
    """$XDG_CACHE_HOME 설정 시 그 아래 soongpt 하위를 반환."""
    monkeypatch.delenv("CLAUDE_PLUGIN_DATA", raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", "/fake/xdg")
    assert renderer.default_output_dir() == Path("/fake/xdg/soongpt")


def test_default_output_dir_falls_back_to_home_cache(renderer, monkeypatch) -> None:
    """CLAUDE_PLUGIN_DATA/XDG 미설정 시 ~/.cache/soongpt를 반환."""
    monkeypatch.delenv("CLAUDE_PLUGIN_DATA", raising=False)
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.setattr(renderer.Path, "home", lambda: Path("/fake/home"))
    assert renderer.default_output_dir() == Path("/fake/home/.cache/soongpt")


def test_resolve_out_path_explicit_out(renderer, tmp_path: Path) -> None:
    """--out 명시 시 그 경로를 그대로 사용 (하위 호환)."""
    assert renderer.resolve_out_path(str(tmp_path / "a.html")) == tmp_path / "a.html"


def test_resolve_out_path_default_uses_cache(
    renderer, tmp_path: Path, monkeypatch
) -> None:
    """--out 미지정 시 캐시 디렉토리로 결정하고 디렉토리를 생성."""
    monkeypatch.delenv("CLAUDE_PLUGIN_DATA", raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    out_path = renderer.resolve_out_path(None)
    assert out_path == tmp_path / "cache" / "soongpt" / "timetable.html"
    assert out_path.parent.exists()


def test_resolve_out_path_falls_back_to_tempdir(
    renderer, tmp_path: Path, monkeypatch
) -> None:
    """캐시 디렉토리 생성 불가 환경은 OS 임시 디렉토리로 폴백 (cwd 아님)."""
    (tmp_path / "tmp").mkdir()
    monkeypatch.setattr(
        renderer.Path, "mkdir", lambda *a, **k: (_ for _ in ()).throw(OSError("ro"))
    )
    monkeypatch.setattr(renderer.tempfile, "gettempdir", lambda: str(tmp_path / "tmp"))
    monkeypatch.delenv("CLAUDE_PLUGIN_DATA", raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    assert renderer.resolve_out_path(None) == tmp_path / "tmp" / "timetable.html"


def test_main_default_output_not_in_cwd(
    renderer, tmp_path: Path, monkeypatch
) -> None:
    """--out 미지정 실행 시 cwd가 아닌 캐시 디렉토리에 생성 (SPR-80)."""
    input_path = tmp_path / "candidate.json"
    input_path.write_text(
        '[{"code": "2150164203", "name": "알고리즘", '
        '"schedule_room": "월 10:30-12:00 (베어드홀 01101-김자헌)"}]',
        encoding="utf-8",
    )
    monkeypatch.delenv("CLAUDE_PLUGIN_DATA", raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.chdir(tmp_path)  # cwd = tmp_path — 여기에 생성되면 실패
    rc = renderer.main([str(input_path)])
    assert rc == 0
    out_path = tmp_path / "cache" / "soongpt" / "timetable.html"
    assert out_path.exists()
    assert "알고리즘" in out_path.read_text(encoding="utf-8")
    assert not (tmp_path / "timetable.html").exists()


# ── 터미널 텍스트 격자 (SPR-117) ────────────────────────────────────────


def test_display_width_korean_two_cols(renderer) -> None:
    """한글은 2칸, ASCII는 1칸으로 계산 (터미널 정렬 기준)."""
    assert renderer._display_width("알고리즘") == 8
    assert renderer._display_width("ABC123") == 6
    assert renderer._display_width("월") == 2
    assert renderer._display_width("10:30") == 5
    assert renderer._display_width("이수 후") == 7


def test_truncate_display_reserves_ellipsis(renderer) -> None:
    """초과 시 마지막 1칸을 '…'로 예약해 자른다 (한글 2칸 고려)."""
    assert renderer._truncate_display("알고리즘", 8) == "알고리즘"
    # 6칸 상한: 내용에 5칸(끝 1칸은 '…' 예약)만 쓸 수 있어 "알고" + "…"
    assert renderer._truncate_display("알고리즘", 6) == "알고…"
    assert renderer._truncate_display("algorithm", 5) == "algo…"
    assert renderer._truncate_display("짧음", 10) == "짧음"


def test_render_text_grid_structure(renderer) -> None:
    """헤더(후보명/시간/요일) + 강의명 + 수업 목록 포함."""
    blocks, warnings = renderer.build_blocks(
        [_lecture(schedule_room="월 수 10:30-12:00 (베어드홀 01101-김자헌)")]
    )
    text = renderer.render_text(blocks, warnings, title="안 A — 3학점")
    assert text.startswith("안 A — 3학점")
    assert "시간" in text
    for day in ("월", "화", "수", "목", "금"):
        assert day in text
    assert "10:30" in text
    assert "알고리즘" in text
    assert "수업 목록" in text
    assert "시간 충돌 없음" in text


def test_render_text_alignment_uniform(renderer) -> None:
    """모든 그리드 라인의 표시 폭이 동일해야 정렬이 맞는다 (SPR-117)."""
    blocks, _ = renderer.build_blocks(
        [
            _lecture(schedule_room="월 10:30-12:00 (베어드홀 01101-김자헌)"),
            _lecture(
                code="2150164204",
                name="자료구조",
                schedule_room="화 09:00-10:30 (숭덕 02108-박은영)",
            ),
        ]
    )
    text = renderer.render_text(blocks, [])
    widths = {
        renderer._display_width(line)
        for line in text.splitlines()
        if "|" in line and not line.startswith("-")
    }
    assert len(widths) == 1


def test_render_text_marks_conflict(renderer) -> None:
    """충돌 강의는 격자·수업 목록에서 ⚠ 로 강조, 충돌 목록에 쌍 표기."""
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
    text = renderer.render_text(blocks, [])
    assert "⚠ 알고리즘" in text
    assert "⚠ 자료구조" in text
    assert "시간 충돌 1건" in text
    assert "알고리즘 (2150164203) ↔ 자료구조 (2150164204)" in text


def test_render_text_truncates_long_name_in_grid_only(renderer) -> None:
    """격자 셀은 잘리고, 수업 목록에는 전체 이름이 남는다."""
    long_name = "이주제는매우긴과목명입니다매우매우"
    blocks, _ = renderer.build_blocks(
        [_lecture(name=long_name, schedule_room="월 10:30-12:00 (베어드홀 01101-김자헌)")]
    )
    text = renderer.render_text(blocks, [])
    assert "…" in text
    assert long_name in text  # 수업 목록의 전체 이름


def test_render_text_weekend_column(renderer) -> None:
    """토요일 수업이 있으면 격자에 토 열이 추가된다 (SPR-60)."""
    blocks, _ = renderer.build_blocks(
        [_lecture(schedule_room="토 09:00-10:15 (운동장-김자헌)")]
    )
    assert "토" in renderer.render_text(blocks, [])


def test_render_text_empty(renderer) -> None:
    text = renderer.render_text([], [])
    assert "표시할 강의가 없습니다" in text
    assert "월" in text


def test_render_text_warnings_section(renderer) -> None:
    text = renderer.render_text([], ["파싱 실패: 2150164203"])
    assert "파싱 경고" in text
    assert "파싱 실패: 2150164203" in text


# ── 졸업사정표 반영 표 (SPR-117) ────────────────────────────────────────


def _grad_summary(**overrides: object) -> dict:
    """졸업사정표 요약 dict (get_graduation_status.graduationSummary 형태)."""
    base = {
        "generalRequired": {"required": 15, "completed": 12, "satisfied": False},
        "majorFoundation": {"required": 6, "completed": 6, "satisfied": True},
        "majorRequired": {"required": 24, "completed": 18, "satisfied": False},
        "chapel": {"satisfied": False},
    }
    base.update(overrides)
    return base


def test_lecture_credits_extraction(renderer) -> None:
    """credits 필드 우선, 없으면 time_points 앞 숫자 (채플 0.5 포함)."""
    assert renderer._lecture_credits({"credits": 4.0}) == 4.0
    assert renderer._lecture_credits({"time_points": "3/3"}) == 3.0
    assert renderer._lecture_credits({"time_points": "0.5/0.5"}) == 0.5
    assert renderer._lecture_credits({}) == 0.0


def test_compute_graduation_rows_after_and_remaining(renderer) -> None:
    """'이수 후' = 현재 + 카테고리별 학점 합산, 잔여 = 목표 - 이수 후."""
    lectures = [
        _lecture(category="전필-컴퓨터학부", time_points="3/3"),
        _lecture(code="2150164204", category="전필-컴퓨터학부", time_points="2/2"),
        _lecture(code="3161011001", category="교필", time_points="3/3"),
        _lecture(code="9990030001", category="채플", time_points="0.5/0.5"),
    ]
    rows = renderer.compute_graduation_rows(lectures, _grad_summary())
    by_label = {r["label"]: r for r in rows}
    # 전공필수: 18 + 3 + 2 = 23, 목표 24, 잔여 1
    assert by_label["전공필수"]["after"] == 23
    assert by_label["전공필수"]["remaining"] == 1
    assert by_label["전공필수"]["satisfied"] is False
    # 교양필수: 12 + 3 = 15, 잔여 0 → 충족
    assert by_label["교양필수"]["after"] == 15
    assert by_label["교양필수"]["remaining"] == 0
    assert by_label["교양필수"]["satisfied"] is True
    # 전공기초: 이미 충족(6/6), 후보 없음 → 그대로 유지
    assert by_label["전공기초"]["after"] == 6
    assert by_label["전공기초"]["satisfied"] is True
    # 채플: 학점 없음(수치 None), 후보에 채플 포함 → 충족
    assert by_label["채플"]["current"] is None
    assert by_label["채플"]["satisfied"] is True


def test_compute_graduation_rows_clamps_negative_remaining(renderer) -> None:
    """목표 초과 시 잔여는 0으로 고정되고 충족 처리된다."""
    lectures = [_lecture(category="전필-컴퓨터학부", time_points="9/9")]
    rows = renderer.compute_graduation_rows(lectures, _grad_summary())
    by_label = {r["label"]: r for r in rows}
    assert by_label["전공필수"]["after"] == 27
    assert by_label["전공필수"]["remaining"] == 0
    assert by_label["전공필수"]["satisfied"] is True


def test_compute_graduation_rows_unknown_category_ignored(renderer) -> None:
    """교직 등 졸업 요약 항목에 없는 이수구분은 어떤 행에도 합산되지 않는다."""
    lectures = [_lecture(category="교직", time_points="3/3")]
    rows = renderer.compute_graduation_rows(lectures, _grad_summary())
    by_label = {r["label"]: r for r in rows}
    assert by_label["전공필수"]["after"] == 18
    assert by_label["교양필수"]["after"] == 12


def test_compute_graduation_rows_only_tracked_items(renderer) -> None:
    """graduationSummary에 없는 항목은 행에 나타나지 않는다."""
    summary = {"generalRequired": {"required": 15, "completed": 12, "satisfied": False}}
    rows = renderer.compute_graduation_rows([], summary)
    labels = [r["label"] for r in rows]
    assert labels == ["교양필수"]


def test_render_graduation_table_format(renderer) -> None:
    """표 구조 + 잔여 0(충족) 행에 ✅ 표시."""
    rows = renderer.compute_graduation_rows(
        [_lecture(category="전필-컴퓨터학부", time_points="3/3")],
        _grad_summary(),
    )
    table = renderer.render_graduation_table(rows)
    for header in ("카테고리", "현재", "이수 후", "목표", "잔여"):
        assert header in table
    assert "전공필수" in table
    assert "교양필수" in table
    assert "✅" in table  # 교양필수 잔여 0 → ✅


def test_render_graduation_table_alignment(renderer) -> None:
    """한글 헤더('이수 후')를 포함해 모든 행의 표시 폭이 동일하다."""
    rows = renderer.compute_graduation_rows([], _grad_summary())
    table = renderer.render_graduation_table(rows)
    widths = {
        renderer._display_width(line)
        for line in table.splitlines()
        if not line.startswith("-")
    }
    assert len(widths) == 1


def test_render_graduation_table_empty(renderer) -> None:
    assert renderer.render_graduation_table([]) == "졸업 요건 데이터 없음"


# ── 졸업 입력 로딩 (--graduation) ──────────────────────────────────────


def test_load_graduation_summary_wrapper(renderer, tmp_path: Path) -> None:
    """get_graduation_status 반환 전체에서 graduationSummary 키를 추출."""
    path = tmp_path / "g.json"
    path.write_text(
        json.dumps({"graduationSummary": {"chapel": {"satisfied": True}}}),
        encoding="utf-8",
    )
    assert renderer._load_graduation_summary(str(path)) == {
        "chapel": {"satisfied": True}
    }


def test_load_graduation_summary_bare(renderer, tmp_path: Path) -> None:
    """graduationSummary dict 그 자체도 허용."""
    path = tmp_path / "g.json"
    path.write_text(
        json.dumps({"majorRequired": {"required": 1, "completed": 0}}),
        encoding="utf-8",
    )
    assert renderer._load_graduation_summary(str(path)) == {
        "majorRequired": {"required": 1, "completed": 0}
    }


def test_load_graduation_summary_none(renderer) -> None:
    assert renderer._load_graduation_summary(None) is None


def test_load_graduation_summary_invalid_type(renderer, tmp_path: Path) -> None:
    path = tmp_path / "g.json"
    path.write_text("[1,2,3]", encoding="utf-8")
    with pytest.raises(TypeError):
        renderer._load_graduation_summary(str(path))


# ── CLI text 모드 (SPR-117) ─────────────────────────────────────────────


def test_main_text_prints_grid_to_stdout(renderer, tmp_path: Path, capsys) -> None:
    """--format text는 stdout으로 격자를 출력하고 파일을 만들지 않는다."""
    input_path = tmp_path / "candidate.json"
    input_path.write_text(
        '[{"code": "2150164203", "name": "알고리즘", '
        '"schedule_room": "월 10:30-12:00 (베어드홀 01101-김자헌)"}]',
        encoding="utf-8",
    )
    rc = renderer.main([str(input_path), "--format", "text"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "알고리즘" in captured.out
    assert "시간" in captured.out


def test_main_text_writes_out_when_requested(renderer, tmp_path: Path) -> None:
    input_path = tmp_path / "candidate.json"
    input_path.write_text("[]", encoding="utf-8")
    out_path = tmp_path / "out.txt"
    rc = renderer.main(
        [str(input_path), "--format", "text", "--out", str(out_path)]
    )
    assert rc == 0
    assert out_path.exists()
    assert "표시할 강의가 없습니다" in out_path.read_text(encoding="utf-8")


def test_main_text_with_graduation(renderer, tmp_path: Path, capsys) -> None:
    """--graduation 지정 시 격자 아래에 졸업 표가 붙는다."""
    input_path = tmp_path / "candidate.json"
    input_path.write_text(
        '[{"code": "2150164203", "name": "알고리즘", "category": "전필-컴퓨터학부", '
        '"time_points": "3/3", '
        '"schedule_room": "월 10:30-12:00 (베어드홀 01101-김자헌)"}]',
        encoding="utf-8",
    )
    grad_path = tmp_path / "grad.json"
    grad_path.write_text(
        json.dumps(
            {
                "graduationSummary": {
                    "majorRequired": {
                        "required": 24,
                        "completed": 18,
                        "satisfied": False,
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    rc = renderer.main(
        [str(input_path), "--format", "text", "--graduation", str(grad_path)]
    )
    captured = capsys.readouterr()
    assert rc == 0
    assert "전공필수" in captured.out
    assert "이수 후" in captured.out
    assert "카테고리" in captured.out


def test_main_text_missing_graduation_returns_1(renderer, tmp_path: Path) -> None:
    input_path = tmp_path / "candidate.json"
    input_path.write_text("[]", encoding="utf-8")
    rc = renderer.main(
        [
            str(input_path),
            "--format",
            "text",
            "--graduation",
            str(tmp_path / "nope.json"),
        ]
    )
    assert rc == 1


def test_main_text_ignores_open(renderer, tmp_path: Path, monkeypatch) -> None:
    """text 모드에서 --open은 무시된다 (브라우저 미오픈)."""
    input_path = tmp_path / "candidate.json"
    input_path.write_text("[]", encoding="utf-8")
    opened: list[str] = []
    monkeypatch.setattr(renderer.webbrowser, "open", lambda uri: opened.append(uri))
    rc = renderer.main([str(input_path), "--format", "text", "--open"])
    assert rc == 0
    assert opened == []


def test_main_html_explicit_still_default(renderer, tmp_path: Path) -> None:
    """--format html 명시 시 기존 HTML 동작 그대로 (회귀 가드)."""
    input_path = tmp_path / "candidate.json"
    input_path.write_text(
        '[{"code": "2150164203", "name": "알고리즘", '
        '"schedule_room": "월 10:30-12:00 (베어드홀 01101-김자헌)"}]',
        encoding="utf-8",
    )
    out_path = tmp_path / "out.html"
    rc = renderer.main(
        [str(input_path), "--format", "html", "--out", str(out_path)]
    )
    assert rc == 0
    assert out_path.exists()
    assert "<html" in out_path.read_text(encoding="utf-8")
