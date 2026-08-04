"""
유세인트 데이터 스냅샷 관련 스키마.

MCP 서버 내부 및 클라이언트 응답에 사용됩니다.
"""

from pydantic import BaseModel, ConfigDict, Field
from typing import Optional


# ============================================================
# Request Schemas
# ============================================================


class UsaintSnapshotRequest(BaseModel):
    """유세인트 데이터 스냅샷 요청"""

    studentId: str = Field(..., pattern=r"^\d{8}$", description="학번 (8자리 숫자)")
    sToken: str = Field(..., min_length=1, description="SSO 토큰")


# ============================================================
# Response Schemas
# ============================================================

class SubjectItem(BaseModel):
    """수강 과목 한 개 — 코드 + 강의명 인라인.

    LLM이 takenCourses를 읽을 때 코드→강의명을 별도 사전 join 없이 바로 해석하도록.
    name은 rusaint classes()의 class_name (없으면 None).
    """

    model_config = ConfigDict(extra="forbid")

    code: str = Field(..., description="과목 코드")
    name: Optional[str] = Field(None, description="강의명 (rusaint class_name, 없으면 None)")


class TakenCourse(BaseModel):
    """학기별 수강 과목 정보"""

    model_config = ConfigDict(extra="forbid")

    year: int = Field(..., description="기준 학년도 (예: 2024)")
    semester: str = Field(..., description="학기 ('1': 1학기, '2': 2학기, 'SUMMER': 여름학기, 'WINTER': 겨울학기)")
    subjects: list[SubjectItem] = Field(
        default_factory=list,
        description="해당 학기 수강 과목 리스트 (코드+강의명 인라인)",
    )


class Flags(BaseModel):
    """복수전공/부전공 및 교직 이수 정보"""

    doubleMajorDepartment: Optional[str] = Field(None, description="복수전공 학과명")
    minorDepartment: Optional[str] = Field(None, description="부전공 학과명")
    teaching: bool = Field(False, description="교직 이수 여부")
    teachingMajor: Optional[str] = Field(
        None, description="교직 이수 전공명 (예: '컴퓨터교육'). teaching=False면 None"
    )


class BasicInfo(BaseModel):
    """기본 학적 정보"""

    year: int = Field(..., description="기준 연도 (예: 2025)")
    grade: int = Field(..., ge=1, le=4, description="학년 (1~4)")
    semester: int = Field(..., ge=1, le=8, description="재학 누적 학기 (1~8)")
    actual_grade: int | None = Field(
        None,
        ge=1,
        le=6,
        description=(
            "보정 전 실제 학년 (1~6) — PT-87 임시 +1학기 보정이 들어가지 않은 "
            "USAINT 원본 학년. 채플 분기(1학년→소그룹채플, 2학년+→비전채플)처럼 "
            "'현재 실제 학년'이 필요한 판단에 사용. 구버전 캐시에는 없을 수 있어 Optional."
        ),
    )
    department: str = Field(..., description="주전공 학과명")
    college: str | None = Field(
        None, description="단과대 (rusaint.collage — SPR-55에서 추출)"
    )
    double_major: Optional[str] = Field(
        None, description="복수전공 학과명 (rusaint.plural_major)"
    )
    connected_major: Optional[str] = Field(
        None,
        description="연계·융합전공 명 (rusaint.connected_major — 통합 제공)",
    )
    minor: Optional[str] = Field(
        None, description="부전공 학과명 (rusaint.sub_major)"
    )
    teaching_certification: bool = Field(
        False, description="교직이수 여부 (rusaint teaching_major.major_name 존재 여부)"
    )
    teaching_major: Optional[str] = Field(
        None,
        description="교직 이수 전공명 (예: '컴퓨터교육'). teaching_certification=False면 None",
    )


class GraduationRequirementItem(BaseModel):
    """개별 졸업 요건 항목"""

    name: str = Field(..., description="졸업요건 이름 (예: '학부-교양필수 19')")
    requirement: Optional[int] = Field(None, description="기준 학점 (None일 경우 요구사항 없음)")
    calculation: Optional[float] = Field(None, description="현재 이수 학점")
    difference: Optional[float] = Field(None, description="차이 (이수-기준, 음수면 부족)")
    result: bool = Field(..., description="충족 여부 (true: 충족, false: 미충족)")
    category: str = Field(..., description="이수구분 (예: '전공필수', '교양선택')")


class GraduationRequirements(BaseModel):
    """졸업 요건 전체 목록 (raw 데이터)"""

    requirements: list[GraduationRequirementItem] = Field(
        default_factory=list,
        description="개별 졸업 요건 항목 목록"
    )


class UsaintSnapshotResponse(BaseModel):
    """유세인트 데이터 스냅샷 응답"""

    takenCourses: list[TakenCourse] = Field(
        default_factory=list,
        description="학기별 수강 과목(코드+강의명 subjects) 목록",
    )
    lowGradeSubjectCodes: list[str] = Field(default_factory=list, description="C 이하 성적 과목 코드 리스트 (재수강 대상)")
    flags: Flags = Field(default_factory=Flags, description="복수전공/부전공 및 교직 이수 정보")
    basicInfo: BasicInfo = Field(..., description="기본 학적 정보")
    warnings: list[str] = Field(default_factory=list, description="빈 데이터 경고 코드 (NO_COURSE_HISTORY, NO_SEMESTER_INFO 등)")
