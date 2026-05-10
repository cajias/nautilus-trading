# SWE Iterative Team Implementation Plan

> **Status (retro-ticked 2026-04-21):** This plan was executed ad-hoc without ticking boxes in real time. Checkboxes below have been verified against HEAD and updated to reflect actual on-disk state. See `.agent/` directory + `.agent/roles/*.md` for the scaffold.
>
> Tasks 1–9 (scaffold) are fully executed and verified (`scripts/team-check.sh` exits 0; all 4 role briefs, template, init script, and runbook present). Task 10 (dry-run validation) ran through Steps 1–5 but the dry-run **FAILED** — see `docs/superpowers/audits/2026-04-18-team-dryrun-FAILED.md`; the "validated" marker (Step 6) and Integrator flip-to-live (Step 7) never landed as prescribed. Task 11 (one-atomic-PR for the scaffold) was never opened; scaffold instead shipped across the sub-project A PR chain.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Scaffold a four-role tmux-backed agent team (Planner/Worker/Reviewer/Integrator) that autonomously executes sub-project A's 8-PR implementation plan via Google's iterative multi-agent pattern.

**Architecture:** Committed role briefs under `.agent/roles/` + a gitignored shared scratchpad (`.agent/team-state.md`) are the entire runtime contract. A bash validator (`scripts/team-check.sh`) written before any role brief enforces TDD-style RED→GREEN as the scaffold fills in. Team is spawned via `TeamCreate` in the main session following a committed runbook; a human dry-run gate validates end-to-end on Task 1.1 of sub-project A before Integrator flips from `DRY_RUN=1` to live commits.

**Tech Stack:** Bash 5 + POSIX `grep`, Markdown role briefs, Claude Code `TeamCreate` (tmux), `SendMessage` pings, `.gitignore`. No Python added; no new runtime deps.

**Spec:** `docs/superpowers/specs/2026-04-17-swe-iterative-team-design.md` (APPROVED, 2026-04-17).

**Branch convention.** This plan's work lands on the current branch `design/subproject-a-v2` (same branch the spec was committed on). One commit per task. At plan end, open a single PR consolidating the scaffold.

**Global commands.** All shell commands run from the repo root `/Users/rc/Projects/workspace/nautilus-trading/.claude/worktrees/subproject-a-resume/`.

---

## Task 1 — `.gitignore` and `.agent/` directory layout

**Files:**
- Modify: `.gitignore`
- Create: `.agent/roles/.gitkeep`
- Create: `.agent/history/.gitkeep`

- [x] **Step 1: Read the current `.gitignore`**

Run: `cat .gitignore`

Note what patterns already exist. We will append; do not clobber.

- [x] **Step 2: Append team-state patterns**

Append these lines to `.gitignore` (preserve any trailing newline):

```gitignore

# SWE iterative team runtime state (per-worktree, never committed)
.agent/team-state.md
.agent/history/
```

- [x] **Step 3: Create empty tracked directories**

```bash
mkdir -p .agent/roles .agent/history
touch .agent/roles/.gitkeep .agent/history/.gitkeep
```

- [x] **Step 4: Verify gitignore patterns match what we'll create**

Run:
```bash
git check-ignore -v .agent/team-state.md .agent/history/anything.md
```
Expected: both paths print a matching pattern from `.gitignore`.

Run:
```bash
git check-ignore -v .agent/roles/planner.md .agent/roles/.gitkeep .agent/history/.gitkeep
```
Expected: **none of these** are ignored (exits non-zero with no output). `.gitkeep` inside `.agent/history/` is allowed because `git check-ignore` honors that directories can have tracked files even under a trailing-slash pattern — but belt-and-braces: if this fails, add `!.agent/history/.gitkeep` as a negation line.

- [x] **Step 5: Commit**

```bash
git add .gitignore .agent/roles/.gitkeep .agent/history/.gitkeep
git commit -m "chore: add .agent/ layout and gitignore for team runtime state"
```

---

## Task 2 — Write `scripts/team-check.sh` (the RED test harness)

**Files:**
- Create: `scripts/team-check.sh`

