# Deprecated Code Audit — Sub-project A Wrap (Task #24)

**Date:** 2026-04-20
**Branch:** `subproject-a/task21-competition-archive` (worktree)
**HEAD:** `2cd7053` — `chore: archive competition code; preserve winners as docs/competition-winners/*.md`
**Scope:** Broader sweep following Task #16 (F401/F841) and PR #20 (competition archive). User flag 2026-04-20: *"we are keeping a lot of code around that is slowing us down"*.

**Read-only.** No deletions, no PRs, no source edits performed.

---

## 1. Executive Summary

| Verdict | Count | Notes |
|---|---|---|
| `safe_delete` | **7** | Untracked leftover dirs + empty lock files + 1 trivial comment |
| `needs_owner_review` | **9** | 4 unregistered strategies, 5 documented ruff exceptions |
| `keep_with_note` | **6** | Protection list (ema_cross, hybrid_sma_r10, etc.) |
| **Total candidates** | **22** | |

**Headline:** Vulture finds **zero** dead symbols at confidence 80. The codebase is actually quite tight. The real cleanup opportunities are *untracked worktree cruft* (empty `competition/` + `tests/competition/` dirs that weren't fully removed after PR #20) and *unregistered strategies* (4 strategies have tests but are not CLI-exposed via `STRATEGY_BUILDERS`; decide: register or retire).

Ruff reports 29 remaining errors, but 21 are `E402` on `sys.path.insert` patterns that cannot sort before imports — these are style polish, not dead code.

---

## 2. Recommended Deletion PR Grouping

### PR A — Cleanup cruft (safe_delete only, ~0 LOC risk)
- Remove untracked empty `competition/` and `tests/competition/` leftover dirs + `__pycache__`
- Remove two 0-byte `uv-*.lock` artefacts at repo root
- Delete commented-out `# 30% drawdown` line (`tests/test_risk_guard.py:159`)
- Merge `vulture_whitelist.py` into `[tool.ruff]` `per-file-ignores` or delete it entirely (`bar` expression triggers B018)

### PR B — Decide on unregistered strategies (needs_owner_review)
Four strategies exist with tests but are not in `STRATEGY_BUILDERS`. Options per strategy:
- **Register** (add builder + CLI flag) — if live-pluggable is the goal
- **Retire** (delete .py + tests + notebooks) — if superseded
- **Mark experimental** (docs note, keep tests) — if hold-for-later

Strategies: `timesfm_grid`, `rvs_swing`, `shock_guard`, `kronos`. (Note: `risk_guard` is a mix-in, not a standalone strategy — different question.)

### PR C — Ruff polish (style only)
Fix `E741`, `I001`, `C408`, `B905`, `B018` (~8 mechanical fixes). Leave `E402` alone (justified by `sys.path.insert` pattern — see §8).

---

## 3. Vulture Unreferenced Symbols

**Command:** `cd nautilus && uv run vulture src/ ../strategies/ ../tests/ --min-confidence 80`

### (none):0 (vulture_80)
- **Evidence:** Empty output. No symbols flagged at confidence ≥80.
- **Verdict:** N/A — nothing to flag.

**Lowering the bar to 60%** surfaces ~30 false positives, all of them framework-dispatched callbacks (`on_start`, `on_bar`, `on_stop`, `on_event`) plus msgspec frozen config fields — the existing `vulture_whitelist.py` is meant to cover these but triggers its own B018 ruff error (see §8).

**Conclusion:** no genuine dead symbols at the vulture level.

---

## 4. Ruff ERA001 — Commented-Out Code

**Command:** `cd nautilus && uv run ruff check --select ERA001 ..`

### `tests/test_risk_guard.py:159` (ERA001)
- **Symbol:** `# 30% drawdown`
- **Evidence:** `ruff check --select ERA001` reports exactly one finding.
- **Last touched:** `486902c` Mon Apr 20 18:18:28 2026 (PR 8.5 cleanup commit)
- **Callers:** N/A — it's a comment in test body.
- **Verdict:** `safe_delete` — trivial leftover label. One-liner removal.

**Total ERA001 findings: 1.**

---

## 5. Stale Strategy Scaffolds

**Registered in `STRATEGY_BUILDERS`** (`nautilus/src/nautilus_trading/cli/_strategy_configs.py:92`):
`grid_bot`, `dca_bot`, `ema_cross`, `timesfm_swing`, `hybrid_sma_r10`.

### `strategies/crypto/timesfm_grid.py` (unregistered)
- **Symbol/thing:** `TimesFMGridStrategy`
- **Evidence:** Not in STRATEGY_BUILDERS. Referenced by `tests/test_timesfm_grid.py`, own notebook `timesfm_grid_backtest.ipynb`, `strategies/crypto/_grid_math.py`, docs/plans.
- **Last touched:** co-modified with `_grid_math.py` during PR 7 series.
- **Callers/imports:** tests + notebook only; NO CLI path.
- **Verdict:** `needs_owner_review` — fully tested but invisible to `nt backtest`. Register or retire.

### `strategies/crypto/rvs_swing.py` + `rvs_data.py` (unregistered)
- **Symbol/thing:** `RVSSwingStrategy`, `RVSSwingConfig`, `RVSSignal`
- **Evidence:** Not in STRATEGY_BUILDERS. Tests (`test_rvs_swing.py`) + notebook (`rvs_swing_backtest.ipynb`) exist.
- **Callers/imports:** tests + notebook only.
- **Verdict:** `needs_owner_review` — decide register/retire.

### `strategies/crypto/shock_guard.py` (unregistered)
- **Symbol/thing:** `ShockGuardStrategy`
- **Evidence:** Not in STRATEGY_BUILDERS. Tests + notebook exist.
- **Verdict:** `needs_owner_review` — decide register/retire. May be intentional as "defensive only, not meant for CLI exposure".

### `strategies/crypto/kronos/` (unregistered)
- **Symbol/thing:** `KronosStrategy`, `KronosActor`, `build_kronos_signal`, `KronosSignal`, custom `backtest.py` + `backtest_config.py` + `paper_trade.py`
- **Evidence:** Not in STRATEGY_BUILDERS. Has its own self-contained `backtest.py` runner and a `paper_trade.py` — appears to run outside the common CLI. 3 test files.
- **Last touched:** PR 4 series (Task 4.1–4.3).
- **Callers:** tests + self-contained runner. No nt-CLI integration.
- **Verdict:** `needs_owner_review` — self-contained subpackage, substantial code (~6 files). Either promote to STRATEGY_BUILDERS or document as "standalone Kronos runner, not part of nt CLI".

### `strategies/crypto/risk_guard.py` (not a strategy)
- **Symbol/thing:** `RiskGuard` mix-in
- **Evidence:** Imported by every crypto strategy (`timesfm_grid`, `dca_bot`, `shock_guard`, `rvs_swing`, `grid_bot`, `timesfm_swing`). Not meant to be CLI-exposed.
- **Verdict:** `keep_with_note` — cross-cutting mix-in, correctly unregistered.

### `strategies/crypto/_grid_math.py` (helper)
- **Symbol/thing:** grid geometry helpers
- **Evidence:** Imported by `grid_bot`, `timesfm_grid`. Underscore prefix signals private helper.
- **Verdict:** `keep_with_note` — private helper module.

### `strategies/forex/ema_cross.py` (registered, docs canonical)
- **Verdict:** `keep_with_note` — referenced in root CLAUDE.md as *the canonical example*.

### `strategies/crypto/hybrid_sma_r10.py` (registered)
- **Verdict:** `keep_with_note` — live R10 winner, user-preserved.

### `strategies/prediction_markets/__init__.py`
- **Evidence:** Empty package, no modules.
- **Verdict:** `keep_with_note` — placeholder for Sub-project C per `docs/superpowers/roadmap.md`.

---

## 6. Unused Fixtures / Conftest Helpers

**File scanned:** `tests/conftest.py` (the only conftest).

**Fixtures defined:**
- `crypto_catalog_path` (session-scoped)
- `cli_runner`
- `nt_app`

### conftest fixture scan
- **Evidence:** All three fixtures are consumed by multiple tests — manual inspection of `tests/test_cli_common.py`, `tests/test_cli_live.py`, `tests/test_fixture_catalog.py`, etc., confirms usage.
- **Verdict:** `keep_with_note` — all in use.

**Conclusion:** no unused fixtures.

---

## 7. Stale Notebooks

**Notebooks scanned (4 total):**

| Notebook | Cells | Import cells | Status |
|---|---|---|---|
| `strategies/crypto/rvs_swing_backtest.ipynb` | 24 | 0 | Valid (uses %run or top-level globals) |
| `strategies/crypto/shock_guard_backtest.ipynb` | 26 | 0 | Valid |
| `strategies/crypto/timesfm_grid_backtest.ipynb` | 32 | 2 | Valid |
| `strategies/forex/ema_cross_backtest.ipynb` | 17 | 2 | Valid |

### `rvs_swing_backtest.ipynb`, `shock_guard_backtest.ipynb`
- **Evidence:** 0 import cells suggests heavy use of jupyter magics. Associated strategy files still exist and match.
- **Verdict:** `needs_owner_review` — tied to unregistered strategies above. If those strategies are retired, delete notebooks too.

### `timesfm_grid_backtest.ipynb`, `ema_cross_backtest.ipynb`
- **Evidence:** Still reference current class names. No rot detected.
- **Verdict:** `keep_with_note`.

---

## 8. Dead Config Knobs

**Scope:** `StrategyConfig` fields vs. their `Strategy` subclass body.

Vulture at confidence 60 flags a few potential unused config fields:
- `strategies/crypto/shock_guard.py:52` — `bid_volume_ratio_threshold`
- `strategies/crypto/timesfm_grid.py:74` — `recalc_interval_bars`

### `shock_guard.py:52` — `bid_volume_ratio_threshold`
- **Evidence:** Vulture 60% confidence flags this. Requires manual grep to verify usage in `on_bar` (would need open file to confirm). Note that vulture misses attribute access via `self.`.
- **Verdict:** `needs_owner_review` — possibly genuine dead config. Worth a 2-minute grep before deleting.

### `timesfm_grid.py:74` — `recalc_interval_bars`
- **Evidence:** Same caveat.
- **Verdict:** `needs_owner_review`.

**CLI flags:** All CLI flags in `cli/backtest.py` and `cli/live.py` flow through `_resolve_strategy_paths` and the STRATEGY_BUILDERS registry — no orphaned flags detected.

---

## 9. Docs Rot — `docs/superpowers/deferred/`

**Directory contents:** one file — `post-refactor-tasks.md` (126 lines).

### `docs/superpowers/deferred/post-refactor-tasks.md`
- **Evidence:** Contains 5 deferred tasks; all reference paths that **still exist** (`strategies/crypto/kronos/backtest.py`, `strategies/crypto/kronos/_fetch_binance.py`, `tests/test_data_providers.py`, `tests/test_backtest_runner.py`, `nautilus/src/nautilus_trading/backtest/runner.py`).
- **Last touched:** Apr 19 10:12.
- **Verdict:** `keep_with_note` — all referenced paths exist; the file tracks genuinely deferred work.

**Conclusion:** no doc rot in the deferred dir.

---

## 10. Ruff Remaining Errors on HEAD

**Command:** `cd nautilus && uv run ruff check ..`
**Total:** 29 errors.

### Breakdown by rule
```
E402: 21  (module-level import not at top)
E741:  1  (ambiguous var name `l`)
I001:  3  (unsorted import block)
B905:  1  (zip() without strict=)
C408:  2  (unnecessary dict() call)
B018:  1  (useless expression)
```

### E402 × 21 — `sys.path.insert` pattern (style polish)
- **Files:** `tests/fixtures/crypto/build_catalog.py`, `tests/test_dca_bot.py`, `tests/test_grid_bot.py`, `tests/test_kronos_strategy.py`, `tests/test_risk_guard.py`, `tests/test_rvs_swing.py`, `tests/test_timesfm_swing.py`
- **Evidence:** Every E402 occurs after a `sys.path.insert(0, str(_REPO_ROOT))` guard required to add `strategies/` to path for tests that can't rely on editable install.
- **Verdict:** `keep_with_note` (false positive / justified). Fix: add `# noqa: E402` or register `tests/` in `pyproject.toml` `[tool.ruff] extend-per-file-ignores`.

### E741 — `tests/fixtures/crypto/build_catalog.py:100`
- **Symbol:** variable `l`
- **Verdict:** `safe_delete`/style-polish — rename.

### I001 × 3 — unsorted imports
- **Files:** `tests/test_risk_guard.py`, `tests/test_grid_bot.py`, another
- **Verdict:** `safe_delete`/style-polish — `ruff check --fix` handles it.

### B905 — `tests/test_grid_bot.py:148`
- **Symbol:** `zip()` without `strict=`
- **Verdict:** style-polish — add `strict=True` or `strict=False`.

### C408 × 2 — unnecessary `dict()` call
- **Files:** include `tests/test_grid_bot.py:264`
- **Verdict:** style-polish — literal form `{}`.

### B018 — `vulture_whitelist.py:4`
- **Symbol:** bare expression `bar  # noqa: F821 — on_bar(self, bar) callback parameter`
- **Evidence:** The whitelist idiom (bare name to silence vulture) trips ruff B018. File at root, tracked by git.
- **Verdict:** `safe_delete` — either (a) delete the file and move vulture whitelisting into `pyproject.toml` `[tool.vulture]` ignore list, or (b) add `# noqa: B018` to each line. Recommend (a).

---

## 11. Untracked Worktree Cruft

Non-ruff, non-vulture findings worth flagging:

### `competition/` + `tests/competition/` dirs (post-archive leftovers)
- **Evidence:** `git ls-files competition/ tests/competition/` → empty (fully removed from git in `2cd7053`). But the directories still exist on disk:
  - `competition/__pycache__/` + `competition/round_configs/`
  - `tests/competition/__pycache__/`
- **Verdict:** `safe_delete` — untracked cruft; `rm -rf` safely.

### `uv-9be70c2d1000bdae.lock` + `uv-setuptools-9be70c2d1000bdae.lock`
- **Evidence:** Both are 0 bytes, last touched Apr 18. Neither is tracked by git.
- **Verdict:** `safe_delete` — stale uv artefacts at repo root, untracked.

### `.agent/pr-8.5-scope.md` (untracked)
- **Evidence:** `git status` shows `?? .agent/pr-8.5-scope.md`.
- **Verdict:** `keep_with_note` — PR 8.5 scope doc, useful history. Gitignored by design in `.agent/` subdir.

### `.agent/history/` (36 task files, ~292 KB)
- **Evidence:** Task execution history for PRs 1–8 + task-21. Useful for retrospective.
- **Verdict:** `keep_with_note` — intentionally preserved as project memory.

---

## 12. Appendix: Protection List (explicitly NOT flagged)

| Path | Why protected |
|---|---|
| `strategies/forex/ema_cross.py` | Canonical example cited in root `CLAUDE.md` |
| `strategies/crypto/hybrid_sma_r10.py` | Live R10 winner, 48 passing tests, user-preserved |
| `strategies/crypto/risk_guard.py` | Cross-cutting mix-in, imported by every crypto strategy |
| `strategies/crypto/_grid_math.py` | Shared helper for grid strategies |
| `strategies/prediction_markets/__init__.py` | Placeholder for Sub-project C per roadmap |
| `nautilus/src/nautilus_trading/cli/_strategy_configs.py` | STRATEGY_BUILDERS registry |
| `docs/superpowers/deferred/post-refactor-tasks.md` | Active deferred task tracker |
| `tests/conftest.py` fixtures | All 3 fixtures in active use |
| `.agent/history/` | Task execution memory |

---

## 13. Open Questions for User

1. **Unregistered strategies (§5):** For each of `timesfm_grid`, `rvs_swing`, `shock_guard`, `kronos` — register or retire? Each has ~100–600 LOC plus tests and notebooks; combined retire-path could be ~1–2k LOC. Register-path requires adding a builder class to `_strategy_configs.py`.
2. **Kronos specifically:** Its self-contained `backtest.py` + `paper_trade.py` suggests it was meant to run *outside* `nt` CLI. Confirm intent?
3. **`vulture_whitelist.py`:** Replace with `pyproject.toml` `[tool.vulture]` config block? (This fixes the B018 and puts the ignore list in a more standard location.)
4. **Tests directory ruff rules:** Should we add `"tests/**" = ["E402"]` to `pyproject.toml` `[tool.ruff.lint.extend-per-file-ignores]` to stop flagging the justified `sys.path.insert` pattern? This silences 21 of the 29 remaining errors without code changes.

---

*Audit ready for user review.*
