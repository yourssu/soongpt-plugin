"""CLI entrypoints for soongpt-mcp.

- soongpt-mcp: MCP 서버 실행 (stdin/stdout JSON-RPC). 최초 툴 호출 시
  자동으로 localhost 웹 브라우저 로그인 플로우가 트리거됩니다.
  별도의 로그인 명령어는 필요 없습니다.
"""
from __future__ import annotations


def main() -> None:
    from .__main__ import main as server_main

    server_main()


if __name__ == "__main__":
    main()