This script exits non-zero until every scaffold piece is in place. It is written first so subsequent tasks have a mechanical gate.

- [x] **Step 1: Create the script**

```bash
mkdir -p scripts
```

Create `scripts/team-check.sh` with exactly this content:

```bash
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
require_section .gitignore '^\.agent/history/$'

if (( FAIL )); then
  echo "team-check: FAIL"
  exit 1
fi
echo "team-check: OK"
```

- [x] **Step 2: Make it executable**

```bash
chmod +x scripts/team-check.sh
```

- [x] **Step 3: Run it — expect FAIL**

Run: `scripts/team-check.sh || true`

Expected: multiple `FAIL:` lines (template missing, all 4 role briefs missing, team-init.sh missing, runbook missing) and final `team-check: FAIL`. The `|| true` is only so set-e in your shell doesn't interrupt; the script itself exits 1.

- [x] **Step 4: Commit**

```bash
git add scripts/team-check.sh
git commit -m "chore: add team-check.sh scaffold validator (RED)"
```

---

## Task 3 — Scratchpad template

**Files:**
- Create: `.agent/team-state.template.md`

- [x] **Step 1: Write the template**

Create `.agent/team-state.template.md` with exactly this content:

```markdown
## Current: <task-id> — <task-title>
PR: <N>/8 · Attempt: 0 · Assigned: Planner · Updated: <ISO-8601 UTC>

### Brief
_Planner writes the task brief here: files to touch, step list, acceptance criteria. Pings Worker when done._

### Attempt
_Worker writes attempt records here (append-only). Each record: timestamp, files changed, test command output summary. Pings Reviewer when attempt is ready._

### Verdict
_Reviewer writes `ACCEPT` or `REJECT` here with reasons. On REJECT with Attempt < 2, pings Worker for retry. On REJECT with Attempt == 2, pings Planner to escalate. On ACCEPT, pings Integrator._

### History
_Integrator appends a one-line record after each ACCEPT rotation: `- <task-id> — ACCEPT on attempt <N> — commit <short-sha>`._
```

- [x] **Step 2: Run team-check — expect progress**

Run: `scripts/team-check.sh || true`

Expected: `ok: .agent/team-state.template.md exists` plus all five section matches; still FAILs on role briefs and team-init.sh.

- [x] **Step 3: Commit**

```bash
git add .agent/team-state.template.md
git commit -m "feat: add team scratchpad template"
```

---

## Task 4 — Planner role brief

**Files:**
- Create: `.agent/roles/planner.md`

- [x] **Step 1: Write the brief**

Create `.agent/roles/planner.md` with exactly this content:

```markdown
# Role: Planner

## Model: Opus

You are the Planner for the SWE iterative team executing
`docs/superpowers/plans/2026-04-17-subproject-a-implementation.md`.

## Owns
- Reading the plan doc and picking the next unchecked task (first `- [ ]` under the lowest-numbered Task heading whose predecessors are all ACCEPTed).
- Writing the task **Brief** section of `.agent/team-state.md`: list of files the task touches, the exact step checkboxes from the plan, and acceptance criteria copied from the plan.
- On escalation from Reviewer (attempt 2 REJECT), either re-decomposing the task into smaller Briefs (and resetting the attempt counter) or writing `blocker: human-needed` with a specific question.
- Picking up at the top of the next PR when main sends "PR N merged, begin PR N+1".

## Must not
- Edit source code. (You may update `.agent/team-state.md` and your own role brief; nothing else.)
- Tick plan checkboxes. Only Integrator does that, after Reviewer ACCEPTs.
- Advance past a task that is still `Assigned: Worker` or `Assigned: Reviewer`.

## Protocol
1. Read `.agent/team-state.md`. If `Current:` is empty or `Assigned: Planner`, proceed. Otherwise wait for a ping.
2. Open the plan at `docs/superpowers/plans/2026-04-17-subproject-a-implementation.md` and find the next unchecked task.
3. Overwrite the `## Current` line with `<task-id> — <task-title>` and set `Assigned: Worker`, `Attempt: 1`, `Updated: <now>`.
4. Fill the `### Brief` section. Be literal: paste the step checkboxes from the plan verbatim so Worker cannot drift.
5. Append `(Planner · <timestamp>)` to your Brief entry.
6. `SendMessage(to=worker, body="your turn — see .agent/team-state.md")`.
7. Stop. Wait for next ping (escalation or PR-boundary nudge).
```

- [x] **Step 2: Run team-check — expect progress**

Run: `scripts/team-check.sh || true`

Expected: `ok` lines for `.agent/roles/planner.md` and its five sections; still FAILs on the other three role briefs + team-init.sh + runbook.

- [x] **Step 3: Commit**

```bash
git add .agent/roles/planner.md
git commit -m "feat: add Planner role brief"
```

---

## Task 5 — Worker role brief

**Files:**
- Create: `.agent/roles/worker.md`

- [x] **Step 1: Write the brief**

Create `.agent/roles/worker.md` with exactly this content:

```markdown
# Role: Worker

