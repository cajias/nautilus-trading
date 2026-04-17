#!/usr/bin/env bash
# Stage:    PreToolUse
# Function: Guard — block Write/Edit/MultiEdit on secrets & lockfiles.
#
# Reads the Claude Code hook JSON on stdin. If the target file_path is a
# secret (*.pem, .env, .env.<stage>) or the uv lockfile, the hook emits a
# deny decision so Claude never touches it.
#
# Explicitly ALLOWED: .env.example (checked-in template) and .envrc
# (direnv, user-managed).
set -uo pipefail

payload=$(cat)
file=$(printf '%s' "$payload" | jq -r '.tool_input.file_path // empty')

# MultiEdit still carries .tool_input.file_path (one target per call), so
# the same extraction works for Write, Edit, and MultiEdit.
[ -z "$file" ] && exit 0

base=$(basename -- "$file")

block=""
case "$base" in
  .env.example|.envrc) : ;;                               # allow
  *.pem)               block="private-key file" ;;
  .env|.env.local|.env.production|.env.staging|.env.development|.env.test)
                       block="environment file (may contain secrets)" ;;
  uv.lock)             block="regenerated via 'uv sync', do not hand-edit" ;;
esac

[ -z "$block" ] && exit 0

jq -n --arg reason "Refusing to Write/Edit $base — $block." '{
  hookSpecificOutput: {
    hookEventName: "PreToolUse",
    permissionDecision: "deny",
    permissionDecisionReason: $reason
  }
}'
