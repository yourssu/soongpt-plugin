"""시간표 인터뷰 결과 스키마 + 영속화 (SPR-33).

사용자의 이번 학기 전략/선호를 3개 섹션에 걸쳐 수집. 각 섹션은 자연어 텍스트로
저장 — LLM이 사용자 답변을 정리해 요약문으로 넣음. 구조적 필드(dict/key-value)는
쓰지 않는다 (사용자 답변의 뉘앙스 보존 + 다음 단계 LLM이 자연어 그대로 소비).

학기별 스냅샷:
- ${CLAUDE_PLUGIN_DATA}/interview_{year}_{semester}.json
- ~/.claude/state/soongpt-planner/interview_{year}_{semester}.json (폴백)

3개 섹션:
1. semester_strategy: 이번 학기 목표 (학점 등) — 자유 텍스트
2. time_preferences: 시간대/요일 선호 + 외부 일정(아르바이트/동아리/수험 등)
3. subject_preferences: 과목 비중 (전공/교양, 재수강, 관심 분야)
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .semester import current_academic_period

logger = logging.getLogger(__name__)


SECTION_NAMES: frozenset[str] = frozenset(
    {
        "semester_strategy",
        "time_preferences",
        "subject_preferences",
    }
)


def _interview_root() -> Path:
    base = os.environ.get("CLAUDE_PLUGIN_DATA")
    if base:
        return Path(base)
    return Path.home() / ".claude" / "state" / "soongpt-planner"


def resolve_interview_path(
    year: int | None = None, semester: str | None = None
) -> Path:
    """인터뷰 결과 JSON 경로. year/semester 생략 시 현재 학기."""
    if year is None or semester is None:
        year, semester = current_academic_period()
    return _interview_root() / f"interview_{year}_{semester}.json"


class InterviewResult(BaseModel):
    """시간표 인터뷰 결과.

    각 섹션은 자연어 텍스트(str). LLM이 사용자 답변을 정리한 요약을 저장.
    빈 문자열("")이 기본값 — 채워졌는지는 .strip() 여부로 판단.
    """

    model_config = ConfigDict(extra="forbid")

    year: int = Field(..., description="인터뷰 대상 연도")
    semester: str = Field(..., description="인터뷰 대상 학기 ('1' | '2')")
    semester_strategy: str = Field(default="", description="이번 학기 전략 요약")
    time_preferences: str = Field(
        default="",
        description="시간대/요일 선호 + 외부 일정 요약",
    )
    subject_preferences: str = Field(
        default="", description="전공/교양 비중, 관심 분야 요약"
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="마지막 수정 시각",
    )

    def apply_section_update(
        self, section: str, content: str
    ) -> InterviewResult:
        """섹션 덮어쓰기. unknown 섹션은 ValueError.

        텍스트 전체를 교체(overwrite) — 부분 수정 시에도 전체 내용을 다시 전달.
        updated_at은 현재 시각으로 갱신.
        """
        if section not in SECTION_NAMES:
            raise ValueError(
                f"알 수 없는 인터뷰 섹션: {section}. 허용 섹션: {sorted(SECTION_NAMES)}"
            )
        merged = self.model_dump()
        merged[section] = content
        merged["updated_at"] = datetime.now(timezone.utc).isoformat()
        return self.__class__.model_validate(merged)

    def completion_summary(self) -> dict[str, bool]:
        """각 섹션 채움 여부 (빈 문자열이 아니면 채워진 것)."""
        return {
            section: bool(getattr(self, section) and getattr(self, section).strip())
            for section in SECTION_NAMES
        }


def load_interview(
    year: int, semester: str, path: Path | None = None
) -> InterviewResult | None:
    """저장된 인터뷰 로드. 파일 없으면 None."""
    target = path or resolve_interview_path(year, semester)
    if not target.exists():
        return None
    try:
        raw = target.read_text(encoding="utf-8")
        data = json.loads(raw)
        return InterviewResult.model_validate(data)
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning("인터뷰 파일 파싱 실패 (%s): %s", target, exc)
        return None
    except OSError as exc:
        logger.warning("인터뷰 파일 읽기 실패 (%s): %s", target, exc)
        return None


def save_interview(
    interview: InterviewResult, path: Path | None = None
) -> Path:
    """인터뷰 저장. atomic write."""
    target = path or resolve_interview_path(interview.year, interview.semester)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = interview.model_dump(mode="json")
    serialized = json.dumps(payload, ensure_ascii=False, indent=2)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(serialized, encoding="utf-8")
    os.replace(tmp, target)
    return target


def list_interview_files(root: Path | None = None) -> list[dict[str, Any]]:
    """저장된 모든 인터뷰 메타 목록 반환.

    반환 항목: {year, semester, completion, updated_at}
    파일명 파싱 실패/스키마 위반 파일은 건너뜀 (best-effort).
    """
    base = root or _interview_root()
    if not base.exists():
        return []
    items: list[dict[str, Any]] = []
    for path in sorted(base.glob("interview_*.json")):
        interview = load_interview_by_path(path)
        if interview is None:
            continue
        items.append(
            {
                "year": interview.year,
                "semester": interview.semester,
                "completion": interview.completion_summary(),
                "updated_at": interview.updated_at.isoformat(),
            }
        )
    return items


def load_interview_by_path(path: Path) -> InterviewResult | None:
    """경로로 직접 로드 (list_interviews용). 파일명 형식 검증 안 함."""
    if not path.exists():
        return None
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
        return InterviewResult.model_validate(data)
    except (json.JSONDecodeError, ValueError, OSError) as exc:
        logger.warning("인터뷰 파일 파싱 실패 (%s): %s", path, exc)
        return None
