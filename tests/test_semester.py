"""current_academic_period 하드코딩 분기 테스트."""
from __future__ import annotations

from datetime import datetime

from soongpt_mcp.semester import current_academic_period


def test_january_returns_first_semester() -> None:
    year, sem = current_academic_period(datetime(2026, 1, 15))
    assert (year, sem) == (2026, "1")


def test_july_returns_first_semester() -> None:
    year, sem = current_academic_period(datetime(2026, 7, 31))
    assert (year, sem) == (2026, "1")


def test_august_returns_second_semester() -> None:
    year, sem = current_academic_period(datetime(2026, 8, 1))
    assert (year, sem) == (2026, "2")


def test_december_returns_second_semester() -> None:
    year, sem = current_academic_period(datetime(2026, 12, 31))
    assert (year, sem) == (2026, "2")


def test_default_uses_system_time() -> None:
    """인자 없을 때 시스템 시각 기준. 현재(2026-07) 기대값: (2026, "1")."""
    year, sem = current_academic_period()
    now = datetime.now()
    expected_sem = "1" if now.month <= 7 else "2"
    assert (year, sem) == (now.year, expected_sem)
