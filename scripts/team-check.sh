#!/usr/bin/env bash
# team-check.sh — validate the SWE iterative team scaffold.
# Exits 0 if every required file exists with required sections.

set -euo pipefail

FAIL=0
fail() { echo "FAIL: $*" >&2; FAIL=1; }
pass() { echo "ok:   $*"; }

require_file() {
  local path="$1"
  if [[ -f "$path" ]]; then pass "$path exists"; else fail "$path missing"; fi
}

require_section() {
  local path="$1" pattern="$2"
  if [[ ! -f "$path" ]]; then fail "$path missing (cannot check section)"; return; fi
  if grep -Eq "$pattern" "$path"; then
    pass "$path matches /$pattern/"
  else
    fail "$path missing section /$pattern/"
  fi
}

# Directory layout
[[ -d .agent/roles ]]   || fail ".agent/roles/ missing"
[[ -d .agent/history ]] || fail ".agent/history/ missing"
[[ -f .agent/roles/.gitkeep ]]   || fail ".agent/roles/.gitkeep missing"
[[ -f .agent/history/.gitkeep ]] || fail ".agent/history/.gitkeep missing"

# Template
require_file   .agent/team-state.template.md
require_section .agent/team-state.template.md '^## Current'
require_section .agent/team-state.template.md '^### Brief'
require_section .agent/team-state.template.md '^### Attempt'
require_section .agent/team-state.template.md '^### Verdict'
require_section .agent/team-state.template.md '^### History'

# Role briefs — each must declare its model, what it owns, what it must-not, and the ping protocol.
for role in planner worker reviewer integrator; do
  f=".agent/roles/${role}.md"
  require_file "$f"
  require_section "$f" '^# Role:'
  require_section "$f" '^## Model:'
  require_section "$f" '^## Owns'
  require_section "$f" '^## Must not'
  require_section "$f" '^## Protocol'
done

# Scripts
require_file scripts/team-init.sh
[[ -x scripts/team-init.sh ]] || fail "scripts/team-init.sh not executable"

# Runbook
require_file docs/superpowers/runbooks/2026-04-18-team-spawn.md
require_section docs/superpowers/runbooks/2026-04-18-team-spawn.md 'INTEGRATOR_DRY_RUN'

# gitignore patterns
require_section .gitignore '^\.agent/team-state\.md$'
require_section .gitignore '^\.agent/history/\*$'

if (( FAIL )); then
  echo "team-check: FAIL"
  exit 1
fi
echo "team-check: OK"
