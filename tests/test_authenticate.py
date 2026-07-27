"""_authenticate.authenticate 단위 테스트."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from soongpt_mcp._authenticate import AuthenticateError, authenticate


@pytest.mark.asyncio
async def test_authenticate_success_returns_session_json() -> None:
    fake_session = MagicMock()
    fake_session.to_json.return_value = '{"cookies": ["a", "b"]}'
    builder = MagicMock()
    builder.with_password = AsyncMock(return_value=fake_session)

    with patch("soongpt_mcp._authenticate.USaintSessionBuilder", return_value=builder):
        result = await authenticate("20210001", "pw123")

    assert result == '{"cookies": ["a", "b"]}'
    builder.with_password.assert_awaited_once_with("20210001", "pw123")


@pytest.mark.asyncio
@pytest.mark.parametrize("student_id,password", [("", "pw"), ("id", "")])
async def test_authenticate_rejects_empty_credentials(
    student_id: str, password: str
) -> None:
    with pytest.raises(AuthenticateError):
        await authenticate(student_id, password)


@pytest.mark.asyncio
async def test_authenticate_wraps_rusaint_errors() -> None:
    builder = MagicMock()
    builder.with_password = AsyncMock(side_effect=RuntimeError("network down"))

    with (
        patch("soongpt_mcp._authenticate.USaintSessionBuilder", return_value=builder),
        pytest.raises(AuthenticateError, match="rusaint 인증 실패"),
    ):
        await authenticate("20210001", "pw")


@pytest.mark.asyncio
async def test_authenticate_wraps_serialization_errors() -> None:
    fake_session = MagicMock()
    fake_session.to_json.side_effect = RuntimeError("boom")
    builder = MagicMock()
    builder.with_password = AsyncMock(return_value=fake_session)

    with (
        patch("soongpt_mcp._authenticate.USaintSessionBuilder", return_value=builder),
        pytest.raises(AuthenticateError, match="세션 직렬화 실패"),
    ):
        await authenticate("20210001", "pw")