## Model: Sonnet

You are the Worker for the SWE iterative team. You implement one task at a time, test-first (red → green → refactor), per the Brief in `.agent/team-state.md`.

## Owns
- Writing and editing production source files to satisfy the Brief's step checkboxes.
- Running the task's failing test first, then the minimal implementation, then confirming green.
- Appending an `### Attempt <N>` entry to `.agent/team-state.md` with: files changed (one per line), the exact test command run, and a terse summary of the result.

## Must not
- `git commit`, `git push`, or open PRs. (Integrator owns git operations entirely.)
- Tick plan checkboxes.
- Skip writing the failing test first. TDD discipline is non-negotiable — the plan assumes it.
- Modify files outside the Brief's file list without updating the Brief (which requires pinging Planner).

## Protocol
1. On ping, read `.agent/team-state.md`. Confirm `Assigned: Worker` and your attempt number matches `Attempt:`.
2. For each `- [ ]` step in the Brief, do it exactly. If the plan shows test code, paste it verbatim.
3. After the last implementation step and a green test run, append `### Attempt <N> (Worker · <timestamp>)` with files changed and a 1-line test summary.
4. Update the header: `Assigned: Reviewer`, `Updated: <now>`. Do not change `Attempt:`.
5. `SendMessage(to=reviewer, body="your turn — see .agent/team-state.md")`.
6. Stop. Wait for next ping (REJECT → retry, or next Brief from Planner).
```

- [x] **Step 2: Run team-check — expect progress**

Run: `scripts/team-check.sh || true`

Expected: `ok` for `planner.md` and `worker.md`; FAILs remain for reviewer/integrator briefs + team-init.sh + runbook.

- [x] **Step 3: Commit**

```bash
git add .agent/roles/worker.md
git commit -m "feat: add Worker role brief"
```

---

## Task 6 — Reviewer role brief

**Files:**
- Create: `.agent/roles/reviewer.md`

- [x] **Step 1: Write the brief**

Create `.agent/roles/reviewer.md` with exactly this content:

```markdown
# Role: Reviewer

## Model: Sonnet

You are the Reviewer. You are the critic in the iterative loop. You do not edit code; you judge it.

## Owns
- Running the quality gate: `cd nautilus && uv run make lint test-unit` (or the exact command the Brief specifies).
- Reading the Worker's Attempt entry and diffing files changed against the Brief's file list. Flag any file touched that the Brief did not authorize.
- Writing the `### Verdict` section: either `ACCEPT` or `REJECT` plus reasons. On REJECT, include the exact gate output or the exact discrepancy.
- Deciding routing: ACCEPT → ping Integrator; REJECT with Attempt < 2 → ping Worker; REJECT with Attempt == 2 → ping Planner.

## Must not
- Edit any source file. (You may edit `.agent/team-state.md` and your own role brief.)
- Run `git commit` or open PRs.
- ACCEPT when the gate command is not green. No exceptions.

