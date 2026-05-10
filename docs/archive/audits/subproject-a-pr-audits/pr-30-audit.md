# PR #30 Audit — sub-project B PR 4: simple directional runners

**Verdict: APPROVE — no blockers**

## Scope reviewed
- 3 new runners: grid_bot_paper.py, dca_bot_paper.py, timesfm_swing_paper.py
- paper_trade.py CLI (conditional-kwargs dispatch, eager build_config validation, 3 new Typer options)
- Composition tests for the 3 runners + 4 new CLI tests

## Focus-area findings

### 1. Runner parity (PASS)
All four runners (ema_cross, grid_bot, dca_bot, timesfm_swing) are structurally identical: `@dataclass` PaperTradeRunner subclass → fetch builder from STRATEGY_BUILDERS → build_paper_trade_node_config → run_paper_trade. Only the field set and builder key differ, exactly as the design intends. No drift.

### 2. Eager build_config() double-call (SAFE)
CLI calls `runner.build_config()` for pre-flight validation; `runner.main()` calls it again inside `run_paper_trade(self.build_config())`. Audit of the call chain:
- STRATEGY_BUILDERS[*].build(args) is pure — returns a new dict, no mutation of inputs, no I/O.
- build_paper_trade_node_config constructs fresh BinanceInstrumentProviderConfig, BinanceDataClientConfig, BinanceExecClientConfig, ImportableStrategyConfig, TradingNodeConfig. Frozen msgspec structs; no side effects.
- InstrumentId.from_str is idempotent.
- No logging, no network, no file I/O on the build path.
Double invocation is safe. The discarded first config is GC-collectible.

### 3. TimesFM composition test (SAFE — no ML side effect)
test_timesfm_swing_paper.py imports only TimesFMSwingPaperTradeRunner, not TimesFMSwingStrategy. `build_config()` emits an ImportableStrategyConfig with a strategy_path *string* — the strategy class (and any TimesFM checkpoint load) is resolved lazily by TradingNode at run-time, which the test never boots. ~1.3s runtime is consistent with no checkpoint load.

### 4. CLI conditional-kwargs correctness (PASS)
Each branch's kwarg set matches the target dataclass exactly:
- ema_cross → base + fast_ema + slow_ema ✓ matches EMACrossPaperTradeRunner fields
- timesfm_swing → base + fast_ema + slow_ema ✓ matches TimesFMSwingPaperTradeRunner fields
- grid_bot → base + upper_price + lower_price + grid_levels ✓ matches GridBotPaperTradeRunner fields
- dca_bot → base + buy_interval_bars (+ buy_amount if not None) ✓ matches DCABotPaperTradeRunner fields (buy_amount defaults to None)

TypeError → BadParameter is correctly scoped as defense-in-depth; with current dispatch logic it is unreachable, which is fine (belt-and-suspenders).

ValueError → BadParameter is the live path: GridBotConfigBuilder.build raises ValueError on missing upper/lower_price, DCABotConfigBuilder on missing buy_interval_bars. test_paper_trade_grid_bot_missing_required_args_is_usage_error exercises this end-to-end.

## Non-blocker observations for PR 5
- `_load_runners()` grows one import + one dict entry per new strategy — O(n) edits but isolated; Protocol/discovery refactor can wait.
- The `if strategy == "..."` ladder in paper_trade.py will likely bloat at PR 5 (already noted). Consider a per-strategy `extract_kwargs(options) -> dict` hook on each runner class to invert control, but only if the ladder hits ~8 branches.
- test_timesfm_swing_paper.py asserts string literal `"strategies.crypto.timesfm_swing:TimesFMSwingStrategy"` — if the module is ever renamed, this test + the runner both need updating. Acceptable for now.
- Duplicate fast_ema=10/slow_ema=20 defaults between Typer options and EMA/TimesFM runner dataclasses remains (acknowledged tradeoff).

## Gates
- make lint: green (reported).
- tests: 277 passed / 19 skipped (reported).
- No secrets in diff.
- No production dependency changes.

**Recommendation: merge.**
