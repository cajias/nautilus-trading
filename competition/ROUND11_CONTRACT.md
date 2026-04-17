# Round 11+ Strategy Contract

_Effective: Round 11 onwards_

## Why This Matters

Rounds 1–10 evaluated pure Python backtests that never touched NautilusTrader.
Starting Round 11, the winning strategy must be deployable to Binance Testnet via
`TradingNode` without modification. This document is the contract that strategies must
satisfy to participate.

---

## Required Interface

Every competition strategy must be a Python module containing **exactly two** exported
symbols:

### 1. A `StrategyConfig` subclass

```python
from nautilus_trader.config import StrategyConfig

class MyStrategyConfig(StrategyConfig, frozen=True):
    instrument_id: InstrumentId
    bar_type: BarType
    trade_size: Decimal
    # ... additional params
```

- Must extend `nautilus_trader.config.StrategyConfig`
- Must have `frozen=True`
- Must include `instrument_id`, `bar_type`, and `trade_size` fields (minimum)

### 2. A `Strategy` subclass

```python
from nautilus_trader.trading.strategy import Strategy

class MyStrategy(Strategy):
    def __init__(self, config: MyStrategyConfig) -> None:
        super().__init__(config)

    def on_start(self) -> None:
        # Subscribe to data, register indicators
        ...

    def on_bar(self, bar: Bar) -> None:
        # Signal logic + order submission
        ...

    def on_stop(self) -> None:
        # Cancel orders, close positions
        self.cancel_all_orders(self.config.instrument_id)
        self.close_all_positions(self.config.instrument_id)
```

- Must extend `nautilus_trader.trading.strategy.Strategy`
- Must implement `on_start()`, `on_bar()`, and `on_stop()`
- Must call `super().__init__(config)` in `__init__`

---

## Import Path Convention

Strategy module must be placed at:
```
competition/agent-N-<name>/round11/strategy.py
```

The config and strategy classes must be importable via:
```python
from competition.agent_N_name.round11.strategy import MyStrategyConfig, MyStrategy
```

Or via `ImportableStrategyConfig`:
```python
ImportableStrategyConfig(
    strategy_path="competition.agent_1_quant.round11.strategy:MyStrategy",
    config_path="competition.agent_1_quant.round11.strategy:MyStrategyConfig",
    config={
        "instrument_id": "BTCUSDT.BINANCE",
        "bar_type": "BTCUSDT.BINANCE-1-HOUR-LAST-EXTERNAL",
        "trade_size": "0.001",
    },
)
```

---

## Mandatory Risk Management

All strategies must implement the following safety constraints. These are validated by
`validate_submission.py` before evaluation.

| Requirement | Minimum | Notes |
|-------------|---------|-------|
| Stop-loss | Required | ATR-based or fixed %; submit via order or check in `on_bar` |
| Max position size | ≤ 50% of starting capital | Enforced by evaluator at order time |
| Max drawdown halt | ≤ 20% | Strategy must call `self.stop()` or stop submitting orders |
| `on_stop()` cleanup | Required | Must cancel open orders + close positions |

Strategies that fail these checks are **disqualified** (not just penalized).

---

## Binance Testnet Constraints

Strategies run against Binance Spot Testnet. Key constraints:

| Constraint | Value |
|------------|-------|
| Minimum order size (BTCUSDT) | 0.00001 BTC |
| Minimum notional (BTCUSDT) | 5 USDT |
| Tick size (BTCUSDT) | 0.01 USDT |
| Lot step size (BTCUSDT) | 0.00001 BTC |
| Rate limit | 1200 requests/min weight |
| Order types allowed | LIMIT, MARKET, STOP_LOSS_LIMIT |
| Leverage | None (Spot only) |
| Starting capital | $500 USDT |

Use `self.instrument.make_price()` and `self.instrument.make_qty()` to snap values to
exchange filters — do NOT hardcode precision.

---

## Evaluation Process (Round 11+)

The evaluator (`evaluate.py --round 11`) uses **NautilusTrader's `BacktestEngine`**, not a
custom pandas simulation. This means:

1. Data is fetched from the ParquetDataCatalog (or downloaded once and cached)
2. Strategies run through the full NT event loop with realistic fill simulation
3. Results include NT-native metrics (fills, slippage, commissions from `Account`)
4. The same strategy file is then deployed to Binance Testnet for live paper trading

### Scoring

Same as rounds 1–10:
- Hidden eval period return (must be positive to win)
- Tiebreaker: Sharpe ratio from backtest period
- Disqualified if: strategy errors during backtest, violates risk constraints

---

## Validation

Before submitting, run:

```bash
cd nautilus && uv run python ../competition/validate_submission.py \
    competition/agent-1-quant/round11
```

All checks must pass for the submission to be accepted.

---

## Example

See `strategies/crypto/grid_bot.py` for a complete reference implementation that
satisfies all requirements.
