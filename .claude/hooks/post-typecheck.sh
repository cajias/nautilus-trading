#!/usr/bin/env bash
# Stage:    PostToolUse
# Function: Type-check edited .py files (mypy), scoped to production code.
#
# Only runs on files under strategies/ or nautilus/src/ — skipping tests/,
# notebooks, and ad-hoc scripts keeps latency reasonable and noise low.
# Non-blocking: errors are reported via additionalContext; the tool call
# has already succeeded. --follow-imports=silent avoids cascading errors
# from third-party stubs.
set -uo pipefail

payload=$(cat)
file=$(printf '%s' "$payload" | jq -r '.tool_response.filePath // .tool_input.file_path // empty')

[ -z "$file" ] && exit 0
[[ "$file" != *.py ]] && exit 0
[ -f "$file" ] || exit 0

PROJECT_ROOT="/Users/rc/Projects/workspace/nautilus-trading"
UV_DIR="$PROJECT_ROOT/nautilus"

case "$file" in
  "$PROJECT_ROOT"/strategies/*) : ;;
  "$PROJECT_ROOT"/nautilus/src/*) : ;;
  *) exit 0 ;;
esac

[ -d "$UV_DIR" ] || exit 0
cd "$UV_DIR" || exit 0

out=$(uv run --quiet mypy --follow-imports=silent --no-error-summary --no-pretty "$file" 2>&1 || true)

if printf '%s' "$out" | grep -qE ': error:'; then
  jq -n --arg msg "mypy reported type errors in $file:
$out" '{
    hookSpecificOutput: {
      hookEventName: "PostToolUse",
      additionalContext: $msg
    }
  }'
fi
exit 0
