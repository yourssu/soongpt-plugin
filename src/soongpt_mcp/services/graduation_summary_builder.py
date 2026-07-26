"""
졸업사정표 요약 빌더.

name 필드 기반으로 졸업 요건을 분류하여 GraduationSummary를 생성합니다.
"""

from typing import Optional

from soongpt_mcp.schemas.graduation_summary import (
    ChapelSummaryItem,
    CreditSummaryItem,
    GraduationSummary,
)
from soongpt_mcp.schemas.usaint_schemas import GraduationRequirementItem


def _safe_int(value: Optional[float]) -> int:
    return int(value) if value is not None else 0


def _safe_bool(value: Optional[bool]) -> bool:
    return value if value is not None else False


def _is_general_required(name: str) -> bool:
    return "교양필수" in name


def _is_balance_excluded(name: str) -> bool:
    return "Balance" in name


def _is_general_elective(name: str) -> bool:
    return "학부-교양선택" in name


def _is_major_foundation(name: str) -> bool:
    return "전기" in name or "전공기초" in name


def _is_major_required_only(name: str) -> bool:
    return "전필" in name and "전선" not in name and "진선" not in name


def _is_major_elective_only(name: str) -> bool:
    has_elective = "전선" in name or "진선" in name or "전공선택" in name
    return has_elective and "전필" not in name


def _is_major_combined(name: str) -> bool:
    has_required = "전필" in name
    has_elective = "전선" in name or "진선" in name
    return has_required and has_elective


def _is_major_total(name: str) -> bool:
    if "전공" not in name:
        return False
    if any(x in name for x in ["전공기초", "전공선택", "복수전공", "부전공"]):
        return False
    return True


def _is_double_major_required_only(name: str) -> bool:
    return "복필" in name and "복선" not in name and "복수전공" not in name


def _is_double_major_combined(name: str) -> bool:
    return "복수전공" in name


def _is_minor(name: str) -> bool:
    return "부전공" in name


def _is_christian(name: str) -> bool:
    return "기독교" in name


def _is_chapel(name: str) -> bool:
    return "채플" in name


def build_graduation_summary(
    requirements: list[GraduationRequirementItem],
) -> GraduationSummary:
    """
    졸업 요건 목록에서 GraduationSummary를 생성합니다.
    """
    general_required: Optional[CreditSummaryItem] = None
    general_elective: Optional[CreditSummaryItem] = None
    major_foundation: Optional[CreditSummaryItem] = None
    major_required: Optional[CreditSummaryItem] = None
    major_elective: Optional[CreditSummaryItem] = None
    major_combined: Optional[GraduationRequirementItem] = None
    double_major_required: Optional[CreditSummaryItem] = None
    double_major_combined: Optional[GraduationRequirementItem] = None
    minor: Optional[CreditSummaryItem] = None
    christian: Optional[CreditSummaryItem] = None
    chapel: Optional[ChapelSummaryItem] = None

    for req in requirements:
        name = req.name

        if _is_balance_excluded(name):
            continue

        if _is_general_required(name):
            general_required = CreditSummaryItem(
                required=_safe_int(req.requirement),
                completed=_safe_int(req.calculation),
                satisfied=_safe_bool(req.result),
            )
            continue

        if _is_general_elective(name):
            general_elective = CreditSummaryItem(
                required=_safe_int(req.requirement),
                completed=_safe_int(req.calculation),
                satisfied=_safe_bool(req.result),
            )
            continue

        if _is_major_combined(name):
            major_combined = req
            continue

        if _is_major_total(name):
            major_combined = req
            continue

        if _is_major_foundation(name):
            major_foundation = CreditSummaryItem(
                required=_safe_int(req.requirement),
                completed=_safe_int(req.calculation),
                satisfied=_safe_bool(req.result),
            )
            continue

        if _is_major_required_only(name):
            major_required = CreditSummaryItem(
                required=_safe_int(req.requirement),
                completed=_safe_int(req.calculation),
                satisfied=_safe_bool(req.result),
            )
            continue

        if _is_major_elective_only(name):
            major_elective = CreditSummaryItem(
                required=_safe_int(req.requirement),
                completed=_safe_int(req.calculation),
                satisfied=_safe_bool(req.result),
            )
            continue

        if _is_double_major_combined(name):
            double_major_combined = req
            continue

        if _is_double_major_required_only(name):
            double_major_required = CreditSummaryItem(
                required=_safe_int(req.requirement),
                completed=_safe_int(req.calculation),
                satisfied=_safe_bool(req.result),
            )
            continue

        if _is_minor(name):
            minor = CreditSummaryItem(
                required=_safe_int(req.requirement),
                completed=_safe_int(req.calculation),
                satisfied=_safe_bool(req.result),
            )
            continue

        if _is_christian(name):
            christian = CreditSummaryItem(
                required=_safe_int(req.requirement),
                completed=_safe_int(req.calculation),
                satisfied=_safe_bool(req.result),
            )
            continue

        if _is_chapel(name):
            chapel = ChapelSummaryItem(satisfied=_safe_bool(req.result))
            continue

    major_required_elective_combined = False
    combined_name = major_combined.name if major_combined else ""

    if major_combined is not None:
        combined_req = _safe_int(major_combined.requirement)
        combined_calc = _safe_int(major_combined.calculation)

        if major_required is not None:
            elective_req = max(0, combined_req - major_required.required)
            elective_calc = max(0, combined_calc - major_required.completed)
        elif major_foundation is not None:
            combined_includes_foundation = (
                "전기" in combined_name
                or ("전공" in combined_name and "전필" not in combined_name and "전선" not in combined_name)
            )
            if combined_includes_foundation:
                elective_req = max(0, combined_req - major_foundation.required)
                elective_calc = max(0, combined_calc - major_foundation.completed)
            else:
                elective_req = combined_req
                elective_calc = combined_calc
        else:
            elective_req = combined_req
            elective_calc = combined_calc

        major_elective = CreditSummaryItem(
            required=elective_req,
            completed=elective_calc,
            satisfied=elective_calc >= elective_req,
        )

        major_required_elective_combined = (
            major_required is None
            and "전필" in combined_name
            and ("전선" in combined_name or "진선" in combined_name)
        )
        if major_required is None:
            major_required = major_elective

    double_major_elective: Optional[CreditSummaryItem] = None
    if double_major_combined is not None:
        combined_req = _safe_int(double_major_combined.requirement)
        combined_calc = _safe_int(double_major_combined.calculation)

        if double_major_required is not None:
            elective_req = max(0, combined_req - double_major_required.required)
            elective_calc = max(0, combined_calc - double_major_required.completed)
        else:
            elective_req = combined_req
            elective_calc = combined_calc

        double_major_elective = CreditSummaryItem(
            required=elective_req,
            completed=elective_calc,
            satisfied=elective_calc >= elective_req,
        )

    return GraduationSummary(
        generalRequired=general_required,
        generalElective=general_elective,
        majorFoundation=major_foundation,
        majorRequired=major_required,
        majorElective=major_elective,
        majorRequiredElectiveCombined=major_required_elective_combined,
        minor=minor,
        doubleMajorRequired=double_major_required,
        doubleMajorElective=double_major_elective,
        christianCourses=christian,
        chapel=chapel,
    )
