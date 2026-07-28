"""InterviewResult 스키마 + load/save/section update 테스트."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from soongpt_mcp import interview as interview_mod
from soongpt_mcp.interview import (
    SECTION_NAMES,
    InterviewResult,
    load_interview,
    save_interview,
)


@pytest.fixture
def isolated_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path))
    return tmp_path


def test_section_names_complete() -> None:
    assert SECTION_NAMES == frozenset(
        {
            "semester_strategy",
            "time_preferences",
            "subject_preferences",
        }
    )


def test_minimal_interview_valid() -> None:
    iv = InterviewResult(year=2026, semester="1")
    assert iv.year == 2026
    assert iv.semester == "1"
    assert iv.semester_strategy == ""
    assert iv.updated_at is not None


def test_extra_field_forbidden() -> None:
    with pytest.raises(ValidationError):
        InterviewResult(year=2026, semester="1", unknown="x")


def test_apply_section_update_sets_text() -> None:
    iv = InterviewResult(year=2026, semester="1")
    updated = iv.apply_section_update(
        "semester_strategy", "15학점 정도 생각 중"
    )
    assert updated.semester_strategy == "15학점 정도 생각 중"
    # 원본 불변
    assert iv.semester_strategy == ""


def test_apply_section_update_overwrites_previous() -> None:
    """덮어쓰기 — 이전 텍스트는 날아감 (merge 아님)."""
    iv = InterviewResult(
        year=2026, semester="1", semester_strategy="옛날 답변"
    )
    updated = iv.apply_section_update(
        "semester_strategy", "새 답변"
    )
    assert updated.semester_strategy == "새 답변"


def test_apply_section_update_empty_string_clears() -> None:
    iv = InterviewResult(
        year=2026, semester="1", semester_strategy="내용 있음"
    )
    updated = iv.apply_section_update("semester_strategy", "   ")
    assert updated.semester_strategy == "   "
    # completion은 strip 기반이라 빈 것으로 간주
    assert updated.completion_summary()["semester_strategy"] is False


def test_apply_section_update_rejects_unknown_section() -> None:
    iv = InterviewResult(year=2026, semester="1")
    with pytest.raises(ValueError, match="알 수 없는 인터뷰 섹션"):
        iv.apply_section_update("unknown_section", "text")


def test_apply_section_update_rejects_non_string_content() -> None:
    """content는 str만 허용. dict 같은 타입은 ValidationError."""
    iv = InterviewResult(year=2026, semester="1")
    with pytest.raises(ValidationError):
        iv.apply_section_update("semester_strategy", {"x": 1})  # type: ignore[arg-type]


def test_completion_summary_all_empty() -> None:
    iv = InterviewResult(year=2026, semester="1")
    summary = iv.completion_summary()
    assert summary == {
        "semester_strategy": False,
        "time_preferences": False,
        "subject_preferences": False,
    }


def test_completion_summary_with_filled_section() -> None:
    iv = InterviewResult(
        year=2026, semester="1",
        semester_strategy="15학점 목표",
    )
    summary = iv.completion_summary()
    assert summary["semester_strategy"] is True
    assert summary["time_preferences"] is False


def test_completion_summary_whitespace_only_is_false() -> None:
    iv = InterviewResult(
        year=2026, semester="1", semester_strategy="   \n  "
    )
    assert iv.completion_summary()["semester_strategy"] is False


def test_resolve_interview_path_uses_current_semester(
    isolated_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "soongpt_mcp.interview.current_academic_period", lambda: (2026, "2")
    )
    path = interview_mod.resolve_interview_path()
    assert path == isolated_root / "interview_2026_2.json"


def test_resolve_interview_path_explicit(isolated_root: Path) -> None:
    path = interview_mod.resolve_interview_path(year=2025, semester="1")
    assert path == isolated_root / "interview_2025_1.json"


def test_save_then_load_roundtrip(isolated_root: Path) -> None:
    iv = InterviewResult(
        year=2026, semester="1",
        semester_strategy="15학점 정도",
        time_preferences="아침 수업 주 2회까지, 금요일은 비움",
    )
    save_interview(iv)
    loaded = load_interview(2026, "1")
    assert loaded is not None
    assert loaded.semester_strategy == "15학점 정도"
    assert loaded.time_preferences == "아침 수업 주 2회까지, 금요일은 비움"


def test_load_missing_returns_none(isolated_root: Path) -> None:
    assert load_interview(2026, "1") is None


def test_load_corrupted_returns_none(isolated_root: Path) -> None:
    target = isolated_root / "interview_2026_1.json"
    target.write_text("not json {{{", encoding="utf-8")
    assert load_interview(2026, "1") is None


def test_load_schema_violation_returns_none(isolated_root: Path) -> None:
    """year 누락 등 스키마 위반 시 None."""
    target = isolated_root / "interview_2026_1.json"
    target.write_text(json.dumps({"semester": "1"}), encoding="utf-8")
    assert load_interview(2026, "1") is None


def test_save_atomic_no_tmp_left(isolated_root: Path) -> None:
    """save 후 .tmp 파일이 남아있지 않은지 확인."""
    iv = InterviewResult(year=2026, semester="1")
    save_interview(iv)
    target = isolated_root / "interview_2026_1.json"
    assert target.exists()
    assert not target.with_suffix(".json.tmp").exists()
