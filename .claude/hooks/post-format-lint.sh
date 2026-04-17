#!/usr/bin/env bash
# Stage:    PostToolUse
# Function: Auto-format + auto-fix lint on edited .py files (ruff).
#
# Runs inside nautilus/ (where pyproject.toml lives) so ruff picks up the
# project config: line-length 100, py312 target, *.ipynb excluded.
# Non-blocking: any issues ruff cannot auto-fix are reported back to Claude
# via additionalContext so it can address them on the next turn.
set -uo pipefail

payload=$(cat)
file=$(printf '%s' "$payload" | jq -r '.tool_response.filePath // .tool_input.file_path // empty')

[ -z "$file" ] && exit 0
[[ "$file" != *.py ]] && exit 0
[ -f "$file" ] || exit 0

PROJECT_ROOT="/Users/rc/Projects/workspace/nautilus-trading"
UV_DIR="$PROJECT_ROOT/nautilus"
[ -d "$UV_DIR" ] || exit 0

cd "$UV_DIR" || exit 0

# 1. Format (safe, idempotent)
uv run --quiet ruff format "$file" >/dev/null 2>&1 || true

# 2. Auto-fix what's safe, then surface what remains
uv run --quiet ruff check --fix --exit-zero "$file" >/dev/null 2>&1 || true
remaining=$(uv run --quiet ruff check --no-fix "$file" 2>&1 || true)

if printf '%s' "$remaining" | grep -qE '^[^[:space:]].*:\s*[A-Z][0-9]+'; then
  jq -n --arg msg "ruff left unfixed issues in $file (run 'make lint' for full view):
$remaining" '{
    hookSpecificOutput: {
      hookEventName: "PostToolUse",
      additionalContext: $msg
    }
  }'
fi
exit 0
