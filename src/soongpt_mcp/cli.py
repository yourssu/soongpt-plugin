"""CLI entrypoints for soongpt-mcp.

- soongpt-mcp-login: interactive login flow that stores a rusaint session
  JSON into the OS keyring.
- soongpt-mcp: server launcher (delegates to the MCP server main; see
  __main__.py for the canonical entry).
"""
from __future__ import annotations

import asyncio
import getpass
import inspect
import sys

from .auth import AuthError, save_session


def _mask_student_id(student_id: str) -> str:
    if not student_id:
        return "<empty>"
    if len(student_id) <= 4:
        return "*" * len(student_id)
    return f"{student_id[:4]}{'*' * (len(student_id) - 4)}"


async def _build_session(student_id: str, password: str):
    from rusaint import USaintSessionBuilder

    builder = USaintSessionBuilder()
    method = builder.with_password
    if inspect.iscoroutinefunction(method):
        return await method(student_id, password)
    return method(student_id, password)


def login_main() -> None:
    print("soongpt-mcp login")
    print("Enter your Soongsil student ID and uSaint password.")
    print("Password is used only in-memory to build a session JSON,")
    print("then discarded. It is never written to disk.")
    print()

    try:
        student_id = input("Student ID: ").strip()
        password = getpass.getpass("uSaint Password: ")
    except (EOFError, KeyboardInterrupt):
        print()
        print("Aborted.")
        sys.exit(1)

    if not student_id or not password:
        print("Error: student ID and password are required.")
        sys.exit(1)

    print(f"Authenticating as { _mask_student_id(student_id) } ...")

    try:
        session = asyncio.run(_build_session(student_id, password))
    except Exception as exc:
        print(f"Login failed: {exc}")
        del password
        sys.exit(1)

    try:
        session_json = session.to_json()
    except Exception as exc:
        print(f"Failed to serialize session: {exc}")
        del password
        sys.exit(1)

    del password

    try:
        save_session(session_json)
    except AuthError as exc:
        print(f"Login succeeded but session could not be saved: {exc}")
        sys.exit(1)

    print("Login successful. Session saved to OS keyring.")
    print('Run "soongpt-mcp" to start the MCP server.')


def main() -> None:
    from .__main__ import main as server_main

    server_main()


if __name__ == "__main__":
    login_main()
