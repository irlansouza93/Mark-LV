"""PostToolUse hook: syntax-check a .py file right after Claude edits it.

CLAUDE.md asks for `py_compile` on everything that changed; doing it here means a
syntax error comes back on the same turn instead of at the end of the task. It
compiles in memory (no __pycache__ written) and only looks at the edited file,
so it stays well under a second even for ui.py (~6k lines).

Exit 2 sends stderr back to Claude; any other problem exits 0 so a hook bug never
blocks work.
"""
import json
import sys
from pathlib import Path


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    path = (payload.get("tool_input") or {}).get("file_path") or ""
    if not path.endswith(".py"):
        return 0
    p = Path(path)
    if not p.is_file():
        return 0
    try:
        compile(p.read_text(encoding="utf-8"), str(p), "exec")
    except SyntaxError as e:
        print(f"py_compile failed: {p}:{e.lineno}: {e.msg}", file=sys.stderr)
        return 2
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
