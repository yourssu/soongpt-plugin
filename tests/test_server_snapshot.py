"""get_usaint_snapshot 캐시/저장 통합 테스트 (SPR-46 / SPR-47).

프로필+수강이력 단일 SoT 스냅샷: fetch 시 저장, 캐시 hit 시 재호출 없음,
force_refresh, 만료 재추출, 프로필 병합(사용자 입력 보존)을 검증한다.
SPR-47: 응답 subjectNames가 takenCourses.subjects에서 파생됨을 검증한다.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from soongpt_mcp import server
from soongpt_mcp import snapshot_cache as sc
from soongpt_mcp.profile import UserProfile
from soongpt_mcp.schemas.usaint_schemas import (
    BasicInfo,
    Flags,
    SubjectItem,
    TakenCourse,
    UsaintSnapshotResponse,
)


@pytest.fixture
def isolated_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path))
    return tmp_path


@pytest.fixture
def fixed_period(monkeypatch: pytest.MonkeyPatch) -> tuple[int, str]:
    """server/snapshot_cache 양쪽 현재 학기를 (2026, '1')로 고정."""
    monkeypatch.setattr(
        "soongpt_mcp.server.current_academic_period", lambda: (2026, "1")
    )
    monkeypatch.setattr(
        "soongpt_mcp.snapshot_cache.current_academic_period", lambda: (2026, "1")
    )
    return 2026, "1"


def _make_snapshot(**overrides: Any) -> UsaintSnapshotResponse:
    defaults: dict[str, Any] = {
        "takenCourses": [
            TakenCourse(
                year=2025,
                semester="1",
                subjects=[SubjectItem(code="CSE1234", name="자료구조")],
            )
        ],
        "lowGradeSubjectCodes": ["CSE1234"],
        "flags": Flags(),
        "basicInfo": BasicInfo(
            year=2023, grade=3, semester=5, department="컴퓨터학부"
        ),
        "warnings": [],
    }
    defaults.update(overrides)
    return UsaintSnapshotResponse(**defaults)


def _patch_service(
    monkeypatch: pytest.MonkeyPatch,
    snapshot: UsaintSnapshotResponse,
    counter: dict[str, int] | None = None,
) -> None:
    """RusaintService.fetch_usaint_snapshot + _run_with_session 스텁."""
    from soongpt_mcp.services.rusaint_service import RusaintService

    async def fake_fetch(self: Any, _session_json: str) -> UsaintSnapshotResponse:
        if counter is not None:
            counter["n"] += 1
        return snapshot

    monkeypatch.setattr(RusaintService, "fetch_usaint_snapshot", fake_fetch)

    async def fake_run(func: Any) -> Any:
        return await func("dummy-session")

    monkeypatch.setattr(server, "_run_with_session", fake_run)


@pytest.mark.asyncio
async def test_first_call_fetches_saves_and_returns_fresh(
    isolated_root: Path,
    fixed_period: tuple[int, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_service(monkeypatch, _make_snapshot())

    result = await server.get_usaint_snapshot()
    assert result["_cache"]["source"] == "fresh"
    assert result["takenCourses"][0]["subjects"] == [
        {"code": "CSE1234", "name": "자료구조"}
    ]
    assert result["lowGradeSubjectCodes"] == ["CSE1234"]
    assert result["basicInfo"]["department"] == "컴퓨터학부"

    # 스냅샷 캐시 + 프로필 저장 확인
    cache, fetched_at = sc.load_snapshot_cache(2026, "1")
    assert cache is not None
    assert fetched_at is not None
    profile = sc.load_profile(2026, "1")
    assert profile is not None
    assert profile.department == "컴퓨터학부"
    assert profile.grade == 3
    assert profile.entered_year == 2023


@pytest.mark.asyncio
async def test_second_call_uses_cache(
    isolated_root: Path,
    fixed_period: tuple[int, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    counter: dict[str, int] = {"n": 0}
    _patch_service(monkeypatch, _make_snapshot(), counter)

    await server.get_usaint_snapshot()
    result = await server.get_usaint_snapshot()
    assert counter["n"] == 1
    assert result["_cache"]["source"] == "cache"


@pytest.mark.asyncio
async def test_force_refresh_bypasses_cache(
    isolated_root: Path,
    fixed_period: tuple[int, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    counter: dict[str, int] = {"n": 0}
    _patch_service(monkeypatch, _make_snapshot(), counter)

    await server.get_usaint_snapshot()
    await server.get_usaint_snapshot(force_refresh=True)
    assert counter["n"] == 2


@pytest.mark.asyncio
async def test_expired_cache_triggers_fetch(
    isolated_root: Path,
    fixed_period: tuple[int, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stale = sc.SnapshotCache(
        year=2026,
        semester="1",
        profile=UserProfile(department="옛학과"),
        basicInfo=BasicInfo(year=2023, grade=3, semester=5, department="옛학과"),
        takenCourses=[
            TakenCourse(
                year=2024,
                semester="2",
                subjects=[SubjectItem(code="OLD100", name=None)],
            )
        ],
        fetched_at=datetime.now(timezone.utc)
        - timedelta(days=sc.CACHE_TTL_DAYS + 1),
    )
    sc.save_snapshot_cache(stale)

    counter: dict[str, int] = {"n": 0}
    _patch_service(monkeypatch, _make_snapshot(), counter)

    result = await server.get_usaint_snapshot()
    assert counter["n"] == 1
    assert result["_cache"]["source"] == "fresh"
    assert result["lowGradeSubjectCodes"] == ["CSE1234"]


@pytest.mark.asyncio
async def test_profile_merge_preserves_user_fields(
    isolated_root: Path,
    fixed_period: tuple[int, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server.save_profile(
        UserProfile(student_id="20240001", name="사용자이름", college="IT대학")
    )
    _patch_service(
        monkeypatch,
        _make_snapshot(
            basicInfo=BasicInfo(
                year=2023,
                grade=3,
                semester=5,
                department="컴퓨터학부",
                college="공과대학",
            )
        ),
    )

    await server.get_usaint_snapshot()
    profile = sc.load_profile(2026, "1")
    assert profile is not None
    # 학번/이름은 사용자 입력 보존
    assert profile.student_id == "20240001"
    assert profile.name == "사용자이름"
    # SPR-55: college는 USAINT 제공 필드 — USAINT 값으로 덮어씀
    assert profile.college == "공과대학"
    # USAINT 9필드는 스냅샷 값으로 덮어씀
    assert profile.department == "컴퓨터학부"
    assert profile.grade == 3


@pytest.mark.asyncio
async def test_cache_hit_returns_cached_profile_via_get_user_profile(
    isolated_root: Path,
    fixed_period: tuple[int, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """캐시 hit 시 fetch 없이 프로필도 캐시에서 그대로 노출."""
    sc.save_snapshot_cache(
        sc.SnapshotCache(
            year=2026,
            semester="1",
            profile=UserProfile(department="컴퓨터학부", grade=3, entered_year=2023),
            basicInfo=BasicInfo(year=2023, grade=3, semester=5, department="컴퓨터학부"),
            takenCourses=[
                TakenCourse(
                    year=2025,
                    semester="1",
                    subjects=[SubjectItem(code="CSE1234", name="자료구조")],
                )
            ],
            fetched_at=datetime.now(timezone.utc),
        )
    )

    result = await server.get_usaint_snapshot()
    assert result["_cache"]["source"] == "cache"

    profile_result = await server.get_user_profile()
    assert profile_result["profile"]["department"] == "컴퓨터학부"
    assert profile_result["profile"]["grade"] == 3


@pytest.mark.asyncio
async def test_corrupted_cache_triggers_fetch(
    isolated_root: Path,
    fixed_period: tuple[int, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """손상된 스냅샷 파일은 miss로 처리되어 fetch로 폴백."""
    (isolated_root / "snapshot_2026_1.json").write_text(
        "not json {{{", encoding="utf-8"
    )
    counter: dict[str, int] = {"n": 0}
    _patch_service(monkeypatch, _make_snapshot(), counter)

    result = await server.get_usaint_snapshot()
    assert counter["n"] == 1
    assert result["_cache"]["source"] == "fresh"


@pytest.mark.asyncio
async def test_refresh_user_profile_preserves_snapshot_academic_data(
    isolated_root: Path,
    fixed_period: tuple[int, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """refresh_user_profile(프로필만 갱신)이 스냅샷의 수강이력/신선도를 보존."""
    sc.save_snapshot_cache(
        sc.SnapshotCache(
            year=2026,
            semester="1",
            profile=UserProfile(
                department="컴퓨터학부", grade=3, entered_year=2023, student_id="20240001"
            ),
            basicInfo=BasicInfo(year=2023, grade=3, semester=5, department="컴퓨터학부"),
            takenCourses=[
                TakenCourse(
                    year=2025,
                    semester="1",
                    subjects=[SubjectItem(code="CSE1234", name="자료구조")],
                )
            ],
            fetched_at=datetime.now(timezone.utc),
        )
    )

    basic = BasicInfo(
        year=2024, grade=4, semester=7, department="소프트웨어학부"
    )

    async def fake_fetch() -> tuple[BasicInfo, list[str]]:
        return basic, []

    monkeypatch.setattr(server, "_fetch_basic_info_via_session", fake_fetch)

    await server.refresh_user_profile(preserve_user_overrides=True)

    cache, fetched_at = sc.load_snapshot_cache(2026, "1")
    assert cache is not None
    # USAINT 8필드는 새 값으로 갱신
    assert cache.profile.department == "소프트웨어학부"
    assert cache.profile.grade == 4
    # 수동 입력 필드는 보존
    assert cache.profile.student_id == "20240001"
    # 수강이력/신선도는 그대로 유지
    assert cache.takenCourses[0].subjects[0].code == "CSE1234"
    assert fetched_at is not None


@pytest.mark.asyncio
async def test_subject_names_derived_from_subjects(
    isolated_root: Path,
    fixed_period: tuple[int, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SPR-47: 응답 subjectNames는 takenCourses.subjects에서 파생된다.

    name이 None인 과목은 제외되고, 여러 학기에 중복된 코드는 한 번으로 수렴한다.
    쓰기 진실 소스는 subjects 하나 — subjectNames는 매 응답마다 재파생된다.
    """
    snapshot = UsaintSnapshotResponse(
        takenCourses=[
            TakenCourse(
                year=2025,
                semester="1",
                subjects=[
                    SubjectItem(code="CSE1234", name="자료구조"),
                    SubjectItem(code="CSE1235", name=None),  # 이름 없음 → 제외
                ],
            ),
            TakenCourse(
                year=2024,
                semester="2",
                subjects=[SubjectItem(code="CSE1234", name="자료구조")],  # 재수강 중복
            ),
        ],
        lowGradeSubjectCodes=["CSE1234"],
        flags=Flags(),
        basicInfo=BasicInfo(year=2023, grade=3, semester=5, department="컴퓨터학부"),
        warnings=[],
    )
    _patch_service(monkeypatch, snapshot)

    result = await server.get_usaint_snapshot()
    assert result["_cache"]["source"] == "fresh"
    # name=None 과목은 제외, 중복 코드는 수렴
    assert result["subjectNames"] == {"CSE1234": "자료구조"}
    # takenCourses는 subjects 인라인 구조 유지
    assert result["takenCourses"][0]["subjects"][0] == {"code": "CSE1234", "name": "자료구조"}
    assert result["takenCourses"][0]["subjects"][1] == {"code": "CSE1235", "name": None}

    # 캐시 hit 시에도 동일하게 파생되는지 확인 (subjects가 진실 소스)
    cached_result = await server.get_usaint_snapshot()
    assert cached_result["_cache"]["source"] == "cache"
    assert cached_result["subjectNames"] == {"CSE1234": "자료구조"}


