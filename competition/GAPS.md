# Strategy Competition — Gap Analysis

_Audited: 2026-04-11_

## TL;DR

The competition has been running pandas-only backtests that are **completely disconnected
from NautilusTrader**. All 50 competition strategy files (5 agents × 10 rounds) expose a
`run_backtest(start, end, initial_capital)` function that directly calls the Binance REST
API, runs a custom event loop, and returns a metrics dict. None of them are
`nautilus_trader.trading.strategy.Strategy` subclasses. They cannot plug into a
`TradingNode` without a complete rewrite.

A parallel set of proper NautilusTrader strategies lives in `strategies/crypto/`
(GridBot, DCABot, RVSSwing, ShockGuard, TimesFMSwing, TimesFMGrid) but these are never
referenced by any competition evaluator.

---

## Audit Results

### `strategies/crypto/` — NautilusTrader Strategy subclasses

| File | Class | NT subclass? | Paper-trade ready? | Notes |
|------|-------|:---:|:---:|-------|
| `grid_bot.py` | `GridBotStrategy` | ✅ | ✅ | Tick-snapping via `make_price()`, EMA trend filter, stop-loss |
| `dca_bot.py` | `DCABotStrategy` | ✅ | ✅ | RSI filter + exit, partial sells |
| `shock_guard.py` | `ShockGuardStrategy` | ✅ | ⚠️ | OCO not native in NT; uses market orders for SL/TP |
| `timesfm_swing.py` | `TimesFMSwingStrategy` | ✅ | ⚠️ | TimesFM optional; falls back to EMA cross |
| `timesfm_grid.py` | `TimesFMGridStrategy` | ✅ | ⚠️ | TimesFM for grid boundaries; needs manual P10/P90 override for backtest |
| `rvs_swing.py` | `RVSSwingStrategy` | ✅ | ⚠️ | Requires `RVSSignal` custom data feed — no live source wired |

**Missing from all NT strategies:**
- Portfolio-level drawdown circuit breaker (per-strategy stops exist, but no shared guard)
- Max position size as fraction of total account equity (hard-coded `Decimal` quantities only)
- Explicit Binance Testnet compatibility test (min LOT_SIZE, min notional)

### `competition/agent-N/roundN/strategy.py` — pandas-only simulations

All 50 files (5 agents × 10 rounds) use the same pattern:

```python
def run_backtest(start: str, end: str, initial_capital: float = 1000.0) -> dict:
    # Calls https://api.binance.com/api/v3/klines at runtime
    # Runs custom pandas event loop
    # Returns {"final_equity": ..., "total_return_pct": ..., ...}
```

**They are NOT NautilusTrader Strategy subclasses.** They cannot be "refactored" — they
would need a complete reimplementation to plug into `TradingNode`.

| Agent | Approach | Best round | Why it can't be trivially converted |
|-------|----------|:---:|------|
| Agent 1 (Quant) | EMA crossover, RSI mean-reversion | R1 (+48%) | Daily bar logic; NT needs live subscriptions, not static arrays |
| Agent 2 (Sentiment) | Volume/price anomaly, fear-greed | R8 (+25%) | Sentiment data has no live source wired |
| Agent 3 (Macro) | Regime detection, trend following | R7 | Regime logic uses whole-history lookbacks |
| Agent 4 (ML) | Walk-forward ML, feature engineering | R4 | ML model fit happens inside `run_backtest()` at eval time |
| Agent 5 (Hybrid) | Multi-strategy ensemble, 1.5× leverage | R10 | Tournament selection happens at runtime using the eval period |

### `competition/agent-N/strategies/round1.py`

These 5 files were supposed to be the "live-pluggable" versions of the first round. They
are **still pandas/requests simulations**, not NT Strategy subclasses. They use
`requests.get(binance_api_url)` and pandas DataFrames directly.

---

## Gaps by Category

### Gap 1 — Competition evaluator uses hardcoded absolute paths

