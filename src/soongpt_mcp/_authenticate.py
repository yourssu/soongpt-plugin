"""rusaint 인증 래퍼.

CLI/웹 로그인/세션 매니저 모두 같은 함수를 사용하도록 분리.
학번/비밀번호는 이 함수 안에서만 사용되고, 호출자가 미리 `del` 해야 함.
"""
from __future__ import annotations

from rusaint import USaintSessionBuilder


class AuthenticateError(RuntimeError):
    """rusaint 인증 실패 (잘못된 학번/비밀번호, 네트워크 오류 등)."""


async def authenticate(student_id: str, password: str) -> str:
    """rusaint로 SSO 인증 후 세션 JSON 문자열 반환.

    학번/비밀번호는 rusaint에 전달 후 이 함수 프레임에서 사라지며,
    반환값은 세션 JSON(쿠키 묶음)만 포함. 학번/비밀번호 미포함.
    """
    if not student_id or not password:
        raise AuthenticateError("학번과 비밀번호는 필수입니다.")
    try:
        builder = USaintSessionBuilder()
        session = await builder.with_password(student_id, password)
    except Exception as exc:
        raise AuthenticateError(f"rusaint 인증 실패: {exc}") from exc
    try:
        return session.to_json()
    except Exception as exc:
        raise AuthenticateError(f"세션 직렬화 실패: {exc}") from exc
