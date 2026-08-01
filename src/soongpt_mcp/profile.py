"""사용자 프로필 스키마.

학적 컨텍스트를 로컬에 저장해 매번 get_usaint_snapshot을 호출하지 않도록 한다.
USAINT에서 가져올 수 없는 필드(학번/이름/트랙)는 사용자가 수동 입력하고,
USAINT에서 추출 가능한 필드(단과대/주전공/학년/입학연도/복수·연계·부전공/교직)는
refresh_user_profile 또는 get_usaint_snapshot으로 덮어쓰기.

SPR-46부터 프로필 영속화는 snapshot_cache.py의 학기별 스냅샷 파일
(snapshot_{year}_{semester}.json)이 단일 SoT로 담당한다 — 이 모듈은 스키마와
검증/매핑 로직만 보유하며 파일 I/O는 하지 않는다.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

SUBMISSION_FIELDS: frozenset[str] = frozenset(
    {
        "student_id",
        "name",
        "college",
        "department",
        "grade",
        "track",
        "entered_year",
        "double_major",
        "connected_major",
        "minor",
        "teaching_certification",
        "teaching_major",
    }
)


class UserProfile(BaseModel):
    """사용자 학적 프로필.

    모든 식별 필드는 Optional — 처음엔 빈 프로필로 시작해 사용자 입력 또는
    get_usaint_snapshot()/refresh_user_profile()로 채운다. updated_at은 저장 시
    자동 갱신.
    """

    model_config = ConfigDict(extra="forbid")

    student_id: str | None = Field(None, description="학번 (8자리)")
    name: str | None = Field(None, description="이름")
    college: str | None = Field(None, description="단과대")
    department: str | None = Field(None, description="주전공 학과")
    grade: int | None = Field(None, description="학년 (1~6)", ge=1, le=6)
    track: str | None = Field(None, description="세부 전공/트랙 (선택)")
    entered_year: int | None = Field(None, description="입학 연도")
    double_major: str | None = Field(
        None, description="복수전공 학과 (USAINT plural_major)"
    )
    connected_major: str | None = Field(
        None,
        description="연계·융합전공 (USAINT connected_major — 연계/융합 통합)",
    )
    minor: str | None = Field(
        None, description="부전공 학과 (USAINT sub_major)"
    )
    teaching_certification: bool = Field(
        False, description="교직이수 여부 (True: 교직 이수자)"
    )
    teaching_major: str | None = Field(
        None, description="교직 이수 전공명 (예: '컴퓨터교육'). 미이수 시 None"
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="마지막 수정 시각",
    )

    @field_validator("student_id")
    @classmethod
    def _validate_student_id(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not v:
            return None
        if not v.isdigit():
            raise ValueError("학번은 숫자만 허용됩니다")
        return v

    @field_validator(
        "name",
        "college",
        "department",
        "track",
        "double_major",
        "connected_major",
        "minor",
        "teaching_major",
    )
    @classmethod
    def _strip_text(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        return v or None

    @classmethod
    def from_basic_info(cls, basic_info: Any) -> UserProfile:
        """USAINT BasicInfo(dict 또는 BaseModel)에서 프로필 생성.

        매핑: department, college, grade, entered_year, double_major,
        connected_major, minor, teaching_certification, teaching_major 추출.
        나머지 필드는 None.

        참고: BasicInfo.year는 fetchers.fetch_basic_info에서 admission_year를
        저장한 필드 — 현재 입학연도를 가리킴 (기준 연도가 아님).
        double_major/connected_major/minor는 SPR-35에서 추가 (rusaint.plural_major,
        connected_major, sub_major — 모두 Optional).
        teaching_certification/teaching_major는 SPR-36에서 추가 (rusaint
        teaching_major.major_name — Optional).
        college는 SPR-55에서 추가 (rusaint.collage — USAINT가 단과대 제공).
        """
        if hasattr(basic_info, "model_dump"):
            data = basic_info.model_dump()
        elif isinstance(basic_info, dict):
            data = basic_info
        else:
            raise TypeError(
                f"from_basic_info는 dict 또는 BaseModel을 받아야 합니다: {type(basic_info).__name__}"
            )

        return cls(
            department=data.get("department"),
            college=data.get("college"),
            grade=data.get("grade"),
            entered_year=data.get("year"),
            double_major=data.get("double_major"),
            connected_major=data.get("connected_major"),
            minor=data.get("minor"),
            teaching_certification=bool(data.get("teaching_certification")),
            teaching_major=data.get("teaching_major"),
        )

    def apply_partial_update(self, updates: dict[str, Any]) -> UserProfile:
        """허용된 필드만 부분 업데이트한 새 인스턴스 반환.

        updated_at은 항상 현재 시각으로 갱신. unknown 필드는 ValueError.
        model_validate로 재검증하여 grade/학번 등 제약조건을 강제합니다.
        """
        unknown = set(updates) - SUBMISSION_FIELDS
        if unknown:
            raise ValueError(f"알 수 없는 프로필 필드: {sorted(unknown)}")

        merged = self.model_dump()
        for k, v in updates.items():
            if isinstance(v, str):
                v = v.strip()
                if not v:
                    v = None
            merged[k] = v
        merged["updated_at"] = datetime.now(timezone.utc)

        return self.__class__.model_validate(merged)