Rounds 2–10 evaluators hardcode `/Users/rc/Projects/workspace/nautilus-trading/...` in
both agent strategy paths and output file paths. This breaks when the repo is cloned to
any other location (or run from a git worktree).

**Fixed in this PR:** All evaluators updated to use `Path(__file__).parent` as the base.

### Gap 2 — Evaluator never uses NautilusTrader

The `evaluate.py` evaluator (and archived `archive/evaluate_round*.py` files) calls `mod.run_backtest(start, end, capital)`. NautilusTrader
is never imported. The competition results are from a custom pandas simulation, not from
the same engine that will run live.

**Risk:** A strategy that "wins" the competition may behave very differently when deployed
via `TradingNode` due to:
- Fill model differences (pandas assumes perfect fill at close; NT uses bid/ask spread)
- Latency and partial fills not modeled
- Position sizing differences (pandas uses float equity; NT uses `Quantity` with precision)
- Fee model differences

**Fixed in this PR:** `evaluate.py` uses `BacktestEngine` for NT-based evaluation.
Round 11+ strategies must be NT subclasses (see `ROUND11_CONTRACT.md`).

### Gap 3 — No strategy interface contract documented

There is no document specifying what a competition strategy must implement to be
deployable. Agents have been free to write any Python code.

**Fixed in this PR:** `ROUND11_CONTRACT.md` defines the required interface.

### Gap 4 — No strategy validator

There is no code to check whether a submitted strategy file:
- Is a valid NautilusTrader `Strategy` subclass
- Has a matching `StrategyConfig` with `frozen=True`
- Implements the required lifecycle methods
- Has proper risk management (stop-loss, position limits)

**Fixed in this PR:** `validate_strategy.py` performs static + import-time validation.

### Gap 5 — Missing portfolio-level risk management in NT strategies

Each NT strategy in `strategies/crypto/` has individual position-level risk (stop-loss per
trade) but none have:
- A maximum drawdown circuit breaker that halts the strategy
- Account-equity-based position sizing (all use fixed `Decimal` quantities)
- Hard cap on total capital deployed

**Fixed in this PR:** `strategies/crypto/risk_guard.py` provides a `RiskGuard` mixin.
All six NT strategies updated to use it.

### Gap 6 — Binance Testnet-specific constraints not validated

The `live/runner.py` correctly uses `BinanceEnvironment.TESTNET` and `BinanceKeyType.ED25519`.
But strategies do not verify:
- `LOT_SIZE` filter (minimum quantity)
- `MIN_NOTIONAL` filter (minimum order value)
- `PRICE_FILTER` (tick size) — partially handled by `make_price()` in GridBot

The testnet has the same filters as production; violations cause order rejections.

**Fixed in this PR:** `strategies/crypto/risk_guard.py` includes Binance filter validation
helpers. Strategies that place orders call `self._validate_order_filters()` before submit.

### Gap 7 — Competition rules say nothing about paper trading

`COMPETITION.md` only specifies backtest return scoring. There is no rule requiring
strategies to be deployable. This means "winning" the competition is a vanity metric
unless round 11+ enforces NT compatibility.

**Fixed in this PR:** `ROUND11_CONTRACT.md` extends the competition rules for R11+.

---

## What Was NOT Fixed (Out of Scope)

- **Rounds 1–10 pandas strategies not converted**: The 50 existing competition strategy
  files are left as-is. They are historical artifacts of the simulation competition.
  Converting them would require reimplementing each agent's logic as an NT Strategy
  subclass — a separate project.

- **RVSSwingStrategy live data source**: The `RVSSignal` data type exists but there is no
  live Reddit/Twitter data feed. This strategy can only run in backtest with synthetic
  signals until a live data provider is implemented.

- **TimesFM in live trading**: TimesFM requires ~8 GB RAM for inference. Not suitable for
  continuous live trading on small accounts. The fallback EMA mode is what will run.

- **Agent 4 ML strategy**: Walk-forward ML cannot be trivially converted to NT because the
  model needs to be fit on training data before deployment. A proper implementation would
  pre-train and serialize the model, then load it in `on_start()`.
