"""
rusaint-service 설정 호환 레이어.

soongpt-backend의 app.core.config.settings를 대체하기 위한 최소한의 상수 모듈.
rusaint_service 코드를 그대로 옮겨오면서 settings.xxx 참조를 지원하기 위해 존재합니다.
"""

from typing import Set


class _Settings:
    """rusaint_service가 사용하던 설정의 기본값. MCP 서버에서는 환경 변수를 사용하지 않고 상수로 고정."""

    rusaint_timeout: int = 30
    pseudonym_secret: str = ""  # MCP에서는 pseudonym을 사용하지 않음 (외부 서비스 담당)
    FAIL_GRADE: str = "F"
    LOW_GRADE_RANKS: Set[str] = {"C+", "C0", "C-", "D+", "D0", "D-"}


settings = _Settings()
