"""학과-단과대 매핑 캐시.

USAINT 강의시간표의 collages() + departments()를 순회하여 빌드한
{학과명: 단과대} 매핑을 1년 캐싱. 학과 구조 변경이 연 1~2회 수준이라는
가정하에 최초 1회 빌드 비용(10~20초, 단과대 수만큼 네트워크 왕복)을
이후 로컬 조회로 amortize.

3-tier 로딩 순서 (server.py load_department_map이 사용):
1. 로컬 캐시: ${CLAUDE_PLUGIN_DATA}/department_map_{year}.json
   (폴백 ~/.local/share/soongpt-mcp/department_map_{year}.json)
2. 번들 seed: 패키지 내 data/department_map_{year}.json (메인테이너가 커밋)
3. 자동 빌드: USAINT에서 실시간 fetch → 로컬 캐시에 저장

메인테이너 워크플로 (학기별, 학기 시작 전):
- load_department_map(year, force_refresh=True) 호출로 fresh 빌드
- 생성된 로컬 캐시를 src/soongpt_mcp/data/department_map_{year}.json로 복사
- 커밋. 이후 모든 신규 사용자는 0초 seed 사용.
- seed의 semester는 빌드 당시 학기(1/2). 호출 시 현재 학기와 다르면
  server가 seed를 무시하고 자동 빌드하므로(SPR-100), 학기별 신설/통폐합이
  반영되려면 학기 시작마다 위 갱신을 반복한다.

캐시 무효화 조건:
1. 캐시 파일 없음
2. built_at으로부터 365일 경과
3. 저장된 semester가 현재 학기와 다름 (SPR-100) — 학기 특이적 학과 반영
4. 사용자 명시 요청 (force_refresh=True) — 신설/통폐합 학과 반영
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from importlib.resources import files
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)


CACHE_TTL_DAYS = 365


def _department_map_root() -> Path:
    base = os.environ.get("CLAUDE_PLUGIN_DATA")
    if base:
        return Path(base)
    xdg = os.environ.get("XDG_DATA_HOME")
    return (Path(xdg) if xdg else Path.home() / ".local" / "share") / "soongpt-mcp"


def resolve_department_map_path(year: int) -> Path:
    """특정 연도 학과-단과대 매핑 캐시 파일 경로."""
    return _department_map_root() / f"department_map_{year}.json"


def is_department_map_fresh(
    built_at: datetime,
    now: datetime | None = None,
    ttl_days: int = CACHE_TTL_DAYS,
) -> bool:
    """built_at이 now 기준 ttl_days 이내인지."""
    now = now or datetime.now(timezone.utc)
    return (now - built_at) < timedelta(days=ttl_days)


class DepartmentMap(BaseModel):
    """학과-단과대 매핑 스키마.

    year/semester는 빌드에 사용된 기준 학기. mapping의 키는 학과명,
    값은 단과대. built_at은 캐시 신선도 판단 기준.
    """

    model_config = ConfigDict(extra="forbid")

    year: int = Field(..., description="빌드 기준 연도")
    semester: str = Field(..., description="빌드에 사용된 학기 (1 | 2)")
    mapping: dict[str, str] = Field(
        default_factory=dict, description="{학과명: 단과대}"
    )
    built_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="빌드 시각 (캐시 신선도 기준)",
    )


def load_department_map(
    year: int, path: Path | None = None
) -> tuple[DepartmentMap | None, datetime | None]:
    """캐시 로드. (DepartmentMap, built_at) 튜플 반환.

    파일 없으면 (None, None). JSON 손상/스키마 위반 시에도 (None, None)과 경고 로그.
    """
    target = path or resolve_department_map_path(year)
    if not target.exists():
        return None, None
    try:
        raw = target.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("학과-단과대 매핑 파싱 실패 (%s): %s", target, exc)
        return None, None

    try:
        dm = DepartmentMap.model_validate(data)
    except ValueError as exc:
        logger.warning("학과-단과대 매핑 스키마 위반 (%s): %s", target, exc)
        return None, None

    if dm.built_at.tzinfo is None:
        logger.warning(
            "학과-단과대 매핑 built_at에 타임존 정보 없음 (%s): %s",
            target,
            dm.built_at,
        )
        return None, None

    return dm, dm.built_at


def save_department_map(
    dm: DepartmentMap, path: Path | None = None
) -> Path:
    """캐시 저장. atomic write (tmp → rename)로 중단 시 손상 방지."""
    target = path or resolve_department_map_path(dm.year)
    target.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(
        dm.model_dump(mode="json"), ensure_ascii=False, indent=2
    )
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(serialized, encoding="utf-8")
    os.replace(tmp, target)
    return target


def _bundled_data_dir() -> Path:
    """패키지에 번들된 data/ 디렉토리 경로."""
    return Path(str(files("soongpt_mcp") / "data"))


def resolve_bundled_department_map_path(year: int) -> Path:
    """번들 seed 파일 경로 (없을 수도 있음)."""
    return _bundled_data_dir() / f"department_map_{year}.json"


def load_bundled_department_map(year: int) -> DepartmentMap | None:
    """패키지에 번들된 seed 매핑 로드. 파일 없거나 손상 시 None.

    메인테이너가 미리 빌드해 커밋한 정적 데이터. 사용자 로컬 캐시가 없을 때
    즉시 사용되어 최초 10~20초 빌드 비용을 회피.
    """
    target = resolve_bundled_department_map_path(year)
    if not target.is_file():
        return None
    try:
        raw = target.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("번들 학과-단과대 매핑 파싱 실패 (%s): %s", target, exc)
        return None

    try:
        dm = DepartmentMap.model_validate(data)
    except ValueError as exc:
        logger.warning("번들 학과-단과대 매핑 스키마 위반 (%s): %s", target, exc)
        return None

    if dm.built_at.tzinfo is None:
        logger.warning(
            "번들 매핑 built_at에 타임존 정보 없음 (%s): %s", target, dm.built_at
        )
        return None

    return dm
