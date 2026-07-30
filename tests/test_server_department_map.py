"""load_department_map 캐시 통합 테스트.

캐시 hit / miss / force_refresh / 만료 분기 검증. USAINT fetch는 stub 처리.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from soongpt_mcp import department_map as dm_mod
from soongpt_mcp import server
from soongpt_mcp.department_map import DepartmentMap


@pytest.fixture
def isolated_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path))
    # 번들 seed가 실제 repo 파일을 읽지 않도록 빈 디렉토리로 격리.
    # 개별 테스트가 _seed_bundled()로 덮어쓰지 않는 한 bundled는 항상 miss.
    empty_bundled = tmp_path / "_bundled_empty"
    empty_bundled.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        dm_mod,
        "resolve_bundled_department_map_path",
        lambda year: empty_bundled / f"department_map_{year}.json",
    )
    return tmp_path


def _patch_build_and_session(
    monkeypatch: pytest.MonkeyPatch,
    mapping_payload: dict[str, str],
    counter: dict[str, int] | None = None,
) -> None:
    """RusaintService.build_department_map + _run_with_session 스텁.

    fake_build은 bound method로 쓰이므로 self 인자 필요.
    """
    from soongpt_mcp.services.rusaint_service import RusaintService

    async def fake_build(
        self: Any, _session_json: str, year: int, semester: str
    ) -> dict[str, Any]:
        if counter is not None:
            counter["n"] += 1
        return {
            "mapping": dict(mapping_payload),
            "collages": sorted(set(mapping_payload.values())),
            "department_count": len(mapping_payload),
            "collage_count": len(set(mapping_payload.values())),
            "fetchTime": "0.50s",
        }

    monkeypatch.setattr(RusaintService, "build_department_map", fake_build)

    async def fake_run(func: Any) -> Any:
        return await func("dummy-session")

    monkeypatch.setattr(server, "_run_with_session", fake_run)


@pytest.mark.asyncio
async def test_first_call_builds_and_caches(
    isolated_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = {"컴퓨터학부": "IT대학", "경영학부": "경영대학"}
    _patch_build_and_session(monkeypatch, payload)

    result = await server.load_department_map(2026)
    assert result["year"] == 2026
    assert result["mapping"] == payload
    assert result["count"] == 2
    assert result["_cache"]["source"] == "fresh"
    assert result["_cache"]["age_days"] == 0

    cached, built_at = dm_mod.load_department_map(2026)
    assert cached is not None
    assert cached.mapping == payload
    assert built_at is not None


@pytest.mark.asyncio
async def test_second_call_uses_cache(
    isolated_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = {"컴퓨터학부": "IT대학"}
    counter: dict[str, int] = {"n": 0}
    _patch_build_and_session(monkeypatch, payload, counter)

    await server.load_department_map(2026)
    result = await server.load_department_map(2026)
    assert counter["n"] == 1
    assert result["_cache"]["source"] == "cache"
    assert result["mapping"] == payload


@pytest.mark.asyncio
async def test_force_refresh_bypasses_cache(
    isolated_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = {"컴퓨터학부": "IT대학"}
    counter: dict[str, int] = {"n": 0}
    _patch_build_and_session(monkeypatch, payload, counter)

    await server.load_department_map(2026)
    await server.load_department_map(2026, force_refresh=True)
    assert counter["n"] == 2


@pytest.mark.asyncio
async def test_expired_cache_triggers_rebuild(
    isolated_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stale_mapping = {"OLD_DEPT": "OLD_COLLEGE"}
    stale_dm = DepartmentMap(
        year=2026,
        semester="1",
        mapping=stale_mapping,
        built_at=datetime.now(timezone.utc)
        - timedelta(days=dm_mod.CACHE_TTL_DAYS + 1),
    )
    stale_path = isolated_root / "department_map_2026.json"
    stale_path.write_text(
        json.dumps(stale_dm.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    fresh_payload = {"NEW_DEPT": "NEW_COLLEGE"}
    counter: dict[str, int] = {"n": 0}
    _patch_build_and_session(monkeypatch, fresh_payload, counter)

    result = await server.load_department_map(2026)
    assert counter["n"] == 1
    assert result["_cache"]["source"] == "fresh"
    assert result["mapping"] == fresh_payload


@pytest.mark.asyncio
async def test_build_propagates_collages_into_mapping(
    isolated_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """여러 단과대-학과 쌍이 올바르게 병합되는지 (stub이지만 payload 다변화)."""
    payload = {
        "컴퓨터학부": "IT대학",
        "글로벌미디어학부": "IT대학",
        "경영학부": "경영대학",
        "회계학과": "경영대학",
        "법학과": "법과대학",
    }
    _patch_build_and_session(monkeypatch, payload)

    result = await server.load_department_map(2026)
    assert result["count"] == 5
    # 같은 단과대에 여러 학과 매핑되는지
    assert result["mapping"]["컴퓨터학부"] == "IT대학"
    assert result["mapping"]["글로벌미디어학부"] == "IT대학"
    assert result["mapping"]["회계학과"] == "경영대학"


@pytest.mark.asyncio
async def test_semester_auto_resolved_to_current(
    isolated_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """semester를 안 넘겨도 현재 학기로 자동 결정되는지."""
    captured: dict[str, Any] = {}

    from soongpt_mcp.services.rusaint_service import RusaintService

    async def fake_build(
        self: Any, _session_json: str, year: int, semester: str
    ) -> dict[str, Any]:
        captured["year"] = year
        captured["semester"] = semester
        return {
            "mapping": {"X": "Y"},
            "collages": ["Y"],
            "department_count": 1,
            "collage_count": 1,
            "fetchTime": "0.10s",
        }

    monkeypatch.setattr(RusaintService, "build_department_map", fake_build)

    async def fake_run(func: Any) -> Any:
        return await func("dummy-session")

    monkeypatch.setattr(server, "_run_with_session", fake_run)

    await server.load_department_map(2026)
    assert captured["year"] == 2026
    assert captured["semester"] in ("1", "2")


# --- 3-tier fallback: cache → bundled seed → 자동 빌드 ---


def _seed_bundled(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    mapping: dict[str, str],
    built_at: datetime,
    year: int = 2026,
) -> Path:
    """resolve_bundled_department_map_path가 tmp_path를 가리키도록 패치하고 seed 파일 작성."""
    seed_dir = tmp_path / "bundled"
    seed_dir.mkdir(parents=True, exist_ok=True)
    seed_path = seed_dir / f"department_map_{year}.json"
    seed_path.write_text(
        json.dumps(
            {
                "year": year,
                "semester": "1",
                "mapping": mapping,
                "built_at": built_at.isoformat(),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        dm_mod,
        "resolve_bundled_department_map_path",
        lambda y: seed_dir / f"department_map_{y}.json",
    )
    return seed_path


@pytest.mark.asyncio
async def test_local_miss_bundled_hit_uses_bundled(
    isolated_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """로컬 캐시 없고 seed valid → source='bundled', 빌드 호출 안 됨."""
    seed_mapping = {"번들학과": "번들단과대"}
    _seed_bundled(
        monkeypatch,
        isolated_root,
        seed_mapping,
        datetime.now(timezone.utc),
    )

    counter: dict[str, int] = {"n": 0}
    _patch_build_and_session(monkeypatch, {"SHOULD_NOT_USED": "X"}, counter)

    result = await server.load_department_map(2026)
    assert counter["n"] == 0  # 자동 빌드 미호출
    assert result["_cache"]["source"] == "bundled"
    assert result["mapping"] == seed_mapping


@pytest.mark.asyncio
async def test_local_cache_wins_over_bundled(
    isolated_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """로컬 캐시와 seed 둘 다 있으면 로컬이 우선."""
    _seed_bundled(
        monkeypatch,
        isolated_root,
        {"SEED": "단과대"},
        datetime.now(timezone.utc),
    )

    # 로컬 캐시를 직접 준비 (빌드 루트 우회)
    from soongpt_mcp.department_map import DepartmentMap

    local_mapping = {"LOCAL": "단과대"}
    save_via_cache = DepartmentMap(
        year=2026,
        semester="2",
        mapping=local_mapping,
        built_at=datetime.now(timezone.utc),
    )
    dm_mod.save_department_map(save_via_cache)

    counter: dict[str, int] = {"n": 0}
    _patch_build_and_session(monkeypatch, {"SHOULD_NOT_USED": "X"}, counter)

    result = await server.load_department_map(2026)
    assert counter["n"] == 0
    assert result["_cache"]["source"] == "cache"
    assert result["mapping"] == local_mapping


@pytest.mark.asyncio
async def test_bundled_stale_triggers_build(
    isolated_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """seed가 있어도 TTL 만료 시 자동 빌드."""
    _seed_bundled(
        monkeypatch,
        isolated_root,
        {"STALE_SEED": "단과대"},
        datetime.now(timezone.utc)
        - timedelta(days=dm_mod.CACHE_TTL_DAYS + 1),
    )

    fresh_mapping = {"FRESH": "단과대"}
    counter: dict[str, int] = {"n": 0}
    _patch_build_and_session(monkeypatch, fresh_mapping, counter)

    result = await server.load_department_map(2026)
    assert counter["n"] == 1
    assert result["_cache"]["source"] == "fresh"
    assert result["mapping"] == fresh_mapping


@pytest.mark.asyncio
async def test_force_refresh_bypasses_bundled(
    isolated_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """force_refresh=True는 seed도 우회하고 무조건 빌드."""
    _seed_bundled(
        monkeypatch,
        isolated_root,
        {"SEED": "단과대"},
        datetime.now(timezone.utc),
    )

    fresh_mapping = {"FRESH": "단과대"}
    counter: dict[str, int] = {"n": 0}
    _patch_build_and_session(monkeypatch, fresh_mapping, counter)

    result = await server.load_department_map(2026, force_refresh=True)
    assert counter["n"] == 1
    assert result["_cache"]["source"] == "fresh"
    assert result["mapping"] == fresh_mapping
