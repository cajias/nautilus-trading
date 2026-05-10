# PR #29 Audit — sub-project B PR 3: EMACrossPaperTradeRunner + CLI dispatch

**Verdict: APPROVE — no blockers.**

Scope reviewed: diff `origin/main...subproject-b/pr3-cli-ema-runner`, 5 commits.

Files read:
- `strategies/crypto/ema_cross_paper.py` (new)
- `nautilus/src/nautilus_trading/cli/paper_trade.py` (rewritten)
- `nautilus/src/nautilus_trading/paper_trade/node_config.py` (round_to_tick simplified)
- `nautilus/src/nautilus_trading/paper_trade/runner_base.py`
- `nautilus/src/nautilus_trading/cli/_strategy_configs.py` (EMAConfigBuilder)
- `tests/strategies/crypto/test_ema_cross_paper.py`
- `tests/cli/test_paper_trade_cli.py`
- `tests/paper_trade/test_node_config.py`
- `strategies/forex/ema_cross.py` (target of dispatch)
- `nautilus/pyproject.toml` (vulture allowlist)

## Verification performed
- PR-scoped tests (18) all pass under venv Python 3.14.
- Full suite (per user): 269 passed, 19 skipped; `make lint` green.
- Reproduced the dispatch end-to-end: `STRATEGY_BUILDERS['ema_cross'].build({...})`
  emits `{instrument_id, bar_type, trade_size, ema_period, fast_ema_period, slow_ema_period}`.
  `EMACrossConfig(**...)` rejects `ema_period` as an unexpected kwarg, **but**
  the actual runtime path (`StrategyFactory.create(ImportableStrategyConfig)`) uses
  msgspec JSON decode which silently drops unknown fields in this nautilus version.
  So the extra key is cosmetic cruft, not a crash.

## Blockers found
None.

## Non-blocker observations (worth a follow-up, not this PR)

1. **EMAConfigBuilder leaks a stray `ema_period` key** (`cli/_strategy_configs.py:57-62`).
   It sets `out["ema_period"] = args["slow_ema"]` plus `fast_ema_period`/`slow_ema_period`.
   `EMACrossConfig` only defines the latter two — the `ema_period` field is silently
   dropped by msgspec. This is pre-existing (builder was authored earlier for the
   timesfm/swing variants), but the ema_cross runner is now the first real caller,
   so the dead key is visible. Recommend splitting the builder or guarding per-strategy
   when PR 4 lands the timesfm_swing runner (which genuinely uses `ema_period`).

2. **`_RUNNERS: dict[str, type]` has no Protocol bound** — already called out as
   deferred; fine.

3. **Duplicate default `fast_ema=10 / slow_ema=20`** in CLI Typer options and in
   the `EMACrossPaperTradeRunner` dataclass — already acknowledged architectural
   tradeoff; fine.

4. **`test_paper_trade_unknown_strategy_exits_nonzero`** asserts only exit_code≠0.
   Consider also asserting that the error mentions `ema_cross` as a valid option,
   so a future accidental wipe of `_RUNNERS` doesn't pass the test. Minor.

5. **`_RUNNERS` is module-global mutable state** seeded via `_load_runners()`.
   Harmless in single-process CLI usage, but if anyone later imports `paper_trade`
   from a library context and re-invokes with different strategies, it'll remain
   populated. Not a blocker; flag if PR 7 adds multi-tenant harness tests.

6. **`duration` added to vulture ignore list** — fine as a placeholder, but leaves
   the Typer option documented ("Optional time-box like '30m' or '2h'. Omit for
   continuous run.") while the value is silently discarded. Users who pass
   `--duration 30m` will get a *continuous* run with no warning. Consider either
   (a) logging "--duration is not yet implemented" or (b) raising BadParameter
   until the feature lands. Cosmetic, not a blocker.

7. **`run_paper_trade` signal handler calls `sys.exit(0)` inside a signal frame**
   after `node.dispose()`. If `node.dispose()` raises, the exit is skipped. Pre-
   existing from PR 1, not introduced here.

## Summary
PR is tight, tests pass, lint green, all three Binance-Testnet blocker fixes are
preserved by virtue of `build_paper_trade_node_config` being the single
composition seam. The `ema_period` leak is the only surface worth flagging for
next PR cleanup. No merge-stoppers.
