"""find_lectures 서버 측 자동 저장 (SPR-75) 테스트.

``_run_with_session``을 스텁해 USAINT 실제 호출 없이 find_lectures가
save_to_cache 기본 True로 캐시에 즉시 그룹 저장하는지, False/확인용
조회는 저장을 건너뛰는지, fetch 실패 시 error 그룹을 남기고 예외를
재전파하는지 검증한다.

CLAUDE_PLUGIN_DATA는 conftest의 전역 autouse 픽스처가 임시 디렉토리로 격리한다.
"""
from __future__ import annotations

from typing import Any

import pytest

from soongpt_mcp import lectures_cache as cache_mod
from soongpt_mcp import server
from soongpt_mcp.services.exceptions import RusaintInternalError

_FAKE_RESULT = {
    "lectures": [{"code": "CS101", "name": "컴퓨터개론"}],
    "count": 1,
    "fetchTime": "0.10s",
    "includeDetails": False,
}


async def _fake_success_run(func: Any) -> dict[str, Any]:
    """성공 fetch 스텁 — lectures 1건 반환."""
    return dict(_FAKE_RESULT)


async def _fake_empty_run(func: Any) -> dict[str, Any]:
    return {"lectures": [], "count": 0, "fetchTime": "0.10s"}


async def _fake_raise_run(func: Any) -> dict[str, Any]:
    raise RusaintInternalError("유세인트 강의시간표 조회 중 오류")


def _loaded(year: int, semester: str):
    return cache_mod.load_lectures_cache(year, semester)


@pytest.mark.asyncio
async def test_find_lectures_auto_saves_group(monkeypatch: pytest.MonkeyPatch) -> None:
    """save_to_cache 기본 True → fetch 즉시 캐시에 그룹 저장 + _cache 메타."""
    monkeypatch.setattr(server, "_run_with_session", _fake_success_run)

    result = await server.find_lectures(
        2026, "1", "major", collage="IT대학", department="컴퓨터학부"
    )
    assert result["_cache"]["group_key"] == "major_IT대학_컴퓨터학부"
    assert result["_cache"]["saved"] is True

    cache, _ = _loaded(2026, "1")
    assert cache is not None
    group = cache.groups["major_IT대학_컴퓨터학부"]
    assert group.lectures == _FAKE_RESULT["lectures"]
    assert group.count == 1
    assert group.error is None
    assert group.params["collage"] == "IT대학"
    assert group.params["department"] == "컴퓨터학부"


@pytest.mark.asyncio
async def test_find_lectures_optional_elective_all_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """교양선택 '전체'는 optional_elective_all 단일 그룹 키."""
    monkeypatch.setattr(server, "_run_with_session", _fake_empty_run)

    result = await server.find_lectures(
        2026, "1", "optional_elective", category="전체"
    )
    assert result["_cache"]["group_key"] == "optional_elective_all"
    assert result["_cache"]["saved"] is True

    cache, _ = _loaded(2026, "1")
    assert cache is not None
    assert "optional_elective_all" in cache.groups


@pytest.mark.asyncio
async def test_find_lectures_save_to_cache_false_skips(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """save_to_cache=False → 저장 안 함."""
    monkeypatch.setattr(server, "_run_with_session", _fake_success_run)

    result = await server.find_lectures(
        2026, "1", "major", collage="IT대학", department="컴퓨터학부",
        save_to_cache=False,
    )
    assert result["_cache"]["saved"] is False
    assert result["_cache"]["group_key"] == "major_IT대학_컴퓨터학부"

    cache, _ = _loaded(2026, "1")
    assert cache is None


@pytest.mark.asyncio
async def test_find_lectures_confirm_only_never_saves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """확인용 조회(find_by_lecture/find_by_professor, include_details)는
    save_to_cache 기본 True여도 캐시 오염을 막기 위해 저장 제외."""
    monkeypatch.setattr(server, "_run_with_session", _fake_success_run)

    by_lecture = await server.find_lectures(
        2026, "1", "find_by_lecture", keyword="미분적분학"
    )
    assert by_lecture["_cache"]["saved"] is False

    by_professor = await server.find_lectures(
        2026, "1", "find_by_professor", keyword="홍길동"
    )
    assert by_professor["_cache"]["saved"] is False

    detailed = await server.find_lectures(
        2026, "1", "major", collage="IT대학", department="컴퓨터학부",
        include_details=True,
    )
    assert detailed["_cache"]["saved"] is False

    cache, _ = _loaded(2026, "1")
    assert cache is None


@pytest.mark.asyncio
async def test_find_lectures_error_records_group_and_reraises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """fetch 실패 → error 그룹(lectures=[], count=0) 캐시 기록 후 예외 재전파."""
    monkeypatch.setattr(server, "_run_with_session", _fake_raise_run)

    with pytest.raises(RusaintInternalError, match="강의시간표 조회 중 오류"):
        await server.find_lectures(
            2026, "1", "major", collage="IT대학", department="컴퓨터학부"
        )

    cache, _ = _loaded(2026, "1")
    assert cache is not None
    group = cache.groups["major_IT대학_컴퓨터학부"]
    assert group.error is not None
    assert "강의시간표 조회 중 오류" in group.error
    assert group.lectures == []
    assert group.count == 0


@pytest.mark.asyncio
async def test_find_lectures_error_no_save_when_save_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """save_to_cache=False면 실패 시에도 error 그룹을 남기지 않는다."""
    monkeypatch.setattr(server, "_run_with_session", _fake_raise_run)

    with pytest.raises(RusaintInternalError):
        await server.find_lectures(
            2026, "1", "find_by_lecture", keyword="미분적분학",
            save_to_cache=False,
        )

    cache, _ = _loaded(2026, "1")
    assert cache is None


@pytest.mark.asyncio
async def test_find_lectures_autosave_preserves_previous_groups(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """연속 fetch가 병합 저장 — 앞선 그룹이 보존된다 (덮어쓰기 제거)."""
    monkeypatch.setattr(server, "_run_with_session", _fake_success_run)

    await server.find_lectures(
        2026, "1", "major", collage="IT대학", department="컴퓨터학부"
    )
    monkeypatch.setattr(server, "_run_with_session", _fake_empty_run)
    await server.find_lectures(
        2026, "1", "optional_elective", category="전체"
    )

    cache, _ = _loaded(2026, "1")
    assert cache is not None
    assert set(cache.groups) == {
        "major_IT대학_컴퓨터학부",
        "optional_elective_all",
    }
