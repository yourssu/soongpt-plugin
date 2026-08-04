"""fetch_basic_info 단위 테스트 — SPR-55: 단과대(collage) 추출.

student_info_app(general/qualifications)은 fake 객체로 대체하고,
네트워크/브라우저 로그인 없이 순수 로직만 검증한다.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from soongpt_mcp.schemas.usaint_schemas import BasicInfo
from soongpt_mcp.services.fetchers import fetch_basic_info


class _FakeStudentInfo:
    """rusaint StudentInformation 대역 — 필요한 속성만 보유."""

    def __init__(self, **attrs: Any) -> None:
        for key, value in attrs.items():
            setattr(self, key, value)


class _FakeStudentInfoApp:
    def __init__(
        self,
        info: _FakeStudentInfo,
        teaching_major: Any = None,
    ) -> None:
        self._info = info
        self._qual = SimpleNamespace(teaching_major=teaching_major)

    async def general(self) -> _FakeStudentInfo:
        return self._info

    async def qualifications(self) -> SimpleNamespace:
        return self._qual


def _default_info(**overrides: Any) -> _FakeStudentInfo:
    """기본 학적 정보 — collage 제외 (미추출 상황 재현)."""
    attrs = {
        "apply_year": 2023,
        "grade": 3,
        "term": 1,
        "major": "컴퓨터학부",
    }
    attrs.update(overrides)
    return _FakeStudentInfo(**attrs)


@pytest.mark.asyncio
async def test_fetch_basic_info_extracts_collage() -> None:
    """SPR-55: rusaint StudentInformation.collage를 BasicInfo.college로 추출."""
    app = _FakeStudentInfoApp(_default_info(collage="공과대학"))
    basic, warnings = await fetch_basic_info(app)

    assert isinstance(basic, BasicInfo)
    assert basic.college == "공과대학"
    assert basic.department == "컴퓨터학부"
    assert warnings == []


@pytest.mark.asyncio
async def test_fetch_basic_info_collage_none_when_absent() -> None:
    """collage가 없으면 college=None — 다른 Optional 필드와 동일하게 허용."""
    app = _FakeStudentInfoApp(_default_info())
    basic, _ = await fetch_basic_info(app)

    assert basic.college is None


@pytest.mark.asyncio
async def test_fetch_basic_info_collage_whitespace_becomes_none() -> None:
    """collage가 공백 문자열이면 None으로 정규화."""
    app = _FakeStudentInfoApp(_default_info(collage="   "))
    basic, _ = await fetch_basic_info(app)

    assert basic.college is None


@pytest.mark.asyncio
async def test_fetch_basic_info_collage_strips_whitespace() -> None:
    """collage 앞뒤 공백은 제거된다."""
    app = _FakeStudentInfoApp(_default_info(collage="  공과대학  "))
    basic, _ = await fetch_basic_info(app)

    assert basic.college == "공과대학"


@pytest.mark.asyncio
async def test_fetch_basic_info_freshman_second_semester_actual_grade_stays_one() -> None:
    """SPR-71 회귀: 1학년 2학기 학생(grade=1, term=2)은 채플 분기에서 소그룹채플로.

    PT-87 임시 +1학기 보정은 semester 2→3, grade를 2로 올린다. 이 보정값(grade)으로
    채플 분기를 하면 1학년 2학기 학생이 비전채플로 오라우팅된다. actual_grade는
    USAINT 원본 학년(1)을 그대로 보존해 1학년 → 소그룹채플 라우팅을 보장한다.
    """
    app = _FakeStudentInfoApp(_default_info(grade=1, term=2))
    basic, _ = await fetch_basic_info(app)

    # PT-87 보정은 학기 추천용으로 그대로 유지된다 (semester 3 / grade 2).
    assert basic.semester == 3
    assert basic.grade == 2
    # 채플 분기용 실제 학년은 1 — 1학년 2학기도 소그룹채플.
    assert basic.actual_grade == 1


@pytest.mark.asyncio
async def test_fetch_basic_info_actual_grade_matches_usa_int_grade() -> None:
    """actual_grade는 PT-87 보정과 무관하게 USAINT 원본 학년을 보존한다."""
    cases = [
        (1, 1, 1),  # 1학년 1학기 → actual 1
        (2, 1, 2),  # 2학년 1학기 → actual 2
        (2, 2, 2),  # 2학년 2학기 → actual 2
        (4, 2, 4),  # 4학년 2학기 → actual 4
    ]
    for usa_grade, term, expected in cases:
        app = _FakeStudentInfoApp(_default_info(grade=usa_grade, term=term))
        basic, _ = await fetch_basic_info(app)
        assert basic.actual_grade == expected, (
            f"grade={usa_grade}, term={term} → actual_grade={basic.actual_grade} "
            f"(expected {expected})"
        )


@pytest.mark.asyncio
async def test_fetch_basic_info_actual_grade_clamped_to_six() -> None:
    """초과학기 학년(7)은 actual_grade=6으로 클램프 — BasicInfo le=6 검증 보호.

    USAINT grade를 그대로 넣으면 초과학기 7년차에서 스키마 ValidationError로
    스냅샷 전체 fetch가 실패할 수 있다. 채플 분기(>=2 → 비전채플) 동작은
    6으로 클램프해도 같다.
    """
    app = _FakeStudentInfoApp(_default_info(grade=7, term=1))
    basic, _ = await fetch_basic_info(app)

    assert basic.actual_grade == 6
    # PT-87 보정 학년은 기존대로 4 상한
    assert basic.grade == 4


@pytest.mark.asyncio
async def test_fetch_basic_info_keeps_existing_fields_with_collage() -> None:
    """collage 추출이 기존 필드 추출을 깨지 않는지 확인."""
    app = _FakeStudentInfoApp(
        _default_info(
            collage="IT대학",
            plural_major="경영학과",
            connected_major="AI·소프트웨어융합",
            sub_major="철학과",
        )
    )
    basic, _ = await fetch_basic_info(app)

    assert basic.college == "IT대학"
    assert basic.department == "컴퓨터학부"
    assert basic.year == 2023
    assert basic.grade == 3
    assert basic.double_major == "경영학과"
    assert basic.connected_major == "AI·소프트웨어융합"
    assert basic.minor == "철학과"
