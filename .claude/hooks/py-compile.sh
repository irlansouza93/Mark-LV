#!/usr/bin/env bash
# Runs py_compile_hook.py with the project's own interpreter (.venv on Windows or
# macOS/Linux), falling back to whatever python is on PATH in a fresh clone.
ROOT="${CLAUDE_PROJECT_DIR:-$(pwd)}"
for PY in "$ROOT/.venv/Scripts/python.exe" "$ROOT/.venv/bin/python"; do
  if [ -x "$PY" ]; then
    exec "$PY" "$ROOT/.claude/hooks/py_compile_hook.py"
  fi
done
for PY in python3 python; do
  if command -v "$PY" >/dev/null 2>&1; then
    exec "$PY" "$ROOT/.claude/hooks/py_compile_hook.py"
  fi
done
exit 0
