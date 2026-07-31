#!/usr/bin/env bash
# Bootstraps an isolated venv under ${CLAUDE_PLUGIN_DATA} on first run (or when
# the plugin's dependencies or its own source change), then execs the MCP server.
#
# stdout is reserved for the MCP stdio protocol once the server starts, so every
# bootstrap message below is redirected to a log file instead.
set -euo pipefail

ROOT="${CLAUDE_PLUGIN_ROOT}"
DATA="${CLAUDE_PLUGIN_DATA}"
VENV="${DATA}/venv"
STAMP="${DATA}/source.sha256"
LOCK_DIR="${DATA}/bootstrap.lock"
LOG="${DATA}/bootstrap.log"

mkdir -p "$DATA"

PYTHON_BIN="$(command -v python3 || command -v python || true)"
if [ -z "$PYTHON_BIN" ]; then
  echo "[soongpt-mcp] no python3 or python found on PATH" >>"$LOG" 2>&1
  exit 1
fi

# venv layout differs by platform (POSIX: bin/, native Windows: Scripts/); a
# Git Bash shell on Windows still creates a Scripts/ venv, so check both.
venv_bin() {
  local name="$1"
  if [ -x "$VENV/bin/$name" ]; then
    printf '%s' "$VENV/bin/$name"
  elif [ -x "$VENV/Scripts/$name.exe" ]; then
    printf '%s' "$VENV/Scripts/$name.exe"
  fi
}

# Hashes pyproject.toml plus every file under src/soongpt_mcp, so a source or
# bundled-data change (not just a dependency bump) also invalidates the cache.
hash_source() {
  "$PYTHON_BIN" - "$ROOT" <<'PYEOF'
import hashlib
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
pkg = root / "src" / "soongpt_mcp"
paths = [root / "pyproject.toml"] + sorted(
    p for p in pkg.rglob("*") if p.is_file() and "__pycache__" not in p.parts
)
h = hashlib.sha256()
for f in paths:
    h.update(str(f.relative_to(root)).encode())
    h.update(f.read_bytes())
print(h.hexdigest())
PYEOF
}

# mkdir is atomic on all POSIX filesystems, so it doubles as a portable lock:
# guards concurrent launches (e.g. two sessions connecting at once) from
# racing on the same venv. Held only for the bootstrap phase below, released
# before exec since exec replaces this process without running EXIT traps.
acquire_lock() {
  local waited=0
  while ! mkdir "$LOCK_DIR" 2>/dev/null; do
    local holder_pid
    holder_pid="$(cat "$LOCK_DIR/pid" 2>/dev/null || true)"
    if [ -n "$holder_pid" ] && ! kill -0 "$holder_pid" 2>/dev/null; then
      rm -rf "$LOCK_DIR"
      continue
    fi
    if [ "$waited" -ge 60 ]; then
      echo "[soongpt-mcp] timed out waiting for bootstrap lock (held by pid ${holder_pid:-unknown})"
      return 1
    fi
    sleep 1
    waited=$((waited + 1))
  done
  echo "$$" >"$LOCK_DIR/pid"
}

release_lock() {
  rm -rf "$LOCK_DIR"
}

{
  if acquire_lock; then
    CURRENT_HASH="$(hash_source)"
    if [ -z "$(venv_bin python)" ] || [ "$(cat "$STAMP" 2>/dev/null || true)" != "$CURRENT_HASH" ]; then
      echo "[soongpt-mcp] installing dependencies into ${VENV}"
      rm -rf "$VENV"
      "$PYTHON_BIN" -m venv "$VENV"
      if "$(venv_bin pip)" install --quiet --disable-pip-version-check "$ROOT"; then
        echo "$CURRENT_HASH" >"$STAMP"
        echo "[soongpt-mcp] install complete"
      else
        echo "[soongpt-mcp] install failed, will retry on next launch"
        rm -rf "$VENV" "$STAMP"
        release_lock
        exit 1
      fi
    fi
    release_lock
  else
    exit 1
  fi
} >>"$LOG" 2>&1

exec "$(venv_bin python)" -m soongpt_mcp