## Protocol
1. On ping, read `.agent/team-state.md`. Confirm `Assigned: Reviewer`.
2. Run the gate command. Capture stdout+stderr tail (last ~40 lines is enough).
3. If gate FAILS: write `### Verdict (Reviewer · <timestamp>) — REJECT`. Include the captured output. Set `Assigned: Worker` (or `Planner` if `Attempt: 2`), increment `Attempt:` if routing to Worker. Ping the chosen role.
4. If gate PASSES: diff the Brief's file list against `git status --short`. If they mismatch, REJECT with "Brief/diff mismatch: <list>". Otherwise write `### Verdict (Reviewer · <timestamp>) — ACCEPT`. Set `Assigned: Integrator`. Ping Integrator.
5. Stop.
```

- [x] **Step 2: Run team-check — expect progress**

Run: `scripts/team-check.sh || true`

Expected: `ok` for planner/worker/reviewer; FAILs on integrator + team-init.sh + runbook.

- [x] **Step 3: Commit**

```bash
git add .agent/roles/reviewer.md
git commit -m "feat: add Reviewer role brief"
```

---

## Task 7 — Integrator role brief

**Files:**
- Create: `.agent/roles/integrator.md`

- [x] **Step 1: Write the brief**

Create `.agent/roles/integrator.md` with exactly this content:

```markdown
# Role: Integrator

## Model: Haiku

You are the Integrator. You are mechanical — you do not judge correctness; you run the committed decisions.

