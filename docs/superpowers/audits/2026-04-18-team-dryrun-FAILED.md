# Team Dry-Run — FAILED (2026-04-18)

**Status:** FAILED at Task 1.2 · Team torn down gracefully · Hardening in progress.

## What happened

The 4-role team (Planner/Worker/Reviewer/Integrator) was spawned per the runbook at `docs/superpowers/runbooks/2026-04-18-team-spawn.md` with `INTEGRATOR_DRY_RUN=1`. The intent: let Integrator *print* would-be commits into the scratchpad (§9 of the design), with the human reviewing the first full loop before flipping live.

**Actual behaviour — Worker self-committed three times**, bypassing Integrator entirely:

| Commit | Source | Author of `git commit` |
|--------|--------|------------------------|
| `878b719` | Task 1.1 Attempt 1 (buggy — F821) | Worker |
| `ac0e434` | Task 1.1 Attempt 2 (TYPE_CHECKING fix) | Worker |
| `9a29fa8` | Task 1.2 Attempt 1 (fixture catalog) | Worker |

Integrator, when later pinged, found nothing to commit and no-op'd. Its `### Integrator dry-run` section never appeared in `.agent/history/task-1.1.md`. The dry-run gate never gated anything.

## Root cause

Two-layer failure — the **upper layer is a Planner-brief design flaw**; the **lower layer is a Worker-brief enforcement gap**.

1. **Planner protocol (step 4)** said: *"Fill the `### Brief` section. Be literal: paste the step checkboxes from the plan verbatim so Worker cannot drift."* Every task in the plan doc ends with a literal `Step 6: Commit — git add … && git commit -m "…"` box. Pasting verbatim propagated that box into Worker's Brief.
2. **Worker brief** had a Must-not: *"`git commit`, `git push`, or open PRs. (Integrator owns git operations entirely.)"* But when a literal `- [ ]` checkbox in the Brief said to run `git commit`, Worker followed the checkbox over the Must-not. Role-level rules lose to literal Brief content.

Reviewer compounded the failure by ACCEPTing Task 1.1 Attempt 2 despite flagging `nautilus/pyproject.toml` as modified outside the Brief's authorized file list ("secondary note, not a blocker"). That is the wrong threshold — out-of-scope file edits are blockers.

## Blast radius

Three unauthorized commits on `subproject-a/pr1-test-harness`. The code changes themselves are correct (tests pass, `make lint` green on `ac0e434`); the problem is the commit provenance and the buggy intermediate state (`878b719`) being visible in history.

## Remediation (Option A′ — approved 2026-04-18)

1. Write this audit note.
2. Patch `.agent/roles/planner.md`: strip `git add` / `git commit` / `git push` from Brief-generation; add a `Commit-directive:` metadata line that only Integrator reads.
3. Patch `.agent/roles/worker.md`: explicit Must-not — if a Brief checkbox contains `git commit` / `git add` / `git push`, treat as a Planner bug, skip the step, flag in Attempt entry.
4. `git reset --mixed a404d9c` to unstage the three commits without losing the work in the working tree.
5. Re-commit as three clean commits: scaffold hardening → Task 1.1 → Task 1.2.
6. Verify `cd nautilus && uv run make lint test-unit` green after the rewrite.
7. Fix team backend to tmux (was iterm2 during the failed run).
8. Respawn the team and retry dry-run from **Task 1.3** (1.1 and 1.2 are done-and-committed).

## Lessons

- **Role Must-nots lose to literal Brief content.** Enforcement must happen at Brief-generation (Planner), not at Brief-obedience (Worker). Worker cannot be relied upon to disobey its own checkboxes.
- **"Paste the plan verbatim"** is a dangerous shortcut when the plan format includes operations that belong to a different role. The plan doc is author-oriented (every task has its own commit); the Brief must be role-oriented (strip and redirect).
- **Dry-run gates only work if the gated role is the one doing the thing.** Putting `INTEGRATOR_DRY_RUN=1` on Integrator gated nothing when Worker was the actual committer. The gate should live at the mechanism boundary, not the role boundary — a pre-commit hook on the worktree, for example, would have caught this.
- **Reviewer should REJECT on out-of-scope file edits, not note them.** "Brief/diff mismatch" is a hard-fail per §4 of the Reviewer protocol; it was softened inappropriately.

## Follow-ups (tracked, not done here)

- Consider a `pre-commit` hook on the team-owned branch that refuses commits authored by anything other than Integrator during dry-run mode. Defense in depth.
- Reviewer brief wording audit: ensure "secondary" / "flag only" exits don't exist for hard-fail checks.
- Plan doc (long-term): optional refactor to separate impl steps from commit steps, so the pasted Brief can never contain commit operations even if Planner misbehaves.
