# PR #22 Audit — Register 3 crypto strategy builders

**Verdict:** LGTM

**Scope verified:** 2 files changed — `nautilus/src/nautilus_trading/cli/_strategy_configs.py` (+24, added 3 builder classes + registry entries; plus incidental one-line Protocol `...` reformatting) and `tests/test_strategy_configs.py` (+4 tests; rest of diff is ruff/black reflow of dict args into multi-line `build(...)` calls — cosmetic only).

**Existing review comments:** none (`gh pr view 22 --comments` returned empty).

**Config claim verified against source:**
- `TimesFMGridConfig` (timesfm_grid.py:40) — required: `instrument_id`, `bar_type`, `trade_size`. All others defaulted. OK.
- `RVSSwingConfig` (rvs_swing.py:26) — same three required. OK.
- `ShockGuardConfig` (shock_guard.py:34) — same three required. OK.

Each builder passing `_base(args)` (which emits exactly those three keys) is correct.

**Critical issues:** 0

**Important:** 0

**Nits:**
- Builder classes have no explicit `StrategyConfigBuilder` base; they conform structurally to the Protocol. Consistent with existing pattern in file, so fine — just noting.
- Registry drift test locks 8 keys via `set(...) == {...}`. Good — adding/removing a key fails loudly. Assertion is strong.
- `test_rvs_swing_builder_base_only` / `test_shock_guard_builder_base_only` use `len(out) == 3` instead of full-dict equality. `test_timesfm_grid_builder_base_only` uses `==`. Minor inconsistency; all three are still meaningful (not tautological). Not worth blocking.

**Hidden risks:**
- If a Config later gains a required field (no default), the builder will silently still emit only 3 keys and `ImportableStrategyConfig` instantiation will blow up at runtime, not test time. The registry-sanity test won't catch this — but it's outside PR scope and would be handled by a Config-instantiation integration test (follow-up, not a blocker here).
- The Protocol signature reformat (`... :` → `...`) is unrelated to the stated PR goal. Harmless but slightly noisy; a strict reviewer might ask to split. Not worth blocking.

**Recommendation:** Approve and merge.
