"""KronosStrategy — NautilusTrader Strategy driven by KronosActor signals.

Signal flow:
    KronosActor (on_bar) → publish_data(KronosSignal) → MessageBus
    KronosStrategy.on_data() ← subscribe_data(KronosSignal)

Risk management:
    - Confidence filter: ignores signals below min_confidence
    - Return magnitude filter: ignores signals below min_predicted_return_pct
    - Stop-loss: percentage-based, checked on every bar
    - Take-profit: percentage-based, checked on every bar
    - Max drawdown circuit breaker: halts new entries if peak-to-trough
      exceeds max_drawdown_pct (resets when flat)
    - Fallback EMA crossover: engages when no Kronos signals arrive
      (model unavailable, actor not added, or still warming up)

Backtest vs live:
    - In backtest: add both KronosActor and KronosStrategy to BacktestEngine
    - In live: add both to TradingNode; actor publishes live-inferred signals
    - See strategies/crypto/kronos/backtest.py for a full backtest example
"""

from __future__ import annotations

from decimal import Decimal

from nautilus_trader.config import PositiveInt, StrategyConfig
from nautilus_trader.indicators import ExponentialMovingAverage
from nautilus_trader.model.data import Bar, BarType, DataType
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.trading.strategy import Strategy

from strategies.crypto.kronos.signal import KronosSignal


class KronosStrategyConfig(StrategyConfig, frozen=True):
    """Configuration for KronosStrategy.

    Parameters
    ----------
    instrument_id : InstrumentId
        Trading instrument (must match KronosActor's instrument_id).
    bar_type : BarType
        Bar type for exit checks (must match KronosActor's bar_type).
    trade_size : Decimal
        Base order quantity (e.g. Decimal("0.01") for 0.01 BTC).

    Signal filters
    --------------
    min_confidence : float
        Minimum actor confidence to act (default 0.55 — slightly above coin-flip).
    min_predicted_return_pct : float
        Minimum |predicted return| to act (default 0.008 = 0.8%).
        Filters noise near the forecast zero-crossing.

    Risk management
    ---------------
    stop_loss_pct : float
        Stop loss as fraction of entry price (default 2%).
    take_profit_pct : float
        Take profit as fraction of entry price (default 4%).
    max_drawdown_pct : float
        Max peak-to-trough drawdown circuit breaker (default 10%).
        No new entries until flat and drawdown resets.

    EMA fallback
    ------------
    fallback_ema_fast_period : PositiveInt
        Fast EMA period for fallback crossover mode (default 20).
    fallback_ema_slow_period : PositiveInt
        Slow EMA period for fallback crossover mode (default 50).
    use_fallback_ema : bool
        If True, use EMA crossover when no Kronos signal has arrived.
        Set to False for pure-Kronos mode (safer when model is always available).
    fallback_warmup_bars : PositiveInt
        Bars to wait before deciding the actor is absent and activating fallback.
    """

    instrument_id: InstrumentId
    bar_type: BarType
    trade_size: Decimal

    # Signal filters
    min_confidence: float = 0.55
    min_predicted_return_pct: float = 0.008

    # Risk management
    stop_loss_pct: float = 0.02
    take_profit_pct: float = 0.04
    max_drawdown_pct: float = 0.10

    # EMA fallback
    fallback_ema_fast_period: PositiveInt = 20
    fallback_ema_slow_period: PositiveInt = 50
    use_fallback_ema: bool = True
    fallback_warmup_bars: PositiveInt = 60


