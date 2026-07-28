"""get_graduation_status 캐시 통합 테스트.

캐시 hit / miss / force_refresh / 만료 분기 검증. USAINT fetch는 stub 처리.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from soongpt_mcp import server
from soongpt_mcp import graduation as grad_mod


@pytest.fixture
def isolated_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path))
    return tmp_path


def _patch_service_and_session(
    monkeypatch: pytest.MonkeyPatch,
    payload: dict[str, Any],
    counter: dict[str, int] | None = None,
) -> None:
    """RusaintService.fetch_usaint_graduation_info + _run_with_session 스텁.

    fake_fetch는 bound method로 쓰이므로 self 인자 필요.
    """
    from soongpt_mcp.services.rusaint_service import RusaintService

    async def fake_fetch(self: Any, _session_json: str) -> dict[str, Any]:
        if counter is not None:
            counter["n"] += 1
        return payload

    monkeypatch.setattr(
        RusaintService, "fetch_usaint_graduation_info", fake_fetch
    )

    async def fake_run(func: Any) -> Any:
        return await func("dummy-session")

    monkeypatch.setattr(server, "_run_with_session", fake_run)


@pytest.mark.asyncio
async def test_first_call_fetches_and_caches(
    isolated_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = {"requirements": [{"code": "X"}], "graduationSummary": {"total": 130}}
    _patch_service_and_session(monkeypatch, payload)

    result = await server.get_graduation_status()
    assert result["requirements"] == [{"code": "X"}]
    assert result["_cache"]["source"] == "fresh"
    assert result["_cache"]["age_days"] == 0

    cached, cached_at = grad_mod.load_graduation_cache()
    assert cached == payload
    assert cached_at is not None


@pytest.mark.asyncio
async def test_second_call_uses_cache(
    isolated_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = {"requirements": [], "graduationSummary": {"total": 130}}
    counter: dict[str, int] = {"n": 0}
    _patch_service_and_session(monkeypatch, payload, counter)

    await server.get_graduation_status()
    result = await server.get_graduation_status()
    assert counter["n"] == 1
    assert result["_cache"]["source"] == "cache"


@pytest.mark.asyncio
async def test_force_refresh_bypasses_cache(
    isolated_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = {"requirements": [], "graduationSummary": {"total": 130}}
    counter: dict[str, int] = {"n": 0}
    _patch_service_and_session(monkeypatch, payload, counter)

    await server.get_graduation_status()
    await server.get_graduation_status(force_refresh=True)
    assert counter["n"] == 2


@pytest.mark.asyncio
async def test_expired_cache_triggers_fetch(
    isolated_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    payload = {"requirements": [], "graduationSummary": {"total": 130}}

    stale_payload = {
        "requirements": [{"code": "OLD"}],
        "graduationSummary": {"total": 0},
    }
    stale_target = isolated_root / "graduation.json"
    stale_cached_at = datetime.now(timezone.utc) - timedelta(
        days=grad_mod.CACHE_TTL_DAYS + 1
    )
    stale_target.write_text(
        json.dumps(
            {"cached_at": stale_cached_at.isoformat(), "payload": stale_payload}
        ),
        encoding="utf-8",
    )

    counter: dict[str, int] = {"n": 0}
    _patch_service_and_session(monkeypatch, payload, counter)

    result = await server.get_graduation_status()
    assert counter["n"] == 1
    assert result["_cache"]["source"] == "fresh"
    assert result["graduationSummary"]["total"] == 130

