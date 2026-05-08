# Porting Example: Pandas → NautilusTrader Strategy

This walkthrough maps the symbols of `competition/agent-5-hybrid/round10/strategy.py` (the R10 pandas-only winner, BNBUSDT `trend_sma` ensemble) to `strategies/crypto/hybrid_sma_r10.py` (the live-pluggable Strategy). Read this after `SKILL.md`, not before.

## 1. The signal loop

**Old — a pandas row loop computes SMAs and checks entry/exit on each row:**

```python
def run_trend_sma(df, st, sma=50, stop_pct=0.10, trade_from=None):
    df["sma"] = df["close"].rolling(sma).mean()
    for i in range(sma + 2, len(df)):
        row = df.iloc[i]; price = row["close"]; date = df.index[i]
        if st.pos.side is None and price > row["sma"] and price > df.iloc[i-1]["close"]:
            st.buy(price, date); peak_entry = price
        elif st.pos.side == "long":
            peak_entry = max(peak_entry, price)
            if price < row["sma"] or price < peak_entry * (1 - stop_pct):
                st.sell(price, date)
```

**New — Nautilus calls `on_bar` for each closed bar; you keep a rolling buffer of closes and call a pure-logic helper:**

```python
def on_bar(self, bar: Bar) -> None:
    self._closes.append(Decimal(str(bar.close)))
    update_sub_state(self._sub_fast, list(self._closes))
    update_sub_state(self._sub_slow, list(self._closes))
    self._rebalance_to_target(bar)
```

The pure logic lives in `compute_sma()` and `update_sub_state()` so the tests can exercise it without a running engine.

## 2. State tracking

**Old** — custom `State` dataclass with `pos.side`, `pos.entry`, and a floating `peak_entry` local variable per run.

**New** — a `SubState` dataclass per sub-strategy:

```python
@dataclass
class SubState:
    period: int
    stop_pct: Decimal
    is_long: bool = False
    entry_price: Decimal = ZERO
    peak_price: Decimal = ZERO
```

One `SubState` per sub-strategy lets the tests assert state transitions directly and keeps the `on_bar` callback thin.

## 3. Entry/exit via rebalance (not per-sub order)

This is the subtle part — and the reason a naive port does NOT work on Spot.

**Old** — pandas backtest runs two independent `run_trend_sma` calls with different `sma` / `stop_pct`, each maintaining its own position. The ensemble equity is the average of the two sub-equities.

**Problem** — Binance Spot is a net-position venue. You can't hold "two independent longs" — the exchange nets them to one position, and the second entry becomes a no-op at best, a rejected order at worst.

**New — rebalance-to-target-fraction:**

```python
target_fraction = (
    (Decimal("0.5") if self._sub_fast.is_long else ZERO)
    + (Decimal("0.5") if self._sub_slow.is_long else ZERO)
)
target_notional = self._equity_usdt() * target_fraction * self.config.capital_fraction
target_qty = instrument.make_qty(target_notional / Decimal(str(bar.close)))
delta = target_qty - self.cache.position_quantity(self.instrument_id)
if delta != 0:
    side = OrderSide.BUY if delta > 0 else OrderSide.SELL
    order = self.order_factory.market(self.instrument_id, side, abs(delta))
    self.submit_order(order)
```

On every closed bar, compute the desired exposure as `0.5 * sub_fast.is_long + 0.5 * sub_slow.is_long`, size it via `instrument.make_qty`, and submit a single market order for the delta vs the current net position.

## 4. Why the ensemble shape changed

- Spot venues enforce net positioning — parallel longs don't exist.
- Rebalancing to a target fraction produces the same ensemble behaviour as two independent longs in theory, and works correctly on Spot in practice.
- A single rebalance order per bar is also cleaner for order management: no per-sub tracking, no stale state, no fee duplication.

## 5. Capital fraction, not leverage

**Old** — the R10 backtest used `leverage=1.5` as an explicit multiplier on position sizing.

**New** — Binance Spot cannot leverage, so:

```python
capital_fraction: Decimal = Decimal("0.99")
```

This reserves 1% of equity as a buffer for slippage, fees, and rounding — nothing is leveraged, and the `leverage` identifier does not appear anywhere in the port (the validator would flag it if it did).

## 6. Diff summary

- **Lines**: pandas `run_trend_sma` ≈ 18 lines → NautilusTrader port ≈ 250 lines (the extra LOC goes to config, lifecycle methods, pure-logic helpers, and tests)
- **New imports**: `Strategy`, `StrategyConfig`, `Bar`, `BarType`, `OrderSide`, `InstrumentId`, `Instrument`, `Decimal`, `deque`
- **Removed imports**: `pandas`, `numpy` (not needed in the live path — all the math is `Decimal`-based pure logic)
- **Not ported**: the `State.mark()` equity-curve tracking, the `trade_from` backtest fast-forward, the multi-strategy `RUNNERS` dispatch — these are backtest harness concerns, not strategy logic

## 7. What to reuse from your research

Everything in `round<N>/research/` stays. The pandas loop is useful as the reference implementation you diff your Strategy port against — keep it, add a comment saying "this is the research baseline; the live version is strategy.py". The competition judges the Strategy, but the research artifact is what lets someone understand *why* the Strategy looks the way it does.
