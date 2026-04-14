# Crypto Strategy Competition — 10 Rounds

## Rules
- 5 agents, each isolated, no visibility into others' strategies
- Each round: agents get TRAIN + TEST periods, must be profitable on TEST
- Evaluation on HIDDEN period (agents never see this)
- $1,000 starting capital per round
- Agent with most round wins takes the competition

## Agents
| # | Persona | Approach |
|---|---------|----------|
| 1 | Quantitative Trader | Statistical arb, mean reversion, momentum |
| 2 | Sentiment Trader | Fear/greed, behavioral, volume-price patterns |
| 3 | Macro Strategist | Regime detection, trend following, rotation |
| 4 | ML Engineer | Feature engineering, walk-forward ML, ensembles |
| 5 | Hybrid Strategist | Multi-signal ensemble, adaptive weighting |

## Round Schedule

| Round | Train Period | Test Period | Eval Period (HIDDEN) |
|-------|-------------|-------------|---------------------|
| 1 | Jan-Jun 2024 | Jul-Sep 2024 | Oct-Dec 2024 |
| 2 | Apr-Sep 2024 | Oct-Dec 2024 | Jan-Mar 2025 |
| 3 | Jul-Dec 2024 | Jan-Mar 2025 | Apr-Jun 2025 |
| 4 | Oct 2024-Mar 2025 | Apr-Jun 2025 | Jul-Sep 2025 |
| 5 | Jan-Jun 2025 | Jul-Sep 2025 | Oct-Dec 2025 |
| 6 | Apr-Sep 2025 | Oct-Dec 2025 | Jan-Mar 2026 |
| 7 | Jan-Jun 2024 | Jul-Dec 2024 | Jan-Mar 2025 |
| 8 | Jul 2024-Jun 2025 | Jul-Sep 2025 | Oct-Dec 2025 |
| 9 | Jan-Dec 2025 | Jan-Feb 2026 | Mar-Apr 2026 |
| 10 | 2024 full year | Jan-Jun 2025 | Jul-Dec 2025 |

## Scoring
- Round winner = highest return on hidden eval period (must be positive)
- If no agent is positive, no winner for that round
- Final winner = most round wins. Tiebreaker: cumulative eval return.

## Submission requirements (R11+)

Starting with Round 11, every submission ships **two deliverables** per round:

1. **Research artifact** (`research/` directory) — any tool the agent likes
   (pandas, Jupyter notebook, custom backtest harness, etc.). This is where
   data exploration, parameter tuning, and signal validation live.
2. **NautilusTrader-pluggable strategy module** (`strategy.py`) — a
   `Strategy` subclass plus its `StrategyConfig` that can be wired into a
   `TradingNode` without modification. The same class runs in
   `BacktestEngine` **and** live paper trading on Binance.

The winning strategy each round must BE the live-runnable strategy, not a
pandas-only backtest that has to be hand-ported afterwards. The evaluator
imports `strategy.py` via the `MANIFEST`, runs it on the hidden eval period
through NautilusTrader, and scores the result.

### Required file layout (per submission)

```
competition/agent-N-<persona>/round11/
├── strategy.py          # Strategy + Config + MANIFEST
├── tests/
│   ├── __init__.py
│   └── test_strategy.py # pytest-runnable
├── research/
│   ├── notes.md         # rationale + research summary
│   ├── explore.ipynb    # optional
│   └── backtest.py      # optional pandas/custom harness
└── README.md            # 1-paragraph summary + MANIFEST values
```

The `competition/TEMPLATE/` directory is the canonical reference layout.
Copy it as your starting point.

### Required module-level `MANIFEST`

At the top of `strategy.py`, every submission MUST declare a module-level
dict named `MANIFEST` with the exact six keys below. The validator and
evaluator read it to instantiate your strategy without inspecting the
source file:

```python
MANIFEST: dict[str, Any] = {
    "strategy_class_name": "MyStrategy",            # class in this module
    "config_class_name": "MyConfig",                # config class in this module
    "instrument_id": "BNBUSDT.BINANCE",             # must be in round's allowlist
    "bar_type": "BNBUSDT.BINANCE-1-DAY-LAST-EXTERNAL",
    "default_config": {                             # kwargs for instantiating config
        "sma_fast": 20,
        "sma_slow": 30,
    },
    "description": "One or two sentences of what the strategy does",
}
```

Notes:

- `strategy_class_name` and `config_class_name` must resolve to classes
  defined in the same `strategy.py` module.
- `instrument_id` and `bar_type` are stringified so the validator can parse
  them with `InstrumentId.from_str` / `BarType.from_str`.
- `default_config` must NOT include `instrument_id` or `bar_type` — those
  are injected by the evaluator from the manifest itself, guaranteeing the
  config used in backtest matches the one the live node wires up.
- `description` is surfaced in the leaderboard.

### Hard constraints (live-trading safety)

These come from blockers already hit in Binance testnet paper trading.
Violating any of them will fail validation:

- **Spot-only** — no margin, futures, leverage, or short positions.
- **Long-only** unless the agent explicitly flags a short requirement in
  `research/notes.md` AND the round allowlist permits it.
- **Prices** must go through `instrument.make_price(raw)`, NEVER
  `round(x, price_precision)`. `price_precision` is display decimals;
  `price_increment` is the actual tick size — they differ for many
  Binance instruments and a round() will silently produce off-tick prices
  that get rejected by the venue's PRICE_FILTER.
- **Quantities** must go through `instrument.make_qty(raw)`, not
  hand-rounding.
- Use `self.log.info / warning / error`, NOT `print()`.
- `Decimal` for all monetary values, NEVER `float`.
- Config class must extend `StrategyConfig` with `frozen=True`.
- Type hints required throughout (`from __future__ import annotations`
  is fine).
- Line length 100 (ruff default for this repo).
- Module ships with `tests/test_strategy.py` that `pytest` can run and
  pass on a cold checkout.

### What changed from R10

Through R10, agents submitted pandas-only backtest scripts exposing:

```python
def run_backtest(start: str, end: str, initial_capital: float) -> dict: ...
```

This format is now **deprecated**. The R10 winner (Agent 5's hybrid SMA
ensemble, +93.08% on BNBUSDT hidden eval) was beautiful in a notebook but
not runnable on Binance — it had to be hand-ported into
`strategies/crypto/hybrid_sma_r10.py` before it could paper-trade. The
R11 contract collapses that porting step: the winning `Strategy` subclass
IS already the live strategy.

Agents may still use pandas, Jupyter, or any other tool for research —
that all goes under `research/`. They simply must also ship a `strategy.py`
that follows the contract above.

Reference port:
[strategies/crypto/hybrid_sma_r10.py](../strategies/crypto/hybrid_sma_r10.py)
shows the canonical translation from a pandas backtest into a
NautilusTrader-pluggable strategy (state machines via `SubState` +
`register_indicator_for_bars` + `instrument.make_qty` rebalancing).
