"""cli.py/pyproject.toml 메타 테스트: soongpt-mcp-login 명령어가 제거되었는지 확인."""
from __future__ import annotations

import pathlib

import tomllib


def test_login_main_removed_from_cli_module() -> None:
    from soongpt_mcp import cli

    assert not hasattr(cli, "login_main"), "login_main은 폐지됨"


def test_main_function_present() -> None:
    from soongpt_mcp import cli

    assert callable(cli.main)


def test_pyproject_no_login_script_entry() -> None:
    root = pathlib.Path(__file__).resolve().parent.parent
    with (root / "pyproject.toml").open("rb") as f:
        data = tomllib.load(f)
    scripts = data["project"]["scripts"]
    assert "soongpt-mcp-login" not in scripts
    assert "soongpt-mcp" in scripts
