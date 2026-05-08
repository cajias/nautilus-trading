---
name: crypto-competition
description: Use when producing or porting a submission for the nautilus-trading crypto strategy competition (R11+). Trigger phrases include "competition submission", "crypto round", "port strategy to NautilusTrader", "R11 submission", "round evaluator".
---

# Crypto Competition Submission (R11+)

## When to use this skill

Use this skill whenever you are producing a new submission for the crypto strategy competition, porting an older pandas-only backtest into the R11+ format, or reviewing a submission before it goes to the evaluator. Read the whole workflow before writing any code — the hard constraints below come from real Binance incidents and are not optional.

## The contract in one paragraph

Each submission ships **two deliverables**: a research artifact (any tool — pandas, Jupyter, custom backtests) and a **NautilusTrader-pluggable Strategy subclass**. The Strategy is the same code that will paper-trade on Binance Spot testnet if your submission wins — there is no "porting step" after the round closes. Agents are free to explore however they like, but the final `strategy.py` must satisfy eight hard constraints derived from gotchas we hit during paper-trade bring-up.

## Workflow

### 1. Read the contract

- `competition/COMPETITION.md` — authoritative R11+ spec (the "Submission requirements" section)
- `competition/TEMPLATE/` — minimal working Strategy scaffold you should fork
- `strategies/crypto/hybrid_sma_r10.py` — the canonical worked port (pandas R10 winner → NautilusTrader Strategy)

### 2. Explore with any tool

Belongs under `round<N>/research/`. Pandas, notebooks, custom backtests — anything that helps you arrive at a signal. This artifact is not graded directly, but the evaluator needs enough context to understand your rationale.

### 3. Port the signal to a Strategy subclass

Scaffold from `competition/TEMPLATE/strategy.py`. For the structural mapping from a pandas loop to an `on_bar` callback, read `strategies/crypto/hybrid_sma_r10.py` end-to-end and then consult `PORTING_EXAMPLE.md` in this skill for the condensed walkthrough.

### 4. Apply the hard constraints

See "Hard constraints" below. Each one exists because of a real incident — don't skip them.

### 5. Write tests

Mirror `tests/test_hybrid_sma_r10.py`: pure-logic tests that exercise indicator math, entry/exit rules, and rebalance sizing **without** instantiating a `BacktestEngine`. The tests live at `round<N>/tests/test_strategy.py` and pytest must pass.

### 6. Self-validate

```
cd nautilus && uv run python ../competition/validate_submission.py ../competition/agent-N-<persona>/round<N>
```

Exit code 0 means all hard constraints pass. If it fails, read the per-check diagnostics and fix until it passes — do not submit a failing strategy.

### 7. Submit

Place the submission at `competition/agent-N-<persona>/round<N>/` with this layout:

```
round<N>/
├── strategy.py          # Strategy + Config + MANIFEST
├── tests/
│   └── test_strategy.py
├── research/
│   ├── notes.md
│   ├── explore.ipynb    # optional
│   └── backtest.py      # optional
└── README.md            # 1-paragraph summary
```

## MANIFEST schema

At the top of `strategy.py`, declare a module-level `MANIFEST` dict with exactly these six keys:

| Key | Meaning |
|---|---|
| `strategy_class_name` | Name of the `Strategy` subclass in this module |
| `config_class_name` | Name of the frozen `StrategyConfig` subclass |
| `instrument_id` | Instrument ID string, e.g. `"BNBUSDT.BINANCE"` |
| `bar_type` | Bar type string, e.g. `"BNBUSDT.BINANCE-1-DAY-LAST-EXTERNAL"` |
| `default_config` | kwargs dict for instantiating the config class |
| `description` | One or two sentences describing what the strategy does |

The evaluator imports your Strategy via this manifest — missing or mistyped keys cause an automatic `INVALID` status.

## Hard constraints (and why each one exists)

1. **Spot-only; no leverage / margin / futures / short selling.** The evaluator runs against Binance Spot testnet; anything else fails at paper trading.
2. **Long-only unless explicitly flagged.** Spot is a net-position venue — two independent longs collapse to one position. Use a rebalance-to-target-fraction model instead of running parallel longs.
3. **Prices must go through `instrument.make_price(raw)`, NOT `round(x, price_precision)`.** `price_precision` is *display* decimals; `price_increment` is the actual on-wire tick. They differ on many pairs (SOLUSDT: precision=8, increment=0.01). Off-tick prices get rejected by Binance with `-1013 PRICE_FILTER`.
4. **Quantities must go through `instrument.make_qty(raw)`.** Same reason for lot size — the lot increment is not necessarily 10^-precision.
5. **Logging uses `self.log.*` (info/warning/error), NOT `print`.** NautilusTrader's logger is captured in backtests and live runs; `print` is not, so you lose context when debugging.
6. **Monetary values are `Decimal`, never `float`.** Float drift accumulates across many fills and breaks reconciliation.
7. **Config class extends `StrategyConfig` with `frozen=True`.** msgspec-based configs require it; it also prevents runtime mutation bugs where an indicator accidentally rewrites its own parameters.
8. **Ships with a pytest-runnable `tests/test_strategy.py`.** The validator runs your tests before the evaluator will even look at the strategy — failing tests = automatic `INVALID`.

## Common porting pitfalls

When converting a pandas backtest into a Strategy subclass, these are the symbol-level mappings you'll need. See `PORTING_EXAMPLE.md` in this skill for code-level detail.

| Old (pandas backtest) | New (NautilusTrader Strategy) |
|---|---|
| `for i, row in df.iterrows():` loop | `on_bar(self, bar)` callback |
| `State.buy(price, date)` | `order_factory.market(...)` + `submit_order(...)` |
| `State.sell(price, date)` | Same, but `OrderSide.SELL` |
| Manual position tracking in a DataFrame | `self.cache.position_quantity(instrument_id)` |
| `quantity = equity / price` (raw float) | `instrument.make_qty(raw_qty)` (tick-aligned) |
| `1.5x leverage` | `capital_fraction: Decimal = Decimal("0.99")` (Spot can't leverage) |
| Two parallel `State` objects (ensemble) | `target_fraction = 0.5 * sub1.is_long + 0.5 * sub2.is_long` + single rebalance order |
| `df["sma"] = df["close"].rolling(n).mean()` | `compute_sma(self._closes, period)` updated per-bar |
| `print("buy", price)` | `self.log.info(f"buy {price}")` |

## Reference files

- `competition/COMPETITION.md` — R11+ contract
- `competition/TEMPLATE/strategy.py` — minimal Strategy scaffold
- `competition/TEMPLATE/tests/test_strategy.py` — minimal test pattern
- `strategies/crypto/hybrid_sma_r10.py` — canonical worked port (full 250-line example)
- `strategies/crypto/grid_bot.py` — another real Strategy (different pattern)
- `tests/test_hybrid_sma_r10.py` — 24-test pure-logic test suite pattern
- `competition/validate_submission.py` — the static+dynamic validator

## Checklist

Run through `CHECKLIST.md` in this skill before submitting. If any box is unchecked, your submission is not ready.