## Owns
- On Reviewer ACCEPT: tick the plan's `- [ ]` boxes for the current task (edit `docs/superpowers/plans/2026-04-17-subproject-a-implementation.md` → `- [x]`) for every step in the Brief.
- `git add <files-touched> docs/superpowers/plans/2026-04-17-subproject-a-implementation.md`.
- `git commit -m "<conventional-commit message derived from Brief title>"`. If `INTEGRATOR_DRY_RUN=1` (see env-var handling below), **do not commit** — instead print the commit message + `git diff --stat --cached` into the scratchpad and stop before committing.
- On the final task of a PR (detectable by Brief's `PR-final: true` flag, or when the next task is under a new PR heading): `git push -u origin <branch>` and `gh pr create` with a body assembled from the PR heading in the plan doc.
- Rotating the scratchpad: after committing (or after dry-run print), move the completed `## Current` block's content into a fresh `.agent/history/task-<task-id>.md` file, then reset `.agent/team-state.md` from `.agent/team-state.template.md`.
- Ping Planner when done.

## Must not
- Judge whether the code is correct. Reviewer already said ACCEPT.
- Edit any file except: (a) the plan doc, strictly to tick `- [ ]` → `- [x]`; (b) `.agent/team-state.md`; (c) new files under `.agent/history/`. If you find yourself wanting to edit production source, STOP and ping Planner.
- Push to `main`, ever.

## Protocol
1. On ping, read `.agent/team-state.md`. Confirm `Assigned: Integrator` and `Verdict: ACCEPT`.
2. Read `INTEGRATOR_DRY_RUN` from your environment (default: `0`).
3. Tick plan checkboxes for every step in the Brief.
4. `git add` the Worker's files + the plan doc edits.
5. If `INTEGRATOR_DRY_RUN=1`: print the would-be commit message and `git diff --stat --cached` into an `### Integrator dry-run` section of the scratchpad. Skip steps 6–8. Then do step 9.
6. `git commit -m "<conventional message>"`. Capture the short SHA.
7. If this is the PR-final task: `git push -u origin <branch>` and `gh pr create --base main --head <branch> --title "<PR title>" --body-file <(...)`. Capture the PR URL.
8. Append `- <task-id> — ACCEPT on attempt <N> — commit <sha>[<PR URL>]` to the scratchpad's `### History`.
9. Rotate: `mv .agent/team-state.md .agent/history/task-<task-id>.md` logically (use your file tools), then copy `.agent/team-state.template.md` → `.agent/team-state.md`.
10. `SendMessage(to=planner, body="task <task-id> integrated — next, please")`.
11. Stop.

## Env var: `INTEGRATOR_DRY_RUN`
- `1` during initial validation. Prints would-be commit and rotates scratchpad but does not touch git.
- `0` (default) for live operation. Commits normally.
- The runbook documents how to toggle this between validation and live runs.
```

- [x] **Step 2: Run team-check — expect progress**

Run: `scripts/team-check.sh || true`

Expected: all four role briefs `ok`; FAILs only on `scripts/team-init.sh` + runbook.

- [x] **Step 3: Commit**

```bash
git add .agent/roles/integrator.md
git commit -m "feat: add Integrator role brief"
```

---

## Task 8 — `scripts/team-init.sh`

**Files:**
- Create: `scripts/team-init.sh`

- [x] **Step 1: Create the script**

Create `scripts/team-init.sh` with exactly this content:

```bash
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
```

- [x] **Step 2: Make it executable**

```bash
chmod +x scripts/team-init.sh
```

- [x] **Step 3: Run team-check — expect all ok except runbook**

Run: `scripts/team-check.sh || true`

Expected: every check passes except the runbook file and its `INTEGRATOR_DRY_RUN` section.

- [x] **Step 4: Run the init script and verify behavior**

```bash
scripts/team-init.sh
```
Expected: `team-init: initialized .agent/team-state.md from template.`

```bash
scripts/team-init.sh
```
Expected: `team-init: .agent/team-state.md already exists; refusing to clobber.` exit 1.

Clean up after the smoke test:
```bash
rm .agent/team-state.md
```
(`.agent/team-state.md` is gitignored so nothing to un-stage.)

- [x] **Step 5: Commit**

```bash
git add scripts/team-init.sh
git commit -m "feat: add team-init.sh scratchpad bootstrap"
```

---

## Task 9 — Spawn runbook

**Files:**
- Create: `docs/superpowers/runbooks/2026-04-18-team-spawn.md`

- [x] **Step 1: Create the runbook**

```bash
mkdir -p docs/superpowers/runbooks
```

Create `docs/superpowers/runbooks/2026-04-18-team-spawn.md` with exactly this content:

```markdown
# Runbook: Spawn the SWE Iterative Team

**When to use:** You are the main session and need to stand up the 4-role team to execute sub-project A's plan. Prerequisites: scaffold validated (`scripts/team-check.sh` exits 0), scratchpad initialized (`scripts/team-init.sh`).

## 1. Pre-flight

```bash
scripts/team-check.sh    # must exit 0
scripts/team-init.sh     # creates .agent/team-state.md from template (idempotent)
```

If `team-init.sh` refuses because `.agent/team-state.md` already exists and you intend a fresh start, archive it first:

```bash
mv .agent/team-state.md .agent/history/pre-respawn-$(date -u +%Y%m%dT%H%M%SZ).md
scripts/team-init.sh
```

## 2. Spawn via `TeamCreate`

From the main Claude session, call `TeamCreate` with the four teammates below. For each, the `prompt` is the contents of the corresponding `.agent/roles/<role>.md` file plus the two lines below prepended (which orient the teammate to its environment):

```
You are running in tmux team mode. The authoritative state is `.agent/team-state.md` in the repo root. You communicate with teammates only via `SendMessage(to=<role>, body="...")`. Start by reading `.agent/team-state.md` and waiting for your first ping, unless you are Planner — in which case, begin immediately.
```

| Teammate `name` | Model | Brief source |
|---|---|---|
| `planner`    | opus    | `.agent/roles/planner.md` |
| `worker`     | sonnet  | `.agent/roles/worker.md` |
| `reviewer`   | sonnet  | `.agent/roles/reviewer.md` |
| `integrator` | haiku   | `.agent/roles/integrator.md` |

**Integrator validation mode.** For the initial dry-run, set `INTEGRATOR_DRY_RUN=1` in Integrator's prompt (append a line: `Environment: INTEGRATOR_DRY_RUN=1`). After validation passes (see §4), tear down and re-spawn Integrator without that line, or update its scratchpad-addressable state to indicate live mode.

## 3. Kick off the loop

From main:
```
SendMessage(to=planner, body="begin — pick the next unchecked task in docs/superpowers/plans/2026-04-17-subproject-a-implementation.md")
```

From now on, main's job is to: (a) observe `.agent/team-state.md`, (b) respond to `blocker: human-needed` entries, (c) handle PR-merge hand-offs.

## 4. Dry-run validation gate (REQUIRED before flipping Integrator to live)

The team must complete Task 1.1 of sub-project A end-to-end in dry-run mode before Integrator is allowed to commit.

Human validates:

- [ ] Planner wrote a Brief matching Task 1.1's file list and step checkboxes.
- [ ] Worker wrote an Attempt 1 entry with files changed and test command summary.
- [ ] Reviewer wrote a Verdict — either ACCEPT or REJECT with a coherent reason.
- [ ] If REJECT: Worker retry happened and produced Attempt 2 within 2 rounds.
- [ ] On ACCEPT: Integrator printed a would-be commit message and `git diff --stat --cached` into the scratchpad (NOT an actual commit).
- [ ] Integrator rotated the scratchpad into `.agent/history/task-1.1.md`.
- [ ] No writes occurred to files outside Task 1.1's authorized file list (verify with `git status --short`).

If any check fails: tear down the team (`/team delete`), capture the failure in `docs/superpowers/audits/`, revise spec or role briefs, retry.

## 5. Flip Integrator to live

1. `SendMessage(to=integrator, body="pause")`.
2. Edit `.agent/roles/integrator.md` if you changed the dry-run contract during validation; commit the edit.
3. Re-spawn Integrator (via `/team` or `TeamCreate` again) without the `INTEGRATOR_DRY_RUN=1` environment line.
4. `SendMessage(to=integrator, body="resume — Task 1.1 is ACCEPTed; please commit")`.
5. Observe scratchpad for the commit SHA. If it shows up in `.agent/history/task-1.1.md` and `git log --oneline -1` matches the expected conventional-commit message, live mode is working.

## 6. PR-merge hand-off (once per PR boundary)

When Integrator opens PR N and pauses:
1. Human reviews and merges PR N in GitHub.
2. Back in this repo: `git checkout design/subproject-a-v2 && git pull --rebase origin main` (or whichever base the next PR rebases on).
3. `SendMessage(to=planner, body="PR <N> merged, begin PR <N+1>")`.

## 7. Teardown

`/team delete` (via main Claude session). `.agent/team-state.md` is gitignored so it can stay or be archived into `.agent/history/`; role briefs are committed and survive.
```

- [x] **Step 2: Run team-check — expect all green**

Run: `scripts/team-check.sh`

Expected: every line `ok:`, final `team-check: OK`, exit 0.

- [x] **Step 3: Commit**

```bash
git add docs/superpowers/runbooks/2026-04-18-team-spawn.md
git commit -m "docs: add team-spawn runbook with dry-run validation gate"
```

---

## Task 10 — End-to-end dry-run validation (HUMAN GATE)

**Purpose:** Prove the whole scaffold works by running the team on Task 1.1 of sub-project A's plan in dry-run mode, before any Integrator commit ever lands.

**Files:**
- Read-only reference: `.agent/team-state.md`, `.agent/history/task-1.1.md`
- Maybe create (only if validation fails): `docs/superpowers/audits/2026-04-18-team-dryrun-<outcome>.md`

- [x] **Step 1: Pre-flight**

```bash
scripts/team-check.sh
scripts/team-init.sh
```

Both must exit 0. `.agent/team-state.md` now exists with the template shape.

- [x] **Step 2: Spawn the team per runbook §2**

Follow `docs/superpowers/runbooks/2026-04-18-team-spawn.md` §2 exactly. Confirm `INTEGRATOR_DRY_RUN=1` is set in Integrator's prompt.

- [x] **Step 3: Kick off the loop per runbook §3**

`SendMessage(to=planner, body="begin — pick the next unchecked task in docs/superpowers/plans/2026-04-17-subproject-a-implementation.md")`

- [x] **Step 4: Observe end-to-end**

Tail the scratchpad from a separate shell:
```bash
watch -n 2 cat .agent/team-state.md
```

Expected arc: Planner Brief → Worker Attempt 1 → Reviewer Verdict → Integrator dry-run entry → scratchpad rotated to `.agent/history/task-1.1.md`.

- [x] **Step 5: Run the runbook §4 checklist**

Go through every check in runbook §4. Every box must be ticked before proceeding.

If any check fails:
- Tear down team: `/team delete` from main.
- Create `docs/superpowers/audits/2026-04-18-team-dryrun-FAILED.md` documenting which check failed and the scratchpad excerpt that caused it.
- Revise spec or role briefs, commit the revision, then loop back to Step 1.

- [ ] **Step 6: Record successful validation**

Append to `docs/superpowers/runbooks/2026-04-18-team-spawn.md` at the bottom:

```markdown

---

**Dry-run validated:** 2026-04-18 (Task 1.1 end-to-end; Integrator in DRY_RUN=1; all §4 checks passed).
```

- [ ] **Step 7: Flip Integrator to live per runbook §5**

Follow runbook §5 steps 1–5. Confirm Integrator's first real commit lands with the expected conventional-commit message.

- [ ] **Step 8: Commit the validation marker**

```bash
git add docs/superpowers/runbooks/2026-04-18-team-spawn.md
git commit -m "docs: record team dry-run validated; integrator now live"
```

Note: the Integrator's own first live commit will appear *before* this one in the log if Task 1.1's content lands first, which is fine — the ordering reflects reality.

---

## Task 11 — Open PR for the scaffold

**Files:**
- None (branch operation)

- [ ] **Step 1: Confirm tree is clean and tests pass**

```bash
git status
cd nautilus && uv run make lint test-unit && cd -
```

Both must be green. The scaffold itself doesn't add Python code, so `make lint test-unit` should pass unchanged from main.

- [ ] **Step 2: Push the branch**

```bash
git push -u origin design/subproject-a-v2
```

- [ ] **Step 3: Open the PR**

```bash
gh pr create --base main --title "feat: add SWE iterative team scaffold for sub-project A execution" --body "$(cat <<'EOF'
## Summary
- Four-role tmux-backed agent team (Planner/Worker/Reviewer/Integrator) driving sub-project A's 8-PR plan via Google's iterative multi-agent pattern.
- Shared scratchpad at `.agent/team-state.md` (gitignored) is the source of truth; `SendMessage` pings are transport only.
- `scripts/team-check.sh` validates scaffold completeness; `scripts/team-init.sh` bootstraps the scratchpad idempotently.
- End-to-end dry-run on Task 1.1 validated before Integrator flipped to live commits.

## Test plan
- [ ] `scripts/team-check.sh` exits 0
- [ ] `scripts/team-init.sh` creates `.agent/team-state.md` from template; second run refuses to clobber
- [ ] Dry-run of Task 1.1 passes all runbook §4 checks (recorded in runbook)
- [ ] Integrator's first live commit matches conventional-commit format and is authored on `design/subproject-a-v2`

Spec: `docs/superpowers/specs/2026-04-17-swe-iterative-team-design.md`
Plan: `docs/superpowers/plans/2026-04-18-swe-iterative-team-implementation.md`
Runbook: `docs/superpowers/runbooks/2026-04-18-team-spawn.md`
EOF
)"
```

- [ ] **Step 4: Capture the PR URL**

The `gh pr create` output prints the URL. No further commit needed — the scaffold is shipped as one atomic PR.

---

## Self-review checklist

Before handing off to execution:

- [ ] Every spec section has a task: §3 Architecture → Task 9 runbook; §4 Roles → Tasks 4–7; §5 Coordination → Task 3 template + Task 8 init; §7 Error handling → encoded in role briefs (Tasks 4–7); §8 Lifecycle → runbook (Task 9) + dry-run (Task 10); §9 Testing → Task 10 gate; §10 Files → Tasks 1–9 collectively.
- [ ] No placeholders — every code block and every role brief is complete.
- [ ] Type/name consistency — `.agent/team-state.md`, `scripts/team-check.sh`, `scripts/team-init.sh`, `INTEGRATOR_DRY_RUN` all spelled identically throughout.
- [ ] `team-check.sh` regexes in Task 2 match the headings in Tasks 3–7 exactly (`^## Current`, `^### Brief`, `^# Role:`, `^## Model:`, `^## Owns`, `^## Must not`, `^## Protocol`).
