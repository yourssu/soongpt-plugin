"""Module entrypoint for `python -m soongpt_mcp` and the soongpt-mcp script."""
from __future__ import annotations


def main() -> None:
    try:
        from .server import run
    except ImportError as exc:
        raise SystemExit(f"MCP server module is not available: {exc}.")
    run()


if __name__ == "__main__":
    main()
