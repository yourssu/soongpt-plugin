"""사용자 프로필 영속화 스키마 + 로드/저장.

매번 get_usaint_snapshot을 호출하지 않도록 학적 컨텍스트를 로컬에 저장.
SSAINT에서 가져올 수 없는 필드(학번/이름/단과대/트랙)는 사용자가 수동 입력하고,
SSAINT에서 추출 가능한 필드(주전공/학년/입학연도)는 refresh_user_profile로 덮어쓰기.

저장 경로:
- ${CLAUDE_PLUGIN_DATA}/profile.json (플러그인 구성 시)
- ~/.claude/state/soongpt-planner/profile.json (폴백)
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

logger = logging.getLogger(__name__)


SUBMISSION_FIELDS: frozenset[str] = frozenset(
    {
        "student_id",
        "name",
        "college",
        "department",
        "grade",
        "track",
        "entered_year",
    }
)


def resolve_profile_path() -> Path:
    """프로필 JSON 파일 경로 해석.

    CLAUDE_PLUGIN_DATA 환경변수가 있으면 해당 디렉토리, 없으면 ~/.claude/state/soongpt-planner/.
    부모 디렉토리는 자동 생성하지 않음 (save 시 생성).
    """
    base = os.environ.get("CLAUDE_PLUGIN_DATA")
    if base:
        root = Path(base)
    else:
        root = Path.home() / ".claude" / "state" / "soongpt-planner"
    return root / "profile.json"


class UserProfile(BaseModel):
    """사용자 학적 프로필.

    모든 식별 필드는 Optional — 처음엔 빈 프로필로 시작해 사용자 입력 또는
    refresh_user_profile()로 채움. updated_at은 저장 시 자동 갱신.
    """

    model_config = ConfigDict(extra="forbid")

    student_id: str | None = Field(None, description="학번 (8자리)")
    name: str | None = Field(None, description="이름")
    college: str | None = Field(None, description="단과대")
    department: str | None = Field(None, description="주전공 학과")
    grade: int | None = Field(None, description="학년 (1~6)", ge=1, le=6)
    track: str | None = Field(None, description="세부 전공/트랙 (선택)")
    entered_year: int | None = Field(None, description="입학 연도")
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

    @field_validator("name", "college", "department", "track")
    @classmethod
    def _strip_text(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        return v or None

    @classmethod
    def from_basic_info(cls, basic_info: Any) -> UserProfile:
        """SSAINT BasicInfo(dict 또는 BaseModel)에서 프로필 생성.

        매핑: department, grade, entered_year만 추출. 나머지 필드는 None.
        참고: BasicInfo.year는 fetchers.fetch_basic_info에서 admission_year를
        저장한 필드 — 현재 입학연도를 가리킴 (기준 연도가 아님).
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
            grade=data.get("grade"),
            entered_year=data.get("year"),
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


def load_profile(path: Path | None = None) -> UserProfile | None:
    """저장된 프로필 로드. 파일 없으면 None.

    JSON 파싱/스키마 위반 시에도 None 반환 후 경고 로그 (손상 파일 복구 지점).
    """
    target = path or resolve_profile_path()
    if not target.exists():
        return None
    try:
        raw = target.read_text(encoding="utf-8")
        data = json.loads(raw)
        return UserProfile.model_validate(data)
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning("프로필 파일 파싱 실패 (%s): %s", target, exc)
        return None
    except OSError as exc:
        logger.warning("프로필 파일 읽기 실패 (%s): %s", target, exc)
        return None


def build_category_requests(profile: UserProfile) -> list[Any]:
    """사용자 프로필 기반으로 조회할 카테고리 요청 목록 생성.

    TODO(사용자 지정): 어떤 카테고리를 부를지 후속 PR에서 결정. 예:
    - major: profile.college, profile.department, profile.major 필요
    - optional_elective: 사용자 관심 분야 (별도 입력 필드 필요)
    - chapel: lecture_name 지정
    - education: 교직이수자만 (profile 필드 추가 필요)
    - graduated: profile.college, profile.department

    UserProfile 스키마를 건드리지 않으려면 별도 설정 파일/툴로 관심 분야를
    받아야 함. 이 뼈대에서는 빈 리스트를 반환하며, get_available_lectures는
    빈 groups를 정상 반환함.
    """
    return []


def save_profile(profile: UserProfile, path: Path | None = None) -> Path:
    """프로필 저장. 부모 디렉토리 자동 생성.

    임시 파일에 쓴 뒤 atomic rename하여 중단 시 손상 파일을 방지합니다.
    """
    target = path or resolve_profile_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = profile.model_dump(mode="json")
    serialized = json.dumps(payload, ensure_ascii=False, indent=2)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(serialized, encoding="utf-8")
    os.replace(tmp, target)
    return target
