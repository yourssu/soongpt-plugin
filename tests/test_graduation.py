"""졸업사정표 캐시 로직 테스트 (TTL, atomic write, 손상 복구)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from soongpt_mcp import graduation as grad
from soongpt_mcp.graduation import (
    CACHE_TTL_DAYS,
    is_cache_fresh,
    load_graduation_cache,
    save_graduation_cache,
)


@pytest.fixture
def isolated_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """CLAUDE_PLUGIN_DATA → tmp_path."""
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path))
    return tmp_path


def test_resolve_path_uses_plugin_data(isolated_root: Path) -> None:
    assert grad.resolve_graduation_cache_path() == isolated_root / "graduation.json"


def test_save_then_load_roundtrip(isolated_root: Path) -> None:
    payload = {"requirements": [{"code": "X"}], "summary": {"total_credits": 130}}
    save_graduation_cache(payload)
    loaded, cached_at = load_graduation_cache()
    assert loaded == payload
    assert cached_at is not None
    assert cached_at.tzinfo is not None


def test_load_missing_returns_none(isolated_root: Path) -> None:
    assert load_graduation_cache() == (None, None)


def test_is_cache_fresh_within_ttl() -> None:
    now = datetime(2026, 7, 28, tzinfo=timezone.utc)
    cached = now - timedelta(days=CACHE_TTL_DAYS - 1)
    assert is_cache_fresh(cached, now=now) is True


def test_is_cache_fresh_expired() -> None:
    now = datetime(2026, 7, 28, tzinfo=timezone.utc)
    cached = now - timedelta(days=CACHE_TTL_DAYS + 1)
    assert is_cache_fresh(cached, now=now) is False


def test_load_corrupted_json_returns_none(isolated_root: Path) -> None:
    (isolated_root / "graduation.json").write_text("not json {{{", encoding="utf-8")
    assert load_graduation_cache() == (None, None)


def test_load_missing_cached_at_returns_none(isolated_root: Path) -> None:
    (isolated_root / "graduation.json").write_text(
        json.dumps({"payload": {"x": 1}}),  # cached_at 없음
        encoding="utf-8",
    )
    assert load_graduation_cache() == (None, None)


def test_load_invalid_cached_at_returns_none(isolated_root: Path) -> None:
    (isolated_root / "graduation.json").write_text(
        json.dumps({"cached_at": "not-iso", "payload": {"x": 1}}),
        encoding="utf-8",
    )
    assert load_graduation_cache() == (None, None)


def test_load_missing_payload_returns_none(isolated_root: Path) -> None:
    (isolated_root / "graduation.json").write_text(
        json.dumps({"cached_at": datetime.now(timezone.utc).isoformat()}),
        encoding="utf-8",
    )
    assert load_graduation_cache() == (None, None)


def test_save_creates_parent_directory(tmp_path: Path) -> None:
    nested = tmp_path / "deep" / "graduation.json"
    save_graduation_cache({"x": 1}, path=nested)
    assert nested.exists()
