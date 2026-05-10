# PR #23 Audit — Docs cleanup post-sub-project-A

**Verdict:** LGTM

**Scope audited:** `gh pr diff 23` (31 renames at 100% similarity, 1 new README, 2 doc edits). No existing Copilot/human review comments yet.

## Critical issues
0.

## Important
0.

## Nits
- **README scope creep (minor).** `docs/archive/research-timi/README.md` was not in the Brief the user approved; it was Worker-added. Global rule is "no new docs without explicit request." That said, it documents a real hazard (internal path references inside the corpus still point at the pre-move `docs/research/timi/...` layout), so it has genuine reader value. Recommend keeping but flagging in the PR description so the user can nuke it pre-merge if they prefer strict adherence. 24 lines, trivially revertable.

## Hidden risks
- **Self-references inside archived corpus (already disclosed).** 12 of the moved TiMi files still contain strings like `docs/research/timi/adapted/round<N>...`. The new README explicitly calls this out and asks sub-project C to pick one resolution at kickoff. Acceptable — rewriting now would bloat the "100% similarity" rename diff.
- **No sub-project-A plan/spec updates needed.** Grep of `docs/superpowers/plans/` and `docs/superpowers/specs/` finds zero references to the moved paths — the A corpus never linked into `competition-winners/` or `research/timi/`, so archiving them does not invalidate any in-flight design doc.
- **`.agent/` audits reference old paths** but `.agent/` is gitignored (untracked scratch), so not a repo concern.

## Spot-check results
- **Prunes (3/3 verified at HEAD):**
  - `strategies/crypto/kronos/backtest.py:1` — runner docstring correct.
  - `strategies/crypto/kronos/backtest.py:128-132` — `try: run/print_results / finally: engine.dispose()` present.
  - `strategies/crypto/kronos/_fetch_binance.py:1` — fetch-and-convert docstring correct.
- **Retro-ticks (sampled 4/20):** `.agent/roles/{planner,worker,reviewer,integrator}.md`, `.agent/team-state.template.md`, `scripts/team-check.sh` (executable), `docs/superpowers/runbooks/2026-04-18-team-spawn.md` all present on disk. Banner correctly flags Task 10 dry-run failure and missing Task 11 atomic PR.
- **Archive integrity:** `git log --follow docs/archive/research-timi/DESIGN.md` traces through the rename to pre-move history. Diff shows pure renames at 100% similarity (no add/delete pairs).
- **Dangling live links:** None in `docs/` (excluding self-refs inside archive), `README.md`, `CLAUDE.md`, or `docs/superpowers/`.

## Recommendation
Approve. Optional nit: decide whether to keep the unsolicited README before merge.
