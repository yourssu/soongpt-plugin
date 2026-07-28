"""현재 학기 판단.

한국 대학 학사일정 기준 하드코딩:
- 1~7월: 1학기 (1학기 + 여름학기 흡수)
- 8~12월: 2학기 (2학기 + 겨울학기 흡수)

계절학기(여름/겨울)는 본 수업이 아니며 시간표 인터뷰 컨텍스트에서
구분이 불필요하므로 직전 본 학기로 통합.
"""
from __future__ import annotations

from datetime import datetime


def current_academic_period(now: datetime | None = None) -> tuple[int, str]:
    """현재 연도와 학기 반환.

    인자 없음: 시스템 시각 사용.
    month ≤ 7 → ("1"), month ≥ 8 → ("2").
    """
    now = now or datetime.now()
    semester = "1" if now.month <= 7 else "2"
    return now.year, semester
