# PR #32 Audit — sub-project B PR 6 (YAML run configs)

**URL:** https://github.com/cajias/nautilus-trading/pull/32
**Head:** `subproject-b/pr6-yaml-run-configs` @ `9c27d68d`
**Base:** `main`
**State:** OPEN · `mergeable: MERGEABLE` · `mergeStateStatus: CLEAN` · `isDraft: false` · `reviewDecision: ""` (no required reviewers)

## CI Status

- `gh pr checks 32` → **no checks reported** (exit 1). Repo has no GitHub Actions workflows registered for this branch.
- Only check-runs on HEAD SHA: `Prepare` (success) + `Agent` (in_progress) — these are Claude harness artifacts, not a CI gate.
- **Verdict: no green CI signal, but also no failing gate.** Consistent with earlier sub-project A/B PRs (CI was descoped to post-merge per sub-project roadmap).

## Automated Reviewer

- **Copilot** left 1 review (auto-comment, no line-level findings). Note in review body: "Copilot was unable to run its full agentic suite in this review." → no actionable feedback surfaced.
- No other issue comments, no PR review comments.

## Critical Issues (merge blockers)

**None.** Loader uses `msgspec.yaml.decode` (YAML 1.1 safe-load path, no pickle/ctor exec). `forbid_unknown_fields=True`. CLI guards missing file via `typer.Option(exists=True)`. Error paths all funnel through `typer.BadParameter`. No secrets leakage (no env-var handling in new code beyond the existing `load_dotenv_local()`).

## Important Issues (should fix before merge)

1. **PR description claim vs. actual code — `duration` field NOT dropped**
   - `nautilus/src/nautilus_trading/paper_trade/run_config.py:30` — the final HEAD still contains `duration: str | None = None`.
   - PR body explicitly states: *"Dropped unused `duration` field from the schema"*. This is misleading.
   - Commit `9c27d68d` title says "drop unused duration field" but the file still carries it. Either the commit didn't actually remove it, or the PR body is wrong.
   - **Fix:** Either remove the field (and keep the PR body accurate) or amend the PR body to reflect reality.
   - **Severity:** Medium. Not a functional bug (field is silently accepted since it's optional), but the strict-schema guarantee advertised in the PR body ("silently ignored — strict schema now rejects it") is false — unknown fields are rejected, but `duration` is still explicitly allowed and silently ignored by the runner.

## Advisory Issues (post-merge cleanup acceptable)

1. **`params: dict[str, Any]` has no msgspec-level type validation** — noted in PR body as a deferred item. Acceptable: the `StrategyConfigBuilder` downstream raises `ValueError` on bad fields which the CLI remaps to `BadParameter`. Consider per-strategy typed struct unions in a later PR.
2. **No non-monkeypatched `build_config()` end-to-end test** — deferred, acceptable. Every runner's `main()` is stubbed in `test_paper_trade_configs.py`. Real dispatch to Binance is out-of-scope until PR 8 (CI smoke).
3. **Path handling** — `typer.Option(exists=True, dir_okay=False)` guards the input; `path.read_bytes()` takes an absolute or relative Path with no traversal filtering. Acceptable for a local-dev CLI, not a multi-tenant server.
4. **No test for `trade_size` omitted entirely** — `test_load_run_config_accepts_null_trade_size` covers explicit `null`, but not the "field absent" case. Minor; msgspec defaults handle it.

## Spec & Plan Compliance

- **Spec** (`docs/superpowers/specs/2026-04-22-pr6-yaml-run-configs-design.md`): 185 lines, shipped in-tree. All acceptance criteria from the spec (schema strictness, CLI dispatch, error paths, 8 committed YAMLs) are implemented and tested.
- **Plan** (`docs/superpowers/plans/2026-04-22-pr6-yaml-run-configs-implementation.md`): 1070 lines, shipped in-tree. Matches shipped code (7 commits mirror the 7 plan tasks).
- **Roadmap renumbering** — confirmed correct in `docs/superpowers/plans/2026-04-21-subproject-b-implementation.md`:
  - PR 6 (new) = YAML configs (this PR) ✓
  - PR 7 (was PR 6) = Kronos migration + parity gate ✓
  - PR 8 (was PR 7) = CI smoke ✓
  - Dependencies correctly updated ("Depends on: PR 6 merged" for PR 7).

## Test Coverage

- `tests/paper_trade/test_run_config.py` (+86 LoC) — 4 tests: valid round-trip, unknown field rejected, missing-required rejected, `trade_size: null` accepted.
- `tests/cli/test_paper_trade_configs.py` (+53 LoC) — 8 parametrized dispatch tests (one per committed YAML).
- `tests/cli/test_paper_trade_cli.py` — rewritten from 398→41 LoC (old flag-ladder tests deleted; replaced with CLI registration + `--config` dispatch + error-path tests).
- PR body reports: `292 passed, 19 skipped` against full suite.

## Overall Recommendation

**ADDRESS_FIRST** — resolve the `duration` field discrepancy (Important Issue #1). Either the field should actually be removed (PR body is then accurate) or the PR body should be edited to stop claiming it was removed. Fix takes ~1 minute.

All other items (CI gap, deferred typed params, path handling) are acceptable and already acknowledged. Once the duration discrepancy is resolved, this is safe to merge.
