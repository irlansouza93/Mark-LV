#!/usr/bin/env bash
# Spec Kit was initialised with `--script ps`: every /speckit-* skill calls
# .specify/scripts/powershell/*.ps1. PowerShell 7 (pwsh) runs those on Linux and
# macOS too, but cloud sessions start without it, so install it there.
# Local machines (Windows has pwsh/powershell already) are left untouched.
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

if command -v pwsh >/dev/null 2>&1; then
  exit 0
fi

PWSH_VERSION="7.4.6"
case "$(uname -m)" in
  x86_64) ARCH="x64" ;;
  aarch64 | arm64) ARCH="arm64" ;;
  *) echo "session-start: no pwsh build for $(uname -m); /speckit-* scripts will not run" >&2; exit 0 ;;
esac

DEST="$HOME/.local/pwsh"
mkdir -p "$DEST" "$HOME/.local/bin"
curl -fsSL "https://github.com/PowerShell/PowerShell/releases/download/v${PWSH_VERSION}/powershell-${PWSH_VERSION}-linux-${ARCH}.tar.gz" \
  | tar -xz -C "$DEST"
chmod +x "$DEST/pwsh"
ln -sf "$DEST/pwsh" "$HOME/.local/bin/pwsh"

# Hooks cannot change the parent shell's PATH; CLAUDE_ENV_FILE is how a
# SessionStart hook exports variables to later Bash calls.
if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
  echo "export PATH=\"$HOME/.local/bin:\$PATH\"" >> "$CLAUDE_ENV_FILE"
fi
