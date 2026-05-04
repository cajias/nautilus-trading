"""Agent 5 R10 Hybrid SMA Ensemble -- port of competition winner to NautilusTrader live.

Ports `run_trend_sma` from competition/agent-5-hybrid/round10/strategy.py as a 50/50
ensemble of two SMA trend-following sub-strategies, adapted to Binance Spot (netted).

Per sub-strategy (long-only):
    - Entry: close > SMA(n) AND close > prev_close AND sub-strategy currently flat
    - Exit:  close < SMA(n) OR close < peak_since_entry * (1 - stop_pct)

The two sub-strategies (sub_fast = SMA(20)/stop 7%, sub_slow = SMA(30)/stop 8%) run
independently in state-space. Because Spot is net-position, we can't hold two
independent BNB positions; instead each sub votes for 50% of equity, and the
strategy rebalances to the combined target on each closed daily bar.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from decimal import Decimal

from nautilus_trader.config import PositiveInt, StrategyConfig
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.trading.strategy import Strategy

from nautilus_trading.cli._strategy_specs import HybridSMAConfigBuilder, StrategySpec

ZERO = Decimal("0")


# ---------------------------------------------------------------------------
# Pure-logic helpers (testable without a NautilusTrader runtime)
# ---------------------------------------------------------------------------


def compute_sma(closes: list[Decimal] | tuple[Decimal, ...], period: int) -> Decimal | None:
    """Return the simple moving average of the last ``period`` closes, or None.

    Returns None when fewer than ``period`` samples are available.
    """
    if period <= 0 or len(closes) < period:
        return None
    window = closes[-period:]
    total = sum(window, start=ZERO)
    return total / Decimal(period)


@dataclass
class SubState:
    """Per-sub-strategy long-only state machine."""

    period: int
    stop_pct: Decimal
    is_long: bool = False
    entry_price: Decimal = ZERO
    peak_price: Decimal = ZERO


def update_sub_state(
    sub: SubState,
    closes: list[Decimal],
) -> SubState:
    """Apply one bar of signal logic to a SubState in place and return it.

    Implements the same rules as ``run_trend_sma`` from the backtest source:
        - Long entry when flat: close > SMA(period) AND close > prev_close
        - Long exit:            close < SMA(period) OR close < peak * (1 - stop_pct)

    Requires at least ``period + 1`` closes (to have a current and previous bar
    plus a full SMA window). With fewer samples the state is left untouched.
    """
    if len(closes) < sub.period + 1:
        return sub

    sma = compute_sma(closes, sub.period)
    if sma is None:
        return sub

    current = closes[-1]
    previous = closes[-2]

    if not sub.is_long:
        if current > sma and current > previous:
            sub.is_long = True
            sub.entry_price = current
            sub.peak_price = current
    else:
        if current > sub.peak_price:
            sub.peak_price = current
        trailing_stop = sub.peak_price * (Decimal(1) - sub.stop_pct)
        if current < sma or current < trailing_stop:
            sub.is_long = False
            sub.entry_price = ZERO
            sub.peak_price = ZERO

    return sub


def compute_target_fraction(subs: list[SubState]) -> Decimal:
    """Average the per-sub long flags into an exposure fraction in [0, 1]."""
    if not subs:
        return ZERO
    n = Decimal(len(subs))
    longs = sum((Decimal(1) for s in subs if s.is_long), start=ZERO)
    return longs / n


def compute_rebalance_delta(
    *,
    target_qty: Decimal,
    current_qty: Decimal,
    size_increment: Decimal,
) -> Decimal:
    """Return the signed quantity delta to submit, or 0 if below the increment.

    Positive => buy, negative => sell. Returns ZERO when |delta| is smaller
    than ``size_increment`` (no order will fit through the venue's filter).
    """
    delta = target_qty - current_qty
    if abs(delta) < size_increment:
        return ZERO
    return delta


# ---------------------------------------------------------------------------
# Strategy
# ---------------------------------------------------------------------------


class HybridSMAR10Config(StrategyConfig, frozen=True):
    """Configuration for the Hybrid SMA R10 ensemble strategy."""

    instrument_id: InstrumentId
    bar_type: BarType
    sma_fast: PositiveInt = 20
    sma_slow: PositiveInt = 30
    stop_fast: Decimal = Decimal("0.07")
    stop_slow: Decimal = Decimal("0.08")
    capital_fraction: Decimal = Decimal("0.99")


class HybridSMAR10Strategy(Strategy):
    """50/50 SMA-trend ensemble adapted to net Spot positions.

    Two sub-strategies vote independently each daily bar; the combined
    target exposure (0%, 50%, or 100% of equity * capital_fraction) is
    realised via a single MARKET order on the BNBUSDT instrument.
    """

    def __init__(self, config: HybridSMAR10Config) -> None:
        super().__init__(config)
        self.instrument: Instrument | None = None

        self._sub_fast = SubState(period=int(config.sma_fast), stop_pct=config.stop_fast)
        self._sub_slow = SubState(period=int(config.sma_slow), stop_pct=config.stop_slow)

        max_period = max(int(config.sma_fast), int(config.sma_slow))
        # Need ``period + 2`` closes for SMA + previous bar comparisons.
        self._closes: deque[Decimal] = deque(maxlen=max_period + 2)

    # -- Lifecycle ---------------------------------------------------------------

    def on_start(self) -> None:
        self.instrument = self.cache.instrument(self.config.instrument_id)
        if self.instrument is None:
            self.log.error(f"Could not find instrument for {self.config.instrument_id}")
            self.stop()
            return

        self.log.info(
            "Hybrid SMA R10 starting | "
            f"sub_fast=SMA{self.config.sma_fast}/stop={self.config.stop_fast} "
            f"sub_slow=SMA{self.config.sma_slow}/stop={self.config.stop_slow} "
            f"capital_fraction={self.config.capital_fraction}",
        )

        self.subscribe_bars(self.config.bar_type)
        self.log.info(f"Subscribed to bars: {self.config.bar_type}")

    def on_bar(self, bar: Bar) -> None:
        close = Decimal(str(bar.close))
        self._closes.append(close)

        closes_list = list(self._closes)
        update_sub_state(self._sub_fast, closes_list)
        update_sub_state(self._sub_slow, closes_list)

        target_fraction = compute_target_fraction([self._sub_fast, self._sub_slow])
        self.log.info(
            f"Bar close={close} sub_fast.long={self._sub_fast.is_long} "
            f"sub_slow.long={self._sub_slow.is_long} target={target_fraction}",
        )

        self._rebalance(close, target_fraction)

    def on_stop(self) -> None:
        self.cancel_all_orders(self.config.instrument_id)
        self.close_all_positions(self.config.instrument_id)
        self.unsubscribe_bars(self.config.bar_type)

    def on_reset(self) -> None:
        self._closes.clear()
        self._sub_fast = SubState(
            period=int(self.config.sma_fast),
            stop_pct=self.config.stop_fast,
        )
        self._sub_slow = SubState(
            period=int(self.config.sma_slow),
            stop_pct=self.config.stop_slow,
        )

    # -- Rebalancing -------------------------------------------------------------

    def _equity_in_quote(self) -> Decimal:
        """Best-effort total equity in the instrument's quote currency."""
        if self.instrument is None:
            return ZERO
        account = self.portfolio.account(self.instrument.venue)
        if account is None:
            return ZERO
        balance = account.balance_total(self.instrument.quote_currency)
        if balance is None:
            return ZERO
        return Decimal(str(balance.as_decimal()))

    def _current_qty(self) -> Decimal:
        """Net signed position quantity for the configured instrument."""
        net = self.portfolio.net_position(self.config.instrument_id)
        if net is None:
            return ZERO
        return Decimal(str(net))

    def _rebalance(self, current_price: Decimal, target_fraction: Decimal) -> None:
        if self.instrument is None or current_price <= ZERO:
            return

        equity = self._equity_in_quote()
        if equity <= ZERO:
            self.log.warning("Equity unavailable; skipping rebalance")
            return

        target_notional = equity * target_fraction * self.config.capital_fraction
        target_qty_raw = target_notional / current_price

        try:
            target_qty_obj = self.instrument.make_qty(target_qty_raw)
        except ValueError:
            target_qty_obj = self.instrument.make_qty(0)
        target_qty = Decimal(str(target_qty_obj.as_decimal()))

        current_qty = self._current_qty()
        size_increment = Decimal(str(self.instrument.size_increment.as_decimal()))

        delta = compute_rebalance_delta(
            target_qty=target_qty,
            current_qty=current_qty,
            size_increment=size_increment,
        )
        if delta == ZERO:
            return

        side = OrderSide.BUY if delta > ZERO else OrderSide.SELL
        try:
            qty = self.instrument.make_qty(abs(delta))
        except ValueError:
            return

        order = self.order_factory.market(
            instrument_id=self.config.instrument_id,
            order_side=side,
            quantity=qty,
            time_in_force=TimeInForce.GTC,
        )
        self.submit_order(order)
        self.log.info(
            f"Rebalance {side.name} qty={qty} "
            f"(current={current_qty} target={target_qty} fraction={target_fraction})",
        )


STRATEGY_SPEC = StrategySpec(
    name="hybrid_sma_r10",
    builder=HybridSMAConfigBuilder(),
    strategy_path="strategies.crypto.hybrid_sma_r10:HybridSMAR10Strategy",
    config_path="strategies.crypto.hybrid_sma_r10:HybridSMAR10Config",
)
