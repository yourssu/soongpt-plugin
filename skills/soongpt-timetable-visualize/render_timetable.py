"""시간표 시각화 렌더러 — 강의 dict 목록 JSON → 정적 HTML / 터미널 텍스트 격자.

표준 라이브러리만 사용하는 스탠드얼론 스크립트. 어떤 python3에서든 실행 가능
(플러그인 venv 경로를 몰라도 됨). soongpt_mcp.timetable_parsing이 import
가능하면 재사용하고(시간 파싱 + 충돌 검사), 미설치 환경은 동일 스펙의 내장
파서로 폴백한다.

사용법:
    # HTML 모드 (기본 — 브라우저 시각화)
    python3 render_timetable.py timetable.json [--out out.html] [--open]

    # 터미널 텍스트 격자 모드 (SPR-117)
    python3 render_timetable.py timetable.json --format text [--title "안 A — 17학점"]

    # 텍스트 격자 + 졸업사정표 반영 표 (SPR-117 — 3학년 이상 후보 제시용)
    python3 render_timetable.py timetable.json --format text --graduation graduation.json

--out 미지정 시 사용자 캐시 디렉토리(~/.cache/soongpt/timetable.html,
$XDG_CACHE_HOME 우선)에 저장하고 저장소/프로젝트 폴더를 오염시키지 않는다
(SPR-80). 저장 경로는 stdout으로 출력한다.

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
강의는 HTML에서 빨간 테두리, 텍스트에서 '⚠'로 강조. JS 없이 Python이 셀
위치를 미리 계산.

졸업 표(--graduation): get_graduation_status() 반환(graduationSummary 포함) 또는
graduationSummary dict 그 자체를 읽어, 후보 강의의 이수구분(category)별 학점
합산으로 '이수 후'/잔여를 계산한다. 카테고리 표만 출력한다 (SPR-117).
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import tempfile
import unicodedata
import webbrowser
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# ── soongpt_mcp.timetable_parsing 재사용 시도 (실패 시 내장 파서 폴백) ──
try:
    from soongpt_mcp.timetable_parsing import (  # noqa: I001
        find_conflicts as _lib_find_conflicts,
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

# 졸업 표(SPR-117): graduationSummary 키 ↔ 후보 강의 이수구분(category) prefix ↔ 표시명.
# graduationSummary 항목(비-null)마다 행을 만들고, category가 prefix로 시작하는
# 후보 강의 학점을 합산해 '이수 후'를 계산한다. 순서 = 표시 순서 (전공 → 교양 → 기타).
_GRAD_CATEGORY_ROWS: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("majorFoundation", ("전기-",), "전공기초"),
    ("majorRequired", ("전필-",), "전공필수"),
    ("majorElective", ("전선-",), "전공선택"),
    ("generalRequired", ("교필",), "교양필수"),
    ("generalElective", ("교선",), "교양선택"),
    ("christianCourses", ("기독교",), "기독교과목"),
    ("chapel", ("채플",), "채플"),
    ("minor", ("부전공",), "부전공"),
    ("doubleMajorRequired", ("복필",), "복수전공필수"),
    ("doubleMajorElective", ("복선",), "복수전공선택"),
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
    동일 code가 여러 번 나오면 최초 항목을 유지한다 (다른 카테고리 중복 수집 정리).
    """
    result: list[dict] = []
    seen: set[str] = set()
    for raw in lectures:
        code = raw.get("code")
        if not code:
            continue
        code = str(code)
        if code in seen:
            continue
        seen.add(code)
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
    """그리드 시간축 [시작, 종료) 분.

    기본 09:00-18:00 범위를 항상 보장하고, 수업이 그 범위를 벗어나면 확장한다
    (SPR-60). 빈 시간대도 그리드에 나오게 해 수업이 몰린 시간대로만 축소되지
    않도록 한다.
    """
    base_start, base_end = 9 * 60, 18 * 60
    if not blocks:
        return base_start, base_end
    start = min(b.start_min for b in blocks)
    end = max(b.end_min for b in blocks)
    start_h = min((start // 60) * 60, base_start)
    end_h = max(((end + 59) // 60) * 60, base_end)
    return start_h, end_h


def _render_days(blocks: list[Block]) -> list[str]:
    """렌더할 요일 목록. 월~금은 항상 표시, 토/일은 수업이 있을 때만 (SPR-60).

    수업이 없는 요일도 컬럼이 항상 보이도록 _BASE_DAYS(월~금)를 기본으로 깔고,
    주말 수업이 있을 때만 토/일을 추가한다.
    """
    present = {b.day for b in blocks}
    days = list(_BASE_DAYS)
    days += [d for d in _WEEKDAY_ORDER if d not in _BASE_DAYS and d in present]
    return days


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

    # 시간 축 라벨 (1시간 간격). window_end는 그리드 바깥 경계라 마지막 시간은 제외
    # (하단 경계에 절반 걸리는 라벨 방지).
    hour_labels = [
        f'<div class="hour-label" style="top:{hour_label_px(hour)}px">'
        f"{hour:02d}:00</div>"
        for hour in range(window_start // 60, window_end // 60)
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
    # summary_parts는 개수(정수)만으로 구성된 내부 문자열이라 사용자 데이터가 없다.
    # esc()를 거치면 <strong> 태그가 리터럴로 노출되므로 escape하지 않는다.
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
<div class="summary">{empty_note}{" · ".join(summary_parts)}</div>
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


# ── 터미널 텍스트 격자 (SPR-117) ────────────────────────────────────────


def _display_width(text: str) -> int:
    """터미널 표시 폭 — 전각(한글 등) 문자는 2칸, 나머지는 1칸 (SPR-117)."""
    return sum(
        2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text
    )


def _pad_display(text: str, width: int) -> str:
    """표시 폭 기준 좌측 정렬 + 공백 패딩 (한글 셀 정렬 보존)."""
    return text + " " * max(width - _display_width(text), 0)


def _truncate_display(text: str, max_width: int) -> str:
    """표시 폭 초과 시 끝 1칸을 '…'로 예약해 잘라낸다 (한글 2칸 고려)."""
    if _display_width(text) <= max_width:
        return text
    out = ""
    used = 0
    for ch in text:
        width = _display_width(ch)
        if used + width > max_width - 1:
            break
        out += ch
        used += width
    return out + "…"


def render_text(
    blocks: list[Block],
    warnings: list[str],
    *,
    title: str = "시간표",
    max_cell_width: int = 18,
) -> str:
    """Block 목록 → 터미널 요일×시간 텍스트 격자 문자열 (SPR-117).

    행 = 강의 시작 시각(전체 블록의 시작 시각 합집합, 정렬). 셀 = 해당 시각에
    시작하는 강의명(충돌은 '⚠' 접두). 셀 폭은 한글 2칸 기준으로 정렬한다.
    격자 아래에 수업 목록(시간·강의실·교수), 시간 충돌 목록, 파싱 경고를
    함께 출력해 잘린 셀 정보를 보완한다.
    """
    days = _render_days(blocks)
    starts = sorted({b.start_min for b in blocks})

    by_cell: dict[tuple[int, str], list[Block]] = {}
    for block in blocks:
        by_cell.setdefault((block.start_min, block.day), []).append(block)

    # 셀 폭: 요일 헤더·강의명(⚠ 포함) 중 최대값 (상한 적용)
    content_widths = [_display_width(day) for day in days]
    for day_blocks in by_cell.values():
        names = " + ".join(
            ("⚠ " if b.is_conflict else "") + (b.name or b.code)
            for b in day_blocks
        )
        content_widths.append(_display_width(names))
    cell_width = min(max(content_widths, default=1), max_cell_width)

    gutter_w = max(
        _display_width("시간"),
        max((_display_width(_format_minutes(s)) for s in starts), default=0),
    )

    def fmt_cell(day: str, start: int) -> str:
        day_blocks = by_cell.get((start, day))
        if not day_blocks:
            return " " * cell_width
        text = " + ".join(
            ("⚠ " if b.is_conflict else "") + (b.name or b.code)
            for b in day_blocks
        )
        return _pad_display(_truncate_display(text, cell_width), cell_width)

    lines: list[str] = []
    if title:
        lines.append(title)

    conflict_pairs = _overlap_pairs(blocks)
    conflict_blocks = sum(1 for b in blocks if b.is_conflict)
    if conflict_pairs:
        summary = (
            f"총 {len(blocks)}개 수업 블록 · "
            f"시간 충돌 {len(conflict_pairs)}건 (블록 {conflict_blocks}개)"
        )
    else:
        summary = f"총 {len(blocks)}개 수업 블록 · 시간 충돌 없음"
    lines.append(summary)

    header = (
        _pad_display("시간", gutter_w)
        + " | "
        + " | ".join(_pad_display(day, cell_width) for day in days)
    )
    lines.append(header)
    lines.append("-" * _display_width(header))

    for start in starts:
        label = _pad_display(_format_minutes(start), gutter_w)
        cells = " | ".join(fmt_cell(day, start) for day in days)
        lines.append(label + " | " + cells)

    if not blocks:
        lines.append("표시할 강의가 없습니다.")

    if blocks:
        lines.append("")
        lines.append("수업 목록")
        day_index = {day: i for i, day in enumerate(days)}
        ordered = sorted(
            blocks, key=lambda b: (day_index.get(b.day, 99), b.start_min, b.code)
        )
        for block in ordered:
            parts = [
                (
                    f"{block.day} {_format_minutes(block.start_min)}-"
                    f"{_format_minutes(block.end_min)}"
                )
            ]
            if block.room:
                parts.append(block.room)
            if block.professor:
                parts.append(block.professor)
            marker = "⚠ " if block.is_conflict else ""
            lines.append(
                f"  {marker}[{block.name or block.code} {block.code}] "
                + " · ".join(parts)
            )

    if conflict_pairs:
        lines.append("")
        lines.append(f"⚠ 시간 충돌 {len(conflict_pairs)}건")
        for a, b in conflict_pairs:
            overlap_start = max(a.start_min, b.start_min)
            overlap_end = min(a.end_min, b.end_min)
            lines.append(
                f"  {a.day} {_format_minutes(overlap_start)}-"
                f"{_format_minutes(overlap_end)} · "
                f"{a.name or a.code} ({a.code}) ↔ {b.name or b.code} ({b.code})"
            )

    if warnings:
        lines.append("")
        lines.append("파싱 경고 (표시 제외 강의 가능)")
        for warning in warnings:
            lines.append(f"  · {warning}")

    return "\n".join(lines) + "\n"


# ── 졸업사정표 반영 표 (SPR-117) ────────────────────────────────────────


def _lecture_credits(lecture: dict) -> float:
    """lecture dict 학점 추출 — credits 필드 우선, 없으면 time_points 앞 숫자.

    time_points는 "3/3" 형태라 학점/시수 앞 숫자가 학점 (timetable_parsing과 동일).
    """
    credits = lecture.get("credits")
    if isinstance(credits, (int, float)):
        return float(credits)
    time_points = lecture.get("time_points")
    if time_points:
        match = re.match(r"\s*(\d+(?:\.\d+)?)", str(time_points))
        if match:
            return float(match.group(1))
    return 0.0


def compute_graduation_rows(
    lectures: list[dict],
    graduation_summary: dict[str, Any],
) -> list[dict[str, Any]]:
    """졸업 요약 + 후보 강의 → 카테고리별 '이수 후'/잔여 행 목록 (SPR-117).

    graduationSummary 비-null 항목마다 행을 만든다. '이수 후' = summary
    completed + 후보 강의의 category별 학점 합산, 잔여 = required - 이수 후
    (0 미만은 0으로 표시, 0이면 충족). 채플은 학점이 아니라 충족 여부만
    추적하므로 후보에 채플 category 강의가 있으면 충족으로 표시한다.
    """
    credits_by_key: dict[str, float] = defaultdict(float)
    has_chapel = False
    for lecture in lectures:
        category = lecture.get("category") or ""
        credits = _lecture_credits(lecture)
        for key, prefixes, _label in _GRAD_CATEGORY_ROWS:
            if any(category.startswith(prefix) for prefix in prefixes):
                if key == "chapel":
                    has_chapel = True
                else:
                    credits_by_key[key] += credits
                break

    rows: list[dict[str, Any]] = []
    for key, _prefixes, label in _GRAD_CATEGORY_ROWS:
        item = graduation_summary.get(key) if isinstance(graduation_summary, dict) else None
        if not isinstance(item, dict):
            continue
        if key == "chapel":
            rows.append(
                {
                    "label": label,
                    "current": None,
                    "after": None,
                    "target": None,
                    "remaining": None,
                    "satisfied": bool(item.get("satisfied")) or has_chapel,
                }
            )
            continue
        current = float(item.get("completed", 0) or 0)
        target = float(item.get("required", 0) or 0)
        after = current + credits_by_key.get(key, 0.0)
        remaining = max(target - after, 0.0)
        rows.append(
            {
                "label": label,
                "current": current,
                "after": after,
                "target": target,
                "remaining": remaining,
                "satisfied": remaining <= 0.0,
            }
        )
    return rows


def _fmt_num(value: float | None) -> str:
    """학점 수 표시 — 정수면 정수로, 아니면 g 포맷. None은 '-'(채플 등)."""
    if value is None:
        return "-"
    if float(value).is_integer():
        return str(int(value))
    return f"{value:g}"


def render_graduation_table(rows: list[dict[str, Any]]) -> str:
    """카테고리 행 목록 → 표 문자열 (카테고리/현재/이수 후/목표/잔여). SPR-117.

    잔여 0(충족)인 행은 잔여 셀에 '✅'를 붙인다. 채플처럼 수치가 없는 항목은
    현재/이수 후/목표를 '-'로 표시하고 충족 시 잔여 셀에 '✅'만 붙인다.
    """
    if not rows:
        return "졸업 요건 데이터 없음"
    headers = ["카테고리", "현재", "이수 후", "목표", "잔여"]

    cat_w = max(_display_width(h) for h in [headers[0], *(r["label"] for r in rows)])
    cells: list[list[str]] = []
    for row in rows:
        if row.get("current") is None:
            current = after = target = "-"
            remaining = "- ✅" if row.get("satisfied") else "-"
        else:
            current = _fmt_num(row["current"])
            after = _fmt_num(row["after"])
            target = _fmt_num(row["target"])
            remaining = _fmt_num(row["remaining"]) + (
                " ✅" if row.get("satisfied") else ""
            )
        cells.append([current, after, target, remaining])

    num_w = [_display_width(headers[i + 1]) for i in range(4)]
    for cell_row in cells:
        for i, cell in enumerate(cell_row):
            num_w[i] = max(num_w[i], len(cell))

    def fmt_row(cat: str, nums: list[str]) -> str:
        num_text = " | ".join(_pad_display(nums[i], num_w[i]) for i in range(4))
        return _pad_display(cat, cat_w) + " | " + num_text

    lines = [fmt_row(headers[0], headers[1:])]
    lines.append("-" * _display_width(lines[0]))
    for row, cell_row in zip(rows, cells):
        lines.append(fmt_row(row["label"], cell_row))
    return "\n".join(lines) + "\n"


DEFAULT_OUT_FILENAME = "timetable.html"


def default_output_dir() -> Path:
    """기본 출력 디렉토리 (SPR-80) — cwd 오염 방지.

    프로젝트 캐시 관례와 동일하게 `$CLAUDE_PLUGIN_DATA`를 1순위로 쓴다
    (플러그인 런타임에서 다른 캐시들과 co-locate). 미설정 시 standalone
    실행으로 보고 사용자 캐시 디렉토리(`$XDG_CACHE_HOME/soongpt` 또는
    `~/.cache/soongpt`)로 폴백한다. 저장소/프로젝트 루트에 untracked 파일이
    남지 않게 하기 위함. 경로만 반환하며 디렉토리 생성은 호출자가 한다.
    """
    base = os.environ.get("CLAUDE_PLUGIN_DATA")
    if base:
        return Path(base)
    xdg = os.environ.get("XDG_CACHE_HOME")
    if xdg:
        return Path(xdg) / "soongpt"
    return Path.home() / ".cache" / "soongpt"


def resolve_out_path(out_arg: str | None) -> Path:
    """출력 HTML 경로 결정 (SPR-80).

    `--out` 지정 시 그대로 사용하고, 미지정 시 `default_output_dir()` 아래에
    `timetable.html`로 저장한다 (cwd를 오염시키지 않는다). 캐시 디렉토리
    생성이 불가한 환경(홈 접근 불가 등)은 OS 임시 디렉토리로 폴백한다.
    """
    if out_arg:
        return Path(out_arg)
    try:
        out_dir = default_output_dir()
        out_dir.mkdir(parents=True, exist_ok=True)
    except (OSError, RuntimeError):
        # 홈/캐시 경로 접근 불가 환경(HOME 미설정 등) — OS 임시 디렉토리 폴백.
        # 어느 쪽도 cwd가 아니므로 저장소/프로젝트 폴더는 절대 오염되지 않는다.
        out_dir = Path(tempfile.gettempdir())
    return out_dir / DEFAULT_OUT_FILENAME


def _load_graduation_summary(path: str | None) -> dict[str, Any] | None:
    """--graduation 파일 로드 → graduationSummary dict (SPR-117).

    get_graduation_status() 반환 전체(graduationSummary 키 포함) 또는
    graduationSummary dict 그 자체를 지원한다. 미지정 시 None.
    """
    if not path:
        return None
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict) and "graduationSummary" in data:
        data = data["graduationSummary"]
    if not isinstance(data, dict):
        raise TypeError(
            "--graduation 파일은 graduationSummary dict 또는 이를 포함한 응답이어야 합니다"
        )
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "시간표를 정적 HTML 또는 터미널 텍스트 격자로 렌더링합니다 "
            "(표준 라이브러리만 사용)."
        )
    )
    parser.add_argument(
        "input",
        help="강의 dict 목록 JSON 파일 경로 (find_lectures 반환 형태)",
    )
    parser.add_argument(
        "--format",
        choices=("html", "text"),
        default="html",
        help="출력 형식: html(기본 — 정적 HTML 파일), text(터미널 텍스트 격자)",
    )
    parser.add_argument(
        "--graduation",
        default=None,
        help=(
            "졸업사정표 요약 JSON 경로 (get_graduation_status 반환 또는 "
            "graduationSummary dict). text 모드에서 졸업 표를 함께 출력"
        ),
    )
    parser.add_argument(
        "--out",
        default=None,
        help=(
            "출력 경로 (기본 html: 사용자 캐시 디렉토리 "
            "$XDG_CACHE_HOME/soongpt/timetable.html 또는 "
            "~/.cache/soongpt/timetable.html / text: stdout)"
        ),
    )
    parser.add_argument(
        "--title",
        default="시간표",
        help="제목 (기본: '시간표')",
    )
    parser.add_argument(
        "--open",
        action="store_true",
        help="HTML 모드에서 렌더링 후 기본 브라우저로 열기",
    )
    args = parser.parse_args(argv)

    try:
        lectures = load_lectures(Path(args.input))
        blocks, warnings = build_blocks(lectures)
        graduation_summary = _load_graduation_summary(args.graduation)
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 1

    if args.format == "text":
        text = render_text(blocks, warnings, title=args.title)
        if graduation_summary is not None:
            rows = compute_graduation_rows(lectures, graduation_summary)
            text = text.rstrip("\n") + "\n\n" + render_graduation_table(rows)
        if args.out:
            out_path = Path(args.out)
            out_path.write_text(text, encoding="utf-8")
            print(f"시간표 텍스트 저장: {out_path.resolve()}")
        else:
            print(text, end="")
        return 0

    html_str = render_html(blocks, warnings, title=args.title)
    out_path = resolve_out_path(args.out)
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
