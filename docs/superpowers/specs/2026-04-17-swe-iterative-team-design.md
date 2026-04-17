# SWE Iterative Team — Design

**Status:** APPROVED (2026-04-17)
**Purpose:** Execute sub-project A's 8-PR implementation plan (`docs/superpowers/plans/2026-04-17-subproject-a-implementation.md`) via a four-role tmux-backed agent team using Google's iterative multi-agent pattern.
**Scope:** Team architecture, roles, coordination medium, failure handling, lifecycle. Does **not** modify sub-project A's plan or specs — the team consumes them as input.

---

## 1. Goal

Drive sub-project A's implementation plan to completion task-by-task with minimal main-thread intervention, while keeping every step observable on disk and every code change gated by TDD + `make lint test-unit`.

Non-goals:
- Replacing the plan doc as source of truth. The team reads it and ticks its checkboxes; the plan is authoritative.
- Parallel execution of multiple tasks. The team runs one task at a time (plan tasks are sequenced; dependencies are encoded as `Depends on:` lines in the plan).
- Auto-merging PRs. The team opens PRs; the human merges.

## 2. Reference pattern

Google's "developers guide to multi-agent patterns" — iterative pattern: Orchestrator → Worker → Critic loop with shared Memory, iterating until the Critic accepts. We instantiate it as four roles instead of three so that the mechanical PR-plumbing work (commits, gate runs, `gh pr create`) can be cheaply delegated to a Haiku model without cluttering the Critic's judgment surface.

## 3. Architecture

Four long-lived teammates spawned via `TeamCreate` (tmux team mode). One shared scratchpad file is the source of truth for team state. `SendMessage` is used only as a "your turn" ping — never as the authoritative record.

```
main (orchestrator, no code edits)
  │  spawns team once, observes scratchpad, intervenes only on human-needed blockers
  │
  ├─► Planner   (Opus)     ─┐
  ├─► Worker    (Sonnet)   ─┤  all read/write .agent/team-state.md
  ├─► Reviewer  (Sonnet)   ─┤  all pingable via SendMessage
  └─► Integrator (Haiku)   ─┘
```

## 4. Roles

| Role | Model | Owns | Must not |
|---|---|---|---|
| **Planner** | Opus | Pick next unchecked task from plan doc; write task brief into scratchpad (files, step list, acceptance criteria); ping Worker. On escalation, re-decompose task. | Edit code. Tick plan checkboxes. |
| **Worker** | Sonnet | Implement task TDD-style (red → green → refactor). Append `attempt: N` entry with files changed + test output. Ping Reviewer. | Commit. Open PRs. Tick plan checkboxes. |
| **Reviewer** | Sonnet | Run `cd nautilus && uv run make lint test-unit` and check plan adherence. Write `verdict: ACCEPT\|REJECT` + reasons. Ping Worker (reject) or Integrator (accept). | Edit code. Commit. |
| **Integrator** | Haiku | On ACCEPT: tick the plan's `- [ ]` checkboxes for this task, rotate scratchpad into `.agent/history/task-<id>.md`, `git add` + commit with the task's conventional-commit message, push to `subproject-a/pr<N>-<slug>` branch. On final task of a PR: open PR via `gh pr create`. Ping Planner. | Judge correctness. Edit any file other than the plan doc (and only to tick `- [ ]` → `- [x]`). |

## 5. Coordination medium

### 5.1 Scratchpad — `.agent/team-state.md`

Every teammate and the main thread can `cat` this file to see current state. Append-only during a task; rotated by Integrator into `.agent/history/task-<id>.md` immediately after the commit step, leaving a fresh scratchpad for the next task's Brief.

```markdown
## Current: Task 1.1 — Diagnose and make pytest collection hermetic
PR: 1/8 · Attempt: 1 · Assigned: Worker · Updated: 2026-04-17T10:12Z

### Brief (Planner · 2026-04-17T10:10Z)
Files: cli/__init__.py, cli/backtest.py, cli/live.py, backtest/runner.py
Steps: 1–6 of Task 1.1.
Acceptance: `pytest --collect-only` completes in <5s; `make lint test-unit` green.

### Attempt 1 (Worker · 2026-04-17T10:40Z)
Files changed: cli/__init__.py, cli/backtest.py, backtest/runner.py
Test output summary: collection 1.8s, 247 tests collected.

### Verdict (Reviewer · 2026-04-17T10:48Z)
ACCEPT. make lint test-unit green. All 6 checkbox steps verifiably complete.

### History
- Task 1.0 … ACCEPT on attempt 1 (Integrator: commit <sha>)
```

