#!/usr/bin/env bash
# Bootstraps an isolated venv under ${CLAUDE_PLUGIN_DATA} on first run (or when
# pyproject.toml changes across a plugin update), then execs the MCP server.
#
# stdout is reserved for the MCP stdio protocol once the server starts, so every
# bootstrap message below is redirected to a log file instead.
set -euo pipefail

ROOT="${CLAUDE_PLUGIN_ROOT}"
DATA="${CLAUDE_PLUGIN_DATA}"
VENV="${DATA}/venv"
STAMP="${DATA}/pyproject.toml"
LOG="${DATA}/bootstrap.log"

mkdir -p "$DATA"

{
  if ! diff -q "${ROOT}/pyproject.toml" "$STAMP" >/dev/null 2>&1; then
    echo "[soongpt-mcp] installing dependencies into ${VENV}"
    rm -rf "$VENV"
    PYTHON_BIN="$(command -v python3 || command -v python)"
    "$PYTHON_BIN" -m venv "$VENV"
    if "$VENV/bin/pip" install --quiet --disable-pip-version-check "$ROOT"; then
      cp "${ROOT}/pyproject.toml" "$STAMP"
      echo "[soongpt-mcp] install complete"
    else
      echo "[soongpt-mcp] install failed, will retry on next launch"
      rm -rf "$VENV" "$STAMP"
      exit 1
    fi
  fi
} >>"$LOG" 2>&1

exec "$VENV/bin/python" -m soongpt_mcp
