"""시간표 시각화 렌더러 — 강의 dict 목록 JSON → 정적 HTML.

표준 라이브러리만 사용하는 스탠드얼론 스크립트. 어떤 python3에서든 실행 가능
(플러그인 venv 경로를 몰라도 됨). soongpt_mcp.timetable_parsing이 import
가능하면 재사용하고(시간 파싱 + 충돌 검사), 미설치 환경은 동일 스펙의 내장
파서로 폴백한다.

사용법:
    python3 render_timetable.py timetable.json [--out out.html] [--open]

입력 JSON (find_lectures 반환과 동일한 강의 dict 목록):
    [
      {
        "code": "2150164203",
        "name": "알고리즘",
        "professor": "박은영",
        "department": "컴퓨터학부",
        "time_points": "3/3",
        "schedule_room": "월 수 10:30-12:00 (베어드홀 01101-김자헌)\\n..."
      },
      ...
    ]
    최상위가 dict면 "lectures" 키의 목록을 사용한다 (선택).

렌더링: 요일×시간 그리드. 겹치는 강의는 시간축에 나란히 배치하고, 충돌
강의는 빨간 테두리로 시각적 강조. JS 없이 Python이 셀 위치를 미리 계산.
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# ── soongpt_mcp.timetable_parsing 재사용 시도 (실패 시 내장 파서 폴백) ──
try:
    from soongpt_mcp.timetable_parsing import (
        find_conflicts as _lib_find_conflicts,
    )
    from soongpt_mcp.timetable_parsing import (
        parse_lectures as _lib_parse_lectures,
    )
except Exception:  # noqa: BLE001 — 실행 환경(플러그인 venv 설치 여부)에 따라 달라짐
    _lib_parse_lectures = None
    _lib_find_conflicts = None

_WEEKDAY_ORDER = ("월", "화", "수", "목", "금", "토", "일")
_BASE_DAYS = ("월", "화", "수", "목", "금")
_MINUTE_SCALE = 1  # 1분 = 1px (1시간 = 60px) — 그리드라인/슬롯 배치에 사용

# 포맷: 요일(들)[공백]HH:MM-HH:MM (강의실-교수) — timetable_parsing과 동일 스펙.
_SCHEDULE_ROOM_RE = re.compile(
    r"(?P<days>(?:[월화수목금토일]\s*)+)\s*"
    r"(?P<start>\d{1,2}:\d{2})\s*-\s*(?P<end>\d{1,2}:\d{2})\s*"
    r"\((?P<content>.+)\)$"
)


def _to_minutes(hhmm: str) -> int:
    """'HH:MM' → 자정 이후 분 (예: '10:30' → 630)."""
    hours, minutes = hhmm.split(":")
    return int(hours) * 60 + int(minutes)


def _format_minutes(minutes: int) -> str:
    """분 → 'HH:MM' (예: 630 → '10:30')."""
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


@dataclass
class Block:
    """그리드 배치용 수업 블록 — 강의 1개의 특정 요일/시간 발생 1건."""

    code: str
    name: str | None
    professor: str | None
    room: str | None
    day: str
    start_min: int
    end_min: int
    is_conflict: bool = False


def load_lectures(input_path: Path) -> list[dict]:
    """입력 JSON 로드. 배열 또는 {'lectures': [...]} 래퍼를 지원."""
    data = json.loads(input_path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        lectures = data.get("lectures")
        if not isinstance(lectures, list):
            raise TypeError(
                "'lectures' 키가 리스트여야 합니다 (입력: 최상위 배열 또는 "
                "{'lectures': [...]})"
            )
        return lectures
    if not isinstance(data, list):
        raise TypeError("입력은 강의 dict 배열이어야 합니다")
    return data


def _fallback_parse_lectures(lectures: list[dict]) -> list[dict]:
    """내장 파서 — soongpt_mcp 미설치 환경용 최소 구현.

    timetable_parsing.parse_lectures와 동일 스펙: `\n`으로 연결된
    `요일(들) HH:MM-HH:MM (강의실-교수)` 블록을 정규식 1개로 파싱. 실패
    블록/비정상 구간은 건너뛰고 warnings로 보고 (uncertain 판정은 호출자).
    """
    result: list[dict] = []
    for raw in lectures:
        code = raw.get("code")
        if not code:
            continue
        code = str(code)
        schedule_room = raw.get("schedule_room") or ""
        raw_blocks = [b.strip() for b in schedule_room.split("\n") if b.strip()]

        slots: list[dict[str, Any]] = []
        for block in raw_blocks:
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
                {
                    "days": days,
                    "start_min": start_min,
                    "end_min": end_min,
                    "room": room,
                    "professor": professor,
                }
            )

        warnings: list[str] = []
        if raw_blocks and len(slots) != len(raw_blocks):
            warnings.append(
                f"schedule_room 파싱 실패 줄 존재 (raw {len(raw_blocks)}블록 중 "
                f"{len(slots)}블록만 파싱됨): {code}"
            )
        result.append(
            {
                "code": code,
                "name": raw.get("name"),
                "professor": raw.get("professor"),
                "department": raw.get("department"),
                "slots": slots,
                "warnings": warnings,
            }
        )
    return result


def _fallback_find_conflicts(parsed: list[dict]) -> list[dict]:
    """내장 충돌 검사 — 같은 요일 + 분 단위 구간 겹침인 강의쌍 탐색."""
    conflicts: list[dict] = []
    for i in range(len(parsed)):
        for j in range(i + 1, len(parsed)):
            a, b = parsed[i], parsed[j]
            for slot_a in a["slots"]:
                for slot_b in b["slots"]:
                    overlap_days = sorted(
                        set(slot_a["days"]) & set(slot_b["days"]),
                        key=_WEEKDAY_ORDER.index,
                    )
                    if not overlap_days:
                        continue
                    if (
                        slot_a["start_min"] < slot_b["end_min"]
                        and slot_b["start_min"] < slot_a["end_min"]
                    ):
                        conflicts.append(
                            {"code_a": a["code"], "code_b": b["code"], "days": overlap_days}
                        )
    return conflicts


def build_blocks(lectures: list[dict]) -> tuple[list[Block], list[str]]:
    """강의 dict 목록 → 그리드 배치용 Block 목록 + 파싱 경고.

    soongpt_mcp.timetable_parsing을 우선 재사용하고, 미설치 환경은 내장
    파서로 폴백. 충돌 강의는 (code, 요일) 단위로 is_conflict=True 표시.
    """
    warnings: list[str] = []
    conflict_keys: set[tuple[str, str]] = set()  # (code, day)

    if _lib_parse_lectures is not None:
        parsed = _lib_parse_lectures(lectures)
        if _lib_find_conflicts is not None:
            for conflict in _lib_find_conflicts(parsed):
                for day in conflict.days:
                    conflict_keys.add((conflict.code_a, day))
                    conflict_keys.add((conflict.code_b, day))
        parsed_norm = [
            {
                "code": p.code,
                "name": p.name,
                "professor": p.professor,
                "department": p.department,
                "slots": [
                    {
                        "days": slot.days,
                        "start_min": slot.start_min,
                        "end_min": slot.end_min,
                        "room": slot.room,
                        "professor": slot.professor,
                    }
                    for slot in p.slots
                ],
                "warnings": p.parse_warnings,
            }
            for p in parsed
        ]
        for p in parsed:
            for warning in p.parse_warnings:
                warnings.append(f"[{p.code}] {warning}")
    else:
        parsed_norm = _fallback_parse_lectures(lectures)
        for conflict in _fallback_find_conflicts(parsed_norm):
            for day in conflict["days"]:
                conflict_keys.add((conflict["code_a"], day))
                conflict_keys.add((conflict["code_b"], day))
        for p in parsed_norm:
            warnings.extend(p["warnings"])

    blocks: list[Block] = []
    for p in parsed_norm:
        for slot in p["slots"]:
            for day in slot["days"]:
                blocks.append(
                    Block(
                        code=p["code"],
                        name=p["name"],
                        professor=slot["professor"] or p["professor"],
                        room=slot["room"],
                        day=day,
                        start_min=slot["start_min"],
                        end_min=slot["end_min"],
                        is_conflict=(p["code"], day) in conflict_keys,
                    )
                )
    return blocks, warnings


def _overlap_pairs(blocks: list[Block]) -> list[tuple[Block, Block]]:
    """같은 요일 + 구간 겹침인 블록쌍 목록 (충돌 요약 출력용)."""
    by_day: dict[str, list[Block]] = {}
    for block in blocks:
        by_day.setdefault(block.day, []).append(block)
    pairs: list[tuple[Block, Block]] = []
    for day_blocks in by_day.values():
        for i in range(len(day_blocks)):
            for j in range(i + 1, len(day_blocks)):
                a, b = day_blocks[i], day_blocks[j]
                if a.start_min < b.end_min and b.start_min < a.end_min:
                    pairs.append((a, b))
    return pairs


def _layout_day(blocks: list[Block]) -> list[tuple[int, int]]:
    """한 요일 블록들에 (열 인덱스, 최대 열수) 배치.

    겹치는 블록은 서로 다른 열에 나란히 배치해 가리지 않게 한다
    (인터벌 그래프 그리디 컬러링 = 캘린더 공용 레이아웃).
    """
    ordered = sorted(
        enumerate(blocks), key=lambda ib: (ib[1].start_min, ib[1].end_min)
    )
    col_ends: list[int] = []
    assignment: dict[int, int] = {}
    for idx, block in ordered:
        col = 0
        while col < len(col_ends) and col_ends[col] > block.start_min:
            col += 1
        if col == len(col_ends):
            col_ends.append(block.end_min)
        else:
            col_ends[col] = block.end_min
        assignment[idx] = col
    max_cols = max(len(col_ends), 1)
    return [(assignment[i], max_cols) for i in range(len(blocks))]


def _time_window(blocks: list[Block]) -> tuple[int, int]:
    """그리드 시간축 [시작, 종료) 분. 시간 단위로 반올림, 기본 09:00-18:00."""
    if not blocks:
        return 9 * 60, 18 * 60
    start = min(b.start_min for b in blocks)
    end = max(b.end_min for b in blocks)
    return (start // 60) * 60, ((end + 59) // 60) * 60


def _render_days(blocks: list[Block]) -> list[str]:
    """렌더할 요일 목록 (요일 순서 유지, 토/일은 있을 때만)."""
    present = {b.day for b in blocks}
    days = [d for d in _WEEKDAY_ORDER if d in present]
    return days or list(_BASE_DAYS)


def render_html(
    blocks: list[Block],
    warnings: list[str],
    *,
    title: str = "시간표",
) -> str:
    """Block 목록 → 정적 HTML 문자열 생성 (JS 불필요)."""
    days = _render_days(blocks)
    window_start, window_end = _time_window(blocks)
    total_height = (window_end - window_start) * _MINUTE_SCALE

    by_day: dict[str, list[Block]] = {}
    for block in blocks:
        by_day.setdefault(block.day, []).append(block)

    conflict_blocks = sum(1 for b in blocks if b.is_conflict)
    conflict_pairs = _overlap_pairs(blocks)

    def esc(value: Any) -> str:
        return html.escape(str(value)) if value is not None else ""

    def hour_label_px(hour: int) -> int:
        return (hour * 60 - window_start) * _MINUTE_SCALE

    def block_style(block: Block, col: int, max_cols: int) -> str:
        top = (block.start_min - window_start) * _MINUTE_SCALE
        height = (block.end_min - block.start_min) * _MINUTE_SCALE
        left_pct = col * (100.0 / max_cols)
        width_pct = 100.0 / max_cols
        return (
            f"top:{top}px; left:{left_pct:.2f}%; width:{width_pct:.2f}%; "
            f"height:{height}px;"
        )

    # 시간 축 라벨 (1시간 간격)
    hour_labels = [
        f'<div class="hour-label" style="top:{hour_label_px(hour)}px">'
        f"{hour:02d}:00</div>"
        for hour in range(window_start // 60, window_end // 60 + 1)
    ]

    header_cells = "".join(
        f'<div class="day-header">{esc(day)}</div>' for day in days
    )

    day_columns: list[str] = []
    for day in days:
        day_blocks = by_day.get(day, [])
        layout = _layout_day(day_blocks)
        slots_html: list[str] = []
        for block, (col, max_cols) in zip(day_blocks, layout):
            classes = ["slot"]
            if block.is_conflict:
                classes.append("conflict")
            tooltip = " · ".join(
                part
                for part in (
                    block.code,
                    block.day,
                    _format_minutes(block.start_min),
                    _format_minutes(block.end_min),
                    block.room,
                    block.professor,
                )
                if part
            )
            meta_parts = []
            if block.room:
                meta_parts.append(block.room)
            if block.professor:
                meta_parts.append(block.professor)
            conflict_badge = (
                '<span class="conflict-badge">충돌</span>'
                if block.is_conflict
                else ""
            )
            slots_html.append(
                '<div class="'
                + " ".join(classes)
                + f'" style="{block_style(block, col, max_cols)}" '
                f'title="{esc(tooltip)}">'
                f'<div class="slot-name">{esc(block.name or block.code)}'
                f"{conflict_badge}</div>"
                f'<div class="slot-meta">[{esc(block.code)}]</div>'
                + (
                    f'<div class="slot-meta">{esc(" · ".join(meta_parts))}</div>'
                    if meta_parts
                    else ""
                )
                + "</div>"
            )
        day_columns.append(
            '<div class="day-col">'
            + "".join(slots_html)
            + ("<div class='empty-note'>—</div>" if not day_blocks else "")
            + "</div>"
        )

    # 요약 + 충돌 리포트
    summary_parts = [f"총 {len(blocks)}개 수업 블록"]
    if conflict_blocks:
        summary_parts.append(
            f"<strong>시간 충돌 {len(conflict_pairs)}건 (블록 {conflict_blocks}개)</strong>"
        )
    else:
        summary_parts.append("시간 충돌 없음")

    conflict_report = ""
    if conflict_pairs:
        rows = []
        for a, b in conflict_pairs:
            overlap_start = max(a.start_min, b.start_min)
            overlap_end = min(a.end_min, b.end_min)
            rows.append(
                f"<li>{esc(a.day)} {_format_minutes(overlap_start)}-"
                f"{_format_minutes(overlap_end)} · "
                f"<strong>{esc(a.name or a.code)}</strong> ({esc(a.code)}) ↔ "
                f"<strong>{esc(b.name or b.code)}</strong> ({esc(b.code)})</li>"
            )
        conflict_report = (
            '<div class="conflict-report"><h3>⚠ 시간 충돌</h3>'
            + "<ul>" + "".join(rows) + "</ul></div>"
        )

    warnings_html = ""
    if warnings:
        warnings_html = (
            '<div class="warnings"><h3>파싱 경고 (표시 제외 강의 가능)</h3><ul>'
            + "".join(f"<li>{esc(w)}</li>" for w in warnings)
            + "</ul></div>"
        )

    empty_note = (
        '<div class="empty-note">표시할 강의가 없습니다.</div>' if not blocks else ""
    )

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<style>
:root {{
  --border: #d1d5db;
  --line: #e5e7eb;
  --normal-bg: #eef2ff;
  --normal-border: #a5b4fc;
  --conflict-bg: #fee2e2;
  --conflict-border: #dc2626;
}}
* {{ box-sizing: border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, "Apple SD Gothic Neo", "Malgun Gothic", "Noto Sans KR", sans-serif; margin: 24px; color: #111827; background: #f9fafb; }}
h1 {{ font-size: 20px; margin: 0 0 6px; }}
h3 {{ font-size: 13px; margin: 0 0 6px; }}
.summary {{ font-size: 13px; color: #4b5563; margin-bottom: 10px; }}
.legend {{ display: flex; gap: 16px; font-size: 12px; color: #4b5563; margin-bottom: 12px; }}
.legend span {{ display: inline-flex; align-items: center; gap: 5px; }}
.swatch {{ display: inline-block; width: 14px; height: 14px; border-radius: 3px; }}
.swatch.normal {{ background: var(--normal-bg); border: 1px solid var(--normal-border); }}
.swatch.conflict {{ background: var(--conflict-bg); border: 1px solid var(--conflict-border); }}
.timetable {{ background: #fff; border: 1px solid var(--border); border-radius: 10px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,.06); }}
.header-row {{ display: flex; background: #f3f4f6; border-bottom: 1px solid var(--border); }}
.corner {{ flex: 0 0 56px; width: 56px; text-align: center; font-weight: 600; font-size: 12px; color: #6b7280; padding: 10px 0; }}
.day-header {{ flex: 1; text-align: center; font-weight: 600; font-size: 13px; padding: 10px 0; border-left: 1px solid var(--line); }}
.body-row {{ display: flex; }}
.gutter {{ flex: 0 0 56px; width: 56px; position: relative; }}
.hour-label {{ position: absolute; right: 6px; transform: translateY(-50%); font-size: 10px; color: #9ca3af; }}
.day-col {{ flex: 1; position: relative; border-left: 1px solid var(--line); background-image: repeating-linear-gradient(to bottom, transparent 0, transparent {60 * _MINUTE_SCALE - 1}px, var(--line) {60 * _MINUTE_SCALE - 1}px, var(--line) {60 * _MINUTE_SCALE}px); }}
.slot {{ position: absolute; border-radius: 6px; padding: 5px 6px; font-size: 11px; line-height: 1.35; overflow: hidden; border: 1px solid var(--normal-border); background: var(--normal-bg); color: #1e293b; }}
.slot.conflict {{ border: 2px solid var(--conflict-border); background: var(--conflict-bg); color: #7f1d1d; }}
.slot .slot-name {{ font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
.slot .slot-meta {{ opacity: .75; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
.conflict-badge {{ display: inline-block; background: var(--conflict-border); color: #fff; font-size: 10px; border-radius: 4px; padding: 0 4px; margin-left: 4px; vertical-align: 1px; }}
.empty-note {{ color: #9ca3af; font-style: italic; font-size: 12px; padding: 6px; }}
.conflict-report {{ margin-top: 16px; font-size: 12px; color: #4b5563; }}
.conflict-report ul {{ margin: 0; padding-left: 18px; }}
.warnings {{ margin-top: 14px; font-size: 12px; color: #b45309; }}
.warnings ul {{ margin: 0; padding-left: 18px; }}
</style>
</head>
<body>
<h1>{esc(title)}</h1>
<div class="summary">{empty_note}{esc(" · ".join(summary_parts))}</div>
<div class="legend">
  <span><span class="swatch normal"></span> 정상</span>
  <span><span class="swatch conflict"></span> 시간 충돌</span>
</div>
<div class="timetable">
  <div class="header-row">
    <div class="corner">시간</div>
    {header_cells}
  </div>
  <div class="body-row" style="height:{total_height}px">
    <div class="gutter">{"".join(hour_labels)}</div>
    {"".join(day_columns)}
  </div>
</div>
{conflict_report}
{warnings_html}
</body>
</html>
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="시간표를 정적 HTML로 렌더링합니다 (표준 라이브러리만 사용)."
    )
    parser.add_argument(
        "input",
        help="강의 dict 목록 JSON 파일 경로 (find_lectures 반환 형태)",
    )
    parser.add_argument(
        "--out",
        default="timetable.html",
        help="출력 HTML 경로 (기본: timetable.html)",
    )
    parser.add_argument(
        "--title",
        default="시간표",
        help="HTML 제목 (기본: '시간표')",
    )
    parser.add_argument(
        "--open",
        action="store_true",
        help="렌더링 후 기본 브라우저로 열기",
    )
    args = parser.parse_args(argv)

    try:
        lectures = load_lectures(Path(args.input))
        blocks, warnings = build_blocks(lectures)
        html_str = render_html(blocks, warnings, title=args.title)
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 1

    out_path = Path(args.out)
    out_path.write_text(html_str, encoding="utf-8")

    conflict_blocks = sum(1 for b in blocks if b.is_conflict)
    print(
        f"시간표 HTML 저장: {out_path.resolve()} "
        f"(수업 블록 {len(blocks)}개, 충돌 블록 {conflict_blocks}개)"
    )
    if args.open:
        webbrowser.open(out_path.resolve().as_uri())
    return 0


if __name__ == "__main__":
    sys.exit(main())