### 5.2 Ping protocol

`SendMessage(to=<teammate>, body="your turn — see .agent/team-state.md")`. No task detail in the ping; detail lives in the scratchpad. This guarantees that a dropped/missed ping costs at most a poll delay, never correctness.

## 6. Per-task loop

```
Planner  → writes Brief → pings Worker
Worker   → implements → writes Attempt N → pings Reviewer
Reviewer → runs gates → writes Verdict
  ├─ ACCEPT  → pings Integrator → commits + maybe opens PR → pings Planner → [next task]
  └─ REJECT  → pings Worker (attempt N+1)
                 └─ if N == 2 → pings Planner (escalate)
                                  ├─ re-decompose into smaller steps, or
                                  └─ writes `blocker: human-needed` and pings main
```

## 7. Error handling

| Condition | Response |
|---|---|
| Reviewer REJECT, attempt < 2 | Worker retries with reasons from scratchpad. |
| Reviewer REJECT, attempt == 2 | Planner inspects reject reasons. If task can be split into smaller steps, Planner writes a new Brief with the decomposed steps and pings Worker (attempt counter resets). If the reasons point to a genuine ambiguity or missing info, Planner writes `blocker: human-needed` + a specific question and pings main. |
| `make lint test-unit` failure | Reviewer appends exact command output to scratchpad, REJECT. |
| Merge conflict / branch drift at Integrator | Integrator writes `blocker: human-needed` and pings main; team pauses. |
| Agent death (tmux pane closed) | Main observes no scratchpad update for >10 min, respawns that role only. New agent reads scratchpad and resumes at the current role's expected action. |
| Plan ambiguity (Planner can't pick next task) | Planner writes `blocker: human-needed` + specific question, pings main. |

## 8. Lifecycle

- **Spawn:** Main runs `TeamCreate` once with role briefs; each agent reads `CLAUDE.md` + its role-specific brief + initial scratchpad.
- **Runtime:** Team processes tasks 1.1 → 8.N continuously. At each PR boundary Integrator opens the PR, writes `paused: awaiting PR <N> merge` in scratchpad, and the team idles.
- **PR-merge hand-off:** Human merges the PR. Main sends `SendMessage(to=Planner, body="PR <N> merged, begin PR <N+1>")`. Planner rebases the current branch on the newly-merged commit, writes the first Brief for PR N+1 into the scratchpad, and pings Worker.
- **Teardown:** On `/team delete` or on completion of PR 8.

## 9. Testing the team itself

Before the team is allowed to commit:
1. **Dry-run mode** — Integrator starts with `INTEGRATOR_DRY_RUN=1` set in its role brief; it prints the commit message and `git diff --stat` to scratchpad instead of committing.
2. Main reviews the first full loop (Task 1.1) end-to-end from scratchpad alone.
3. If the loop is clean, main removes `INTEGRATOR_DRY_RUN` from Integrator's brief and re-pings it to resume live.

If anything goes sideways during the dry-run, the team is torn down and this spec is revised before retrying.

## 10. Files created by this design

- `.agent/team-state.md` — live scratchpad (gitignored; per-worktree).
- `.agent/history/task-<id>.md` — rotated task records (gitignored).
- `.agent/roles/{planner,worker,reviewer,integrator}.md` — per-role briefs (committed; versioned).
- `scripts/team-spawn.sh` — one-shot `TeamCreate` invocation wrapper (committed).

## 11. Open items (resolved before implementation plan)

None. All blocking design decisions resolved during brainstorming (2026-04-17):

- Team composition: 4 roles (Planner/Worker/Reviewer/Integrator)
- Granularity: per-task (1.1, 1.2, …)
- Coordination: hybrid (scratchpad + SendMessage pings)
- Models: Opus / Sonnet / Sonnet / Haiku
- Lifetime: long-lived across all 8 PRs

## 12. Reference

- Plan this team executes: `docs/superpowers/plans/2026-04-17-subproject-a-implementation.md`
- Sub-project A spec: `docs/superpowers/specs/2026-04-17-subproject-a-design.md`
- Sub-project roadmap: `docs/superpowers/roadmap.md`
- User preference: main thread is pure orchestration (no Edit/Write/Bash on production code).
