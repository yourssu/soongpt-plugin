"""
rusaint-service 커스텀 예외.

에러 원인을 명확히 구분하여 라우터에서 적절한 HTTP 상태코드를 반환할 수 있도록 합니다.
"""


class SSOTokenError(Exception):
    """SSO 토큰 만료 또는 무효 (숭실대 서버가 거부)."""
    pass


class RusaintConnectionError(Exception):
    """숭실대 유세인트 서버 연결 실패 (네트워크, DNS, SSL 등)."""
    pass


class RusaintTimeoutError(Exception):
    """숭실대 유세인트 서버 응답 시간 초과."""
    pass


class RusaintInternalError(Exception):
    """rusaint 라이브러리 내부 오류."""
    pass


# 세션 만료로 분류할 rusaint 오류 메시지 마커.
# 만료된 세션으로 첫 요청 시 유세인트 서버가 로그인 페이지를 반환하고,
# rusaint가 기대한 SSR 폼을 찾지 못해 아래 파싱 오류를 일으킨다.
# 아래 문자열은 pinned 버전 rusaint 0.16.3의 Rust 코어 메시지로,
# rusaint 업그레이드 시 마커 재검증이 필요하다.
SESSION_EXPIRY_MARKERS = (
    "Cannot find SSR Client form",
)

# 미개설 과목으로 분류할 rusaint 오류 메시지 마커.
# 해당 학기에 개설되지 않은 강의(과목)를 조회하면 rusaint가 "No lecture found"를
# 던진다. 조회 실패가 아니라 정상적인 빈 결과 조건이므로, 세션/네트워크 실패와
# 달리 예외로 전파하지 않고 빈 결과(count: 0)로 변환한다 (SPR-79).
# 아래 문자열은 pinned 버전 rusaint 0.16.3의 Rust 코어 메시지로,
# rusaint 업그레이드 시 마커 재검증이 필요하다.
NO_LECTURE_MARKERS = (
    "No lecture found",
)


def _chain_has_marker(exc: Exception, markers: tuple[str, ...]) -> bool:
    """예외(와 원인/컨텍스트 체인)에 마커 중 하나가 들어있는지 판별.

    래핑이 `raise X from e` 없이 이뤄지는 경우를 위해 __context__도 탐색한다.
    """
    seen: set[int] = set()
    current: Exception | None = exc
    while current is not None:
        if id(current) in seen:
            break  # 체인 사이클 방어 (통상 발생하지 않음)
        seen.add(id(current))
        if any(marker in str(current) for marker in markers):
            return True
        current = current.__cause__ or current.__context__
    return False


def is_session_expiry_error(exc: Exception) -> bool:
    """예외(와 원인/컨텍스트 체인)가 세션 만료 신호인지 판별.

    세션 만료/무효 시 첫 실제 요청(Application 생성/조회)이 로그인 페이지를
    받아 파싱에 실패한다. 이 신호를 감지해 재로그인 경로로 보내야 하므로,
    RusaintInternalError 등으로 래핑된 예외의 메시지와 체인을 함께 확인한다.
    """
    return _chain_has_marker(exc, SESSION_EXPIRY_MARKERS)


def is_no_lecture_error(exc: Exception) -> bool:
    """예외(와 원인/컨텍스트 체인)가 미개설 과목 신호인지 판별.

    해당 학기에 개설되지 않은 강의를 조회하면 rusaint가 "No lecture found"를
    던진다. 세션/네트워크 실패와 달리 정상적인 빈 결과 조건이므로, 이 신호는
    예외로 전파하지 않고 빈 결과(count: 0)로 변환한다.
    """
    return _chain_has_marker(exc, NO_LECTURE_MARKERS)
