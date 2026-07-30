"""사용자 프로필 영속화 스키마 + 로드/저장.

매번 get_usaint_snapshot을 호출하지 않도록 학적 컨텍스트를 로컬에 저장.
USAINT에서 가져올 수 없는 필드(학번/이름/단과대/트랙)는 사용자가 수동 입력하고,
USAINT에서 추출 가능한 필드(주전공/학년/입학연도)는 refresh_user_profile로 덮어쓰기.

학기별 스냅샷으로 관리 (SPR-33): 전과/학년 증가/세부전공 선택 등 가변 사항을
학기 단위로 고정하기 위해 profile_{year}_{semester}.json 형태로 저장.

저장 경로:
- ${CLAUDE_PLUGIN_DATA}/profile_{year}_{semester}.json (플러그인 구성 시)
- ~/.claude/state/soongpt-planner/profile_{year}_{semester}.json (폴백)

레거시 profile.json (SPR-30)은 현재 학기 파일이 없을 때 읽기 fallback용으로
남아 있을 수 있으며, 다음 save 시점에 새 경로로 마이그레이션됨.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .semester import current_academic_period

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
        "double_major",
        "connected_major",
        "minor",
    }
)


LEGACY_PROFILE_FILENAME = "profile.json"


def _profile_root() -> Path:
    """프로필 저장 디렉토리 루트. CLAUDE_PLUGIN_DATA 우선, 없으면 ~/.claude/state/..."""
    base = os.environ.get("CLAUDE_PLUGIN_DATA")
    if base:
        return Path(base)
    return Path.home() / ".claude" / "state" / "soongpt-planner"


def resolve_profile_path(
    year: int | None = None, semester: str | None = None
) -> Path:
    """프로필 JSON 파일 경로 해석.

    year/semester 생략 시 현재 학기(current_academic_period) 사용.
    부모 디렉토리는 자동 생성하지 않음 (save 시 생성).
    """
    if year is None or semester is None:
        year, semester = current_academic_period()
    return _profile_root() / f"profile_{year}_{semester}.json"


def _resolve_legacy_profile_path() -> Path:
    """레거시 profile.json 경로 (SPR-30). 마이그레이션 읽기 전용."""
    return _profile_root() / LEGACY_PROFILE_FILENAME


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

    @field_validator("name", "college", "department", "track", "double_major", "connected_major", "minor")
    @classmethod
    def _strip_text(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        return v or None

    @classmethod
    def from_basic_info(cls, basic_info: Any) -> UserProfile:
        """USAINT BasicInfo(dict 또는 BaseModel)에서 프로필 생성.

        매핑: department, grade, entered_year, double_major, connected_major,
        minor 추출. 나머지 필드는 None.
        참고: BasicInfo.year는 fetchers.fetch_basic_info에서 admission_year를
        저장한 필드 — 현재 입학연도를 가리킴 (기준 연도가 아님).
        double_major/connected_major/minor는 SPR-35에서 추가 (rusaint.plural_major,
        connected_major, sub_major — 모두 Optional).
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
            double_major=data.get("double_major"),
            connected_major=data.get("connected_major"),
            minor=data.get("minor"),
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

    현재 학기 파일이 없으면 레거시 profile.json(SPR-30 스키마)에서 읽어 마이그레이션.
    JSON 파싱/스키마 위반 시에도 None 반환 후 경고 로그 (손상 파일 복구 지점).
    """
    target = path or resolve_profile_path()
    source = target
    if not target.exists():
        legacy = _resolve_legacy_profile_path()
        if legacy.exists() and legacy != target:
            source = legacy
        else:
            return None
    try:
        raw = source.read_text(encoding="utf-8")
        data = json.loads(raw)
        return UserProfile.model_validate(data)
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning("프로필 파일 파싱 실패 (%s): %s", source, exc)
        return None
    except OSError as exc:
        logger.warning("프로필 파일 읽기 실패 (%s): %s", source, exc)
        return None


def save_profile(profile: UserProfile, path: Path | None = None) -> Path:
    """프로필 저장. 부모 디렉토리 자동 생성.

    임시 파일에 쓴 뒤 atomic rename하여 중단 시 손상 파일을 방지합니다.
    새 경로(profile_{year}_{semester}.json)로 저장한 후, 레거시 profile.json이
    남아 있으면 best-effort로 제거하여 마이그레이션을 완료합니다.
    """
    target = path or resolve_profile_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = profile.model_dump(mode="json")
    serialized = json.dumps(payload, ensure_ascii=False, indent=2)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(serialized, encoding="utf-8")
    os.replace(tmp, target)

    if path is None:
        legacy = _resolve_legacy_profile_path()
        if legacy.exists() and legacy != target:
            try:
                legacy.unlink()
            except OSError as exc:
                logger.warning("레거시 프로필 제거 실패 (%s): %s", legacy, exc)
    return target
