"""`_build_category` 라우팅 단위 테스트 (SPR-49).

`RusaintCourseScheduleService._build_category`가 12개 ``category_type``을
rusaint ``LectureCategoryBuilder``의 올바른 메서드로 매핑하는지, 필수 파라미터
누락/빈 문자열 시 ``ValueError``를 발생시키는지, 알 수 없는 타입을 거부하는지를
검증한다. USAINT 세션/네트워크 없이 빌더를 mock으로 교체해 독립 실행한다.

검증 대상 소스(``rusaint_course_schedule_service.py:443-498``)는 변경하지 않는다.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from soongpt_mcp.services import rusaint_course_schedule_service as svc_mod
from soongpt_mcp.services.rusaint_course_schedule_service import (
    RusaintCourseScheduleService,
)

# LectureCategoryBuilder의 12개 메서드 이름(category_type과 1:1 일치).
BUILDER_METHODS = (
    "major",
    "recognized_other_major",
    "graduated",
    "required_elective",
    "chapel",
    "optional_elective",
    "connected_major",
    "united_major",
    "find_by_professor",
    "find_by_lecture",
    "education",
    "cyber",
)

# 12개 라우팅 매핑 + major=None 패스스루 = 13개 케이스.
# (category_type, kwargs, expected_method, expected_args)
ROUTE_CASES = [
    pytest.param(
        "major",
        {"collage": "IT대학", "department": "컴퓨터학부", "major": "컴퓨터전공"},
        "major",
        ("IT대학", "컴퓨터학부", "컴퓨터전공"),
        id="major",
    ),
    pytest.param(
        "major",
        {"collage": "IT대학", "department": "컴퓨터학부"},
        "major",
        ("IT대학", "컴퓨터학부", None),
        id="major-major-none-passthrough",
    ),
    pytest.param(
        "recognized_other_major",
        {"collage": "IT대학", "department": "컴퓨터학부", "major": "컴퓨터전공"},
        "recognized_other_major",
        ("IT대학", "컴퓨터학부", "컴퓨터전공"),
        id="recognized_other_major",
    ),
    pytest.param(
        "graduated",
        {"collage": "IT대학", "department": "컴퓨터학부"},
        "graduated",
        ("IT대학", "컴퓨터학부"),
        id="graduated",
    ),
    pytest.param(
        "required_elective",
        {"lecture_name": "기독교와세계"},
        "required_elective",
        ("기독교와세계",),
        id="required_elective",
    ),
    pytest.param(
        "chapel",
        {"lecture_name": "채플"},
        "chapel",
        ("채플",),
        id="chapel",
    ),
    pytest.param(
        "optional_elective",
        {"category": "과학·기술"},
        "optional_elective",
        ("과학·기술",),
        id="optional_elective",
    ),
    pytest.param(
        "connected_major",
        {"major": "연계전공"},
        "connected_major",
        ("연계전공",),
        id="connected_major",
    ),
    pytest.param(
        "united_major",
        {"major": "융합전공"},
        "united_major",
        ("융합전공",),
        id="united_major",
    ),
    pytest.param(
        "find_by_professor",
        {"keyword": "홍길동"},
        "find_by_professor",
        ("홍길동",),
        id="find_by_professor",
    ),
    pytest.param(
        "find_by_lecture",
        {"keyword": "미분적분학"},
        "find_by_lecture",
        ("미분적분학",),
        id="find_by_lecture",
    ),
    pytest.param(
        "education",
        {},
        "education",
        (),
        id="education",
    ),
    pytest.param(
        "cyber",
        {},
        "cyber",
        (),
        id="cyber",
    ),
]

# 필수 파라미터가 누락/빈 문자열일 때 ValueError 발생 케이스.
# (category_type, kwargs, expected_missing_field_in_message)
REQUIRE_CASES = [
    pytest.param(
        "major",
        {"collage": None, "department": "컴퓨터학부"},
        "collage",
        id="major-collage-none",
    ),
    pytest.param(
        "major",
        {"collage": "IT대학", "department": None},
        "department",
        id="major-department-none",
    ),
    pytest.param(
        "major",
        {"collage": "   ", "department": "컴퓨터학부"},
        "collage",
        id="major-collage-whitespace",
    ),
    pytest.param(
        "recognized_other_major",
        {"department": "컴퓨터학부"},
        "collage",
        id="recognized_other_major-collage-none",
    ),
    pytest.param(
        "graduated",
        {"collage": "IT대학", "department": None},
        "department",
        id="graduated-department-none",
    ),
    pytest.param(
        "required_elective",
        {"lecture_name": None},
        "lecture_name",
        id="required_elective-lecture_name-none",
    ),
    pytest.param(
        "required_elective",
        {"lecture_name": ""},
        "lecture_name",
        id="required_elective-lecture_name-empty",
    ),
    pytest.param(
        "chapel",
        {"lecture_name": None},
        "lecture_name",
        id="chapel-lecture_name-none",
    ),
    pytest.param(
        "optional_elective",
        {"category": None},
        "category",
        id="optional_elective-category-none",
    ),
    pytest.param(
        "connected_major",
        {"major": None},
        "major",
        id="connected_major-major-none",
    ),
    pytest.param(
        "united_major",
        {"major": None},
        "major",
        id="united_major-major-none",
    ),
    pytest.param(
        "find_by_professor",
        {"keyword": None},
        "keyword",
        id="find_by_professor-keyword-none",
    ),
    pytest.param(
        "find_by_professor",
        {"keyword": ""},
        "keyword",
        id="find_by_professor-keyword-empty",
    ),
    pytest.param(
        "find_by_lecture",
        {"keyword": None},
        "keyword",
        id="find_by_lecture-keyword-none",
    ),
]


@pytest.fixture
def service() -> RusaintCourseScheduleService:
    """생성자 인자 없는 서비스 인스턴스."""
    return RusaintCourseScheduleService()


@pytest.fixture
def fake_builder(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """``rusaint.LectureCategoryBuilder()`` 가짜 인스턴스를 주입하고 반환.

    서비스 파일이 모듈레벨 ``import rusaint`` 후 ``rusaint.LectureCategoryBuilder()``
    로 접근하므로, ``svc_mod.rusaint`` 속성을 교체하면 실제 호출 경로와 일치한다.
    각 빌더 메서드는 MagicMock이어서 호출 인자를 기록한다(모두 sync).
    """
    instance = MagicMock()
    monkeypatch.setattr(
        svc_mod.rusaint,
        "LectureCategoryBuilder",
        lambda: instance,
    )
    return instance


def _call_build(
    service: RusaintCourseScheduleService,
    category_type: str,
    **kwargs: str | None,
):
    """``_build_category``의 6개 파라미터를 kwargs에서 채워 호출하는 헬퍼."""
    return service._build_category(
        category_type,
        collage=kwargs.get("collage"),
        department=kwargs.get("department"),
        major=kwargs.get("major"),
        lecture_name=kwargs.get("lecture_name"),
        category=kwargs.get("category"),
        keyword=kwargs.get("keyword"),
    )


@pytest.mark.parametrize(
    ("category_type", "kwargs", "expected_method", "expected_args"),
    ROUTE_CASES,
)
def test_build_category_routes_to_correct_builder_method(
    service: RusaintCourseScheduleService,
    fake_builder: MagicMock,
    category_type: str,
    kwargs: dict[str, str | None],
    expected_method: str,
    expected_args: tuple,
) -> None:
    """각 category_type이 올바른 빌더 메서드를 올바른 인자로 호출하는지 검증."""
    _call_build(service, category_type, **kwargs)

    method = getattr(fake_builder, expected_method)
    method.assert_called_once_with(*expected_args)


def test_build_category_major_calls_no_other_method(
    service: RusaintCourseScheduleService,
    fake_builder: MagicMock,
) -> None:
    """``major`` 라우팅이 다른 빌더 메서드를 함께 호출하지 않음을 검증.

    ``major``와 ``recognized_other_major``는 시그니처가 동일해 라우팅 버그 시
    조용히 잘못된 메서드가 호출될 수 있다. ``if ... return``(elif 아님) 구조에서
    return 누락으로 인한 fall-through 회귀를 잡기 위해 형제 메서드가 모두
    호출되지 않았음을 확인한다.
    """
    _call_build(
        service,
        "major",
        collage="IT대학",
        department="컴퓨터학부",
        major="컴퓨터전공",
    )

    fake_builder.major.assert_called_once_with("IT대학", "컴퓨터학부", "컴퓨터전공")
    for other in BUILDER_METHODS:
        if other == "major":
            continue
        getattr(fake_builder, other).assert_not_called()


@pytest.mark.parametrize(
    ("category_type", "kwargs", "expected_field"),
    REQUIRE_CASES,
)
def test_build_category_requires_missing_params_raises(
    service: RusaintCourseScheduleService,
    fake_builder: MagicMock,
    category_type: str,
    kwargs: dict[str, str | None],
    expected_field: str,
) -> None:
    """필수 파라미터가 누락/빈 문자열이면 ValueError, 메시지에 필드명 포함."""
    with pytest.raises(ValueError, match=expected_field):
        _call_build(service, category_type, **kwargs)


def test_build_category_unknown_type_raises(
    service: RusaintCourseScheduleService,
    fake_builder: MagicMock,
) -> None:
    """알 수 없는 category_type은 ValueError.

    방어/dead path — 프로덕션에서는 ``find_lectures``가 사전 가드
    (``category_type not in CATEGORY_TYPES`` → ``ValueError``)로 먼저 잡으므로
    이 분기(``rusaint_course_schedule_service.py:490``)는 도달 불가다. private
    메서드를 직접 호출하는 단위 테스트에서만 검증 가능하다.
    """
    with pytest.raises(ValueError, match="처리되지 않은 category_type"):
        _call_build(service, "totally_unknown")