class KronosStrategy(Strategy):
    """Swing trading strategy driven by Kronos foundation model signals.

    Subscribes to KronosSignal objects published by KronosActor via the
    NautilusTrader MessageBus. Applies confidence + magnitude filters,
    then enters long or short positions with stop-loss, take-profit, and
    a peak-drawdown circuit breaker.

    Falls back to a simple EMA crossover when no Kronos signals arrive
    (e.g. model not installed, actor not added to engine, still warming up).
    """

    def __init__(self, config: KronosStrategyConfig) -> None:
        super().__init__(config)
        self.instrument: Instrument | None = None

        # EMA indicators (used for exits and fallback)
        self._ema_fast = ExponentialMovingAverage(config.fallback_ema_fast_period)
        self._ema_slow = ExponentialMovingAverage(config.fallback_ema_slow_period)

        # Position tracking
        self._entry_price: float | None = None
        self._current_price: float = 0.0

        # Drawdown circuit breaker
        self._peak_equity: float = 0.0
        self._circuit_breaker_tripped: bool = False

        # Signal state
        self._kronos_signal_count: int = 0
        self._bars_elapsed: int = 0
        self._prev_fast_above_slow: bool | None = None  # for fallback crossover

    # -- Lifecycle ---------------------------------------------------------------

    def on_start(self) -> None:
        self.instrument = self.cache.instrument(self.config.instrument_id)
        if self.instrument is None:
            self.log.error(f"Instrument not found: {self.config.instrument_id}")
            self.stop()
            return

        # Register EMA indicators (needed for fallback and exit logic)
        self.register_indicator_for_bars(self.config.bar_type, self._ema_fast)
        self.register_indicator_for_bars(self.config.bar_type, self._ema_slow)
        self.subscribe_bars(self.config.bar_type)

        # Subscribe to KronosSignal from the actor (scoped to this instrument)
        self.subscribe_data(
            DataType(
                KronosSignal,
                metadata={"instrument_id": str(self.config.instrument_id)},
            ),
        )

        self.log.info(
            f"KronosStrategy started: {self.config.instrument_id} "
            f"SL={self.config.stop_loss_pct:.1%} "
            f"TP={self.config.take_profit_pct:.1%} "
            f"MaxDD={self.config.max_drawdown_pct:.1%} "
            f"fallback_ema={self.config.use_fallback_ema}"
        )

    def on_bar(self, bar: Bar) -> None:
        self._current_price = float(bar.close)
        self._bars_elapsed += 1

        if not self.indicators_initialized():
            return

        # Update drawdown tracker on every bar
        self._update_drawdown_tracker()

        # Check exits (stop-loss / take-profit)
        self._check_exits()

        # EMA fallback: activates after warmup if no Kronos signals received
        if (
            self.config.use_fallback_ema
            and self._kronos_signal_count == 0
            and self._bars_elapsed >= self.config.fallback_warmup_bars
        ):
            self._ema_crossover_fallback()

    def on_data(self, data: object) -> None:
        """Handle incoming KronosSignal published by the actor."""
        if not isinstance(data, KronosSignal):
            return
        if not self.indicators_initialized():
            return

        self._kronos_signal_count += 1
        self.log.debug(f"Received Kronos signal #{self._kronos_signal_count}: {data}")

        self._handle_kronos_signal(data)

    def on_stop(self) -> None:
        self.cancel_all_orders(self.config.instrument_id)
        self.close_all_positions(self.config.instrument_id)
        self.unsubscribe_bars(self.config.bar_type)
        self.unsubscribe_data(
            DataType(
                KronosSignal,
                metadata={"instrument_id": str(self.config.instrument_id)},
            ),
        )

    def on_reset(self) -> None:
        self._ema_fast.reset()
        self._ema_slow.reset()
        self._entry_price = None
        self._current_price = 0.0
        self._peak_equity = 0.0
        self._circuit_breaker_tripped = False
        self._kronos_signal_count = 0
        self._bars_elapsed = 0
        self._prev_fast_above_slow = None

    # -- Signal handling ---------------------------------------------------------

    def _handle_kronos_signal(self, signal: KronosSignal) -> None:
        """Process a Kronos signal: filter, check risk, then trade."""
        # Circuit breaker: skip new entries if max drawdown exceeded
        if self._circuit_breaker_tripped:
            self.log.debug("Circuit breaker active — no new entries")
            return

        # Don't enter if already in a position in the same direction
        instrument_id = self.config.instrument_id
        if not self.portfolio.is_flat(instrument_id):
            return

        # Confidence filter
        if signal.confidence < self.config.min_confidence:
            self.log.debug(
                f"Signal rejected: confidence {signal.confidence:.3f} < "
                f"threshold {self.config.min_confidence}"
            )
            return

        # Return magnitude filter
        if abs(signal.predicted_return_pct) < self.config.min_predicted_return_pct:
            self.log.debug(
                f"Signal rejected: |return| {abs(signal.predicted_return_pct):.4f} < "
                f"threshold {self.config.min_predicted_return_pct}"
            )
            return

        # EMA trend filter: only trade with the trend
        if self._ema_fast.initialized and self._ema_slow.initialized:
            fast_above_slow = self._ema_fast.value > self._ema_slow.value
            if signal.is_bullish() and not fast_above_slow:
                self.log.debug("Bullish signal rejected: price below slow EMA (downtrend)")
                return
            if signal.is_bearish() and fast_above_slow:
                self.log.debug("Bearish signal rejected: price above slow EMA (uptrend)")
                return

        # Enter position
        if signal.is_bullish():
            self._enter(OrderSide.BUY)
        elif signal.is_bearish():
            self._enter(OrderSide.SELL)

    # -- EMA fallback crossover --------------------------------------------------

    def _ema_crossover_fallback(self) -> None:
        """Simple fast/slow EMA crossover when no Kronos signals arrive.

        Only fires on actual crossover events to prevent re-entry after exits.
        """
        if not self._ema_fast.initialized or not self._ema_slow.initialized:
            return

        fast_above_slow = self._ema_fast.value > self._ema_slow.value

        if self._prev_fast_above_slow is None:
            self._prev_fast_above_slow = fast_above_slow
            return

        if fast_above_slow and not self._prev_fast_above_slow:
            if self.portfolio.is_flat(self.config.instrument_id):
                self.log.info("Fallback EMA crossover: BUY")
                self._enter(OrderSide.BUY)
        elif not fast_above_slow and self._prev_fast_above_slow:
            if self.portfolio.is_flat(self.config.instrument_id):
                self.log.info("Fallback EMA crossover: SELL")
                self._enter(OrderSide.SELL)

        self._prev_fast_above_slow = fast_above_slow

    # -- Risk management ---------------------------------------------------------

    def _update_drawdown_tracker(self) -> None:
        """Update peak equity and trip circuit breaker if drawdown exceeded."""
        account = self.portfolio.account(self.config.instrument_id.venue)
        if account is None:
            return

        try:
            current_equity = float(account.balance_total().as_double())
        except Exception:
            return

        if current_equity > self._peak_equity:
            self._peak_equity = current_equity

        if self._peak_equity > 0:
            drawdown = (self._peak_equity - current_equity) / self._peak_equity
            if drawdown >= self.config.max_drawdown_pct and not self._circuit_breaker_tripped:
                self._circuit_breaker_tripped = True
                self.log.warning(
                    f"Max drawdown circuit breaker tripped: "
                    f"{drawdown:.1%} >= {self.config.max_drawdown_pct:.1%}. "
                    f"No new entries until position is flat."
                )

        # Reset circuit breaker when flat (drawdown recovered or position closed)
        if self._circuit_breaker_tripped and self.portfolio.is_flat(self.config.instrument_id):
            self._circuit_breaker_tripped = False
            self.log.info("Circuit breaker reset — position flat.")

    def _check_exits(self) -> None:
        """Check stop-loss and take-profit on every bar."""
        if self._entry_price is None:
            return

        instrument_id = self.config.instrument_id
        if self.portfolio.is_flat(instrument_id):
            self._entry_price = None
            return

        is_long = self.portfolio.is_net_long(instrument_id)
        pnl_pct = (
            (self._current_price - self._entry_price) / self._entry_price
            if is_long
            else (self._entry_price - self._current_price) / self._entry_price
        )

        if pnl_pct <= -self.config.stop_loss_pct:
            side = "long" if is_long else "short"
            self.log.info(f"Stop loss triggered ({side}): {pnl_pct:.3%}")
            self.close_all_positions(instrument_id)
            self._entry_price = None
        elif pnl_pct >= self.config.take_profit_pct:
            side = "long" if is_long else "short"
            self.log.info(f"Take profit triggered ({side}): {pnl_pct:.3%}")
            self.close_all_positions(instrument_id)
            self._entry_price = None

    # -- Order management --------------------------------------------------------

    def _enter(self, side: OrderSide) -> None:
        """Submit a market order and record the entry price."""
        if self.instrument is None or self._current_price <= 0:
            return

        order = self.order_factory.market(
            instrument_id=self.config.instrument_id,
            order_side=side,
            quantity=self.instrument.make_qty(self.config.trade_size),
            time_in_force=TimeInForce.GTC,
        )
        self.submit_order(order)
        self._entry_price = self._current_price
        self.log.info(
            f"Entered {side.name} @ {self._current_price:.6f} "
            f"qty={self.config.trade_size}"
        )