@pytest.mark.asyncio
async def test_snapshot_response_exposes_college_in_basic_info(
    isolated_root: Path,
    fixed_period: tuple[int, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SPR-55: get_usaint_snapshot 응답 basicInfo에 college(단과대) 노출.

    BasicInfo에 college가 추가되면 _format_snapshot_response가 자동 반영.
    fresh/캐시 hit 양쪽에서 노출되는지 확인.
    """
    snapshot = _make_snapshot(
        basicInfo=BasicInfo(
            year=2023,
            grade=3,
            semester=5,
            department="컴퓨터학부",
            college="공과대학",
        )
    )
    _patch_service(monkeypatch, snapshot)

    result = await server.get_usaint_snapshot()
    assert result["_cache"]["source"] == "fresh"
    assert result["basicInfo"]["college"] == "공과대학"
    assert result["basicInfo"]["department"] == "컴퓨터학부"

    cached_result = await server.get_usaint_snapshot()
    assert cached_result["_cache"]["source"] == "cache"
    assert cached_result["basicInfo"]["college"] == "공과대학"


@pytest.mark.asyncio
async def test_snapshot_response_exposes_actual_grade_in_basic_info(
    isolated_root: Path,
    fixed_period: tuple[int, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SPR-71: get_usaint_snapshot 응답 basicInfo에 actual_grade(보정 전 실제 학년) 노출.

    composer ④ 채플 분기가 basicInfo.actual_grade를 읽으므로 fresh/캐시 hit
    양쪽에서 노출돼야 한다. (grade는 PT-87 보정값 2, actual_grade는 실제 학년 1)
    """
    snapshot = _make_snapshot(
        basicInfo=BasicInfo(
            year=2026, grade=2, semester=3, department="컴퓨터학부", actual_grade=1
        )
    )
    _patch_service(monkeypatch, snapshot)

    result = await server.get_usaint_snapshot()
    assert result["_cache"]["source"] == "fresh"
    assert result["basicInfo"]["grade"] == 2
    assert result["basicInfo"]["actual_grade"] == 1

    cached_result = await server.get_usaint_snapshot()
    assert cached_result["_cache"]["source"] == "cache"
    assert cached_result["basicInfo"]["actual_grade"] == 1
