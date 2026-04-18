#!/usr/bin/env bash
# team-init.sh — bootstrap the runtime scratchpad for the SWE iterative team.
# Idempotent: refuses to clobber an existing .agent/team-state.md.

set -euo pipefail

TEMPLATE=.agent/team-state.template.md
STATE=.agent/team-state.md
HISTORY=.agent/history

if [[ ! -f "$TEMPLATE" ]]; then
  echo "team-init: $TEMPLATE missing — run scripts/team-check.sh first" >&2
  exit 1
fi

mkdir -p "$HISTORY"

if [[ -f "$STATE" ]]; then
  echo "team-init: $STATE already exists; refusing to clobber." >&2
  echo "           If you intended a fresh start, rotate it into $HISTORY/ first." >&2
  exit 1
fi

cp "$TEMPLATE" "$STATE"
echo "team-init: initialized $STATE from template."
echo "team-init: history dir: $HISTORY/"
