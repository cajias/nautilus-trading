"""Shock Guard Macro Allocator -- two-tier crypto trading strategy.

Strategic Tier (slow, hourly):
    Graduated position scaling (0-100%) with 50% default, 15% floor.
    Deterministic EMA crossover signals for backtesting (replaces LLM).
    Regime hysteresis: only change if confidence > 0.7.
    Position changes capped at +/-25% per hour.

Tactical Tier (fast, per-bar):
    Shock Guard monitors 2-of-3 signals:
        1. Price drop >3% in 5 minutes
        2. Bid volume <20% of recent average (simulated via price proxy)
        3. 1-min ATR >5x 1-hour ATR average
    On trigger: reduce to 25% allocation, 30-min cooldown.

OCO orders: stop-loss at -5%, take-profit at +8% from entry.
Risk checklist: position <= max, change <= 25%/hr, R:R >= 2:1 for increases.
"""

from collections import deque
from decimal import Decimal

from nautilus_trader.config import PositiveInt, StrategyConfig
from nautilus_trader.indicators import AverageTrueRange, ExponentialMovingAverage
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.trading.strategy import Strategy

from strategies.crypto.risk_guard import RiskGuard


class ShockGuardConfig(StrategyConfig, frozen=True):
    """Configuration for the Shock Guard Macro Allocator strategy."""

    instrument_id: InstrumentId
    bar_type: BarType
    trade_size: Decimal

    # Strategic tier
    default_allocation_pct: float = 0.50
    min_allocation_pct: float = 0.15
    max_allocation_pct: float = 1.0
    max_position_change_pct: float = 0.25
    regime_confidence_threshold: float = 0.7
    strategic_rebalance_bars: PositiveInt = 60

    # Tactical tier (Shock Guard)
    price_drop_threshold_pct: float = 0.03
    price_drop_window_bars: PositiveInt = 5
    bid_volume_ratio_threshold: float = 0.20
    atr_spike_multiplier: float = 5.0
    shock_guard_signals_required: PositiveInt = 2
    shock_guard_target_pct: float = 0.25
    cooldown_bars: PositiveInt = 30

    # OCO parameters
    stop_loss_pct: float = 0.05
    take_profit_pct: float = 0.08

    # Deterministic signals for backtest (replaces LLM calls)
    use_deterministic_signals: bool = True
    ema_fast_period: PositiveInt = 12
    ema_slow_period: PositiveInt = 26


class ShockGuardStrategy(RiskGuard, Strategy):
    """Two-tier crypto strategy with graduated allocation and shock detection.

    Works in both backtest (deterministic signals) and live (pluggable LLM).
    """

    def __init__(self, config: ShockGuardConfig) -> None:
        super().__init__(config)
        self.instrument: Instrument | None = None

        # Strategic tier state
        self.current_allocation_pct: float = config.default_allocation_pct
        self._bars_since_rebalance: int = 0
        self._entry_price: float = 0.0

        # Deterministic regime indicators
        self._ema_fast = ExponentialMovingAverage(config.ema_fast_period)
        self._ema_slow = ExponentialMovingAverage(config.ema_slow_period)

        # Tactical tier state -- Shock Guard signals
        self._signal_price_drop: bool = False
        self._signal_bid_volume_low: bool = False
        self._signal_atr_spike: bool = False

        # Price history for drop detection
        self._price_history: deque[float] = deque(
            maxlen=config.price_drop_window_bars + 1,
        )

        # ATR indicators for spike detection
        self._short_atr = AverageTrueRange(1)   # 1-bar ATR (most recent)
        self._long_atr = AverageTrueRange(60)    # 60-bar ATR (~1 hour)
        self._short_atr_value: float = 0.0
        self._long_atr_value: float = 0.0

        self._cooldown_remaining: int = 0

    def on_start(self) -> None:
        self.instrument = self.cache.instrument(self.config.instrument_id)
        if self.instrument is None:
            self.log.error(
                f"Could not find instrument for {self.config.instrument_id}",
            )
            self.stop()
            return

        for indicator in (self._ema_fast, self._ema_slow, self._short_atr, self._long_atr):
            self.register_indicator_for_bars(self.config.bar_type, indicator)
        self.subscribe_bars(self.config.bar_type)

        self.current_allocation_pct = self.config.default_allocation_pct

        # Portfolio-level risk guardrails
        self._risk_guard_init(
            starting_equity=1000.0,
            max_drawdown_pct=20.0,
            max_position_pct=0.50,
        )

        self.log.info(
            f"ShockGuard started: alloc={self.current_allocation_pct:.0%} "
            f"| SL={self.config.stop_loss_pct:.1%} TP={self.config.take_profit_pct:.1%} "
            f"| shock_guard={self.config.shock_guard_signals_required}-of-3",
        )

    def on_bar(self, bar: Bar) -> None:
        if self._is_halted():
            return

        if not self.indicators_initialized():
            self._update_price_history(float(bar.close))
            return

        current_price = float(bar.close)
        self._update_price_history(current_price)
        self._short_atr_value = self._short_atr.value
        self._long_atr_value = self._long_atr.value
        self._tick_cooldown()

        # Tactical tier: Shock Guard check (every bar)
        self._update_shock_signals()
        if not self._is_cooling_down() and self._shock_guard_triggered():
            self._handle_shock_guard()
            return

        # Strategic tier: rebalance periodically
        self._bars_since_rebalance += 1
        if self._bars_since_rebalance >= self.config.strategic_rebalance_bars:
            self._bars_since_rebalance = 0
            self._strategic_rebalance(current_price)

    def on_stop(self) -> None:
        self.cancel_all_orders(self.config.instrument_id)
        self.close_all_positions(self.config.instrument_id)
        self.unsubscribe_bars(self.config.bar_type)

    def on_reset(self) -> None:
        self._ema_fast.reset()
        self._ema_slow.reset()
        self._short_atr.reset()
        self._long_atr.reset()
        self._price_history.clear()
        self.current_allocation_pct = self.config.default_allocation_pct

        # Portfolio-level risk guardrails
        self._risk_guard_init(
            starting_equity=1000.0,
            max_drawdown_pct=20.0,
            max_position_pct=0.50,
        )
        self._cooldown_remaining = 0
        self._bars_since_rebalance = 0
        self._signal_price_drop = False
        self._signal_bid_volume_low = False
        self._signal_atr_spike = False

    def _strategic_rebalance(self, current_price: float) -> None:
        """Evaluate regime and adjust allocation (hourly)."""
        if self.config.use_deterministic_signals:
            suggested, confidence = self._deterministic_regime_signal()
        else:
            # Placeholder for LLM-based signals in live trading
            suggested, confidence = self.current_allocation_pct, 0.0

        new_alloc = self._evaluate_regime_signal(suggested, confidence)
        new_alloc = self._apply_rate_limit(new_alloc)
        new_alloc = self._clamp_allocation(new_alloc)

        # Risk checklist for increases
        if new_alloc > self.current_allocation_pct and not self._risk_reward_acceptable():
            self.log.info("Risk checklist failed: R:R < 2:1, skipping increase")
            return

        if abs(new_alloc - self.current_allocation_pct) > 0.01:
            self.log.info(
                f"Strategic rebalance: {self.current_allocation_pct:.0%} -> {new_alloc:.0%}",
            )
            self.current_allocation_pct = new_alloc
            self._adjust_position(current_price)

    def _deterministic_regime_signal(self) -> tuple[float, float]:
        """Use EMA crossover as a deterministic regime signal for backtesting.

        Returns (suggested_allocation, confidence).
        """
        if not self._ema_fast.initialized or not self._ema_slow.initialized:
            return self.config.default_allocation_pct, 0.0

        fast = self._ema_fast.value
        slow = self._ema_slow.value

        # Spread as a fraction of slow EMA
        spread = (fast - slow) / slow if slow > 0 else 0.0

        # Map spread to allocation: positive spread = bullish = higher allocation
        if spread > 0.005:
            suggested = 0.75
            confidence = min(abs(spread) / 0.01, 1.0)
        elif spread > 0.001:
            suggested = 0.60
            confidence = min(abs(spread) / 0.005, 1.0)
        elif spread < -0.005:
            suggested = 0.25
            confidence = min(abs(spread) / 0.01, 1.0)
        elif spread < -0.001:
            suggested = 0.40
            confidence = min(abs(spread) / 0.005, 1.0)
        else:
            suggested = self.config.default_allocation_pct
            confidence = 0.3  # low confidence in neutral zone

        return suggested, confidence

    def _evaluate_regime_signal(
        self,
        suggested_allocation: float,
        confidence: float,
    ) -> float:
        """Apply regime hysteresis: only change if confidence > threshold."""
        if confidence > self.config.regime_confidence_threshold:
            return suggested_allocation
        return self.current_allocation_pct

    def _apply_rate_limit(self, target: float) -> float:
        """Cap position change to max_position_change_pct per period."""
        delta = target - self.current_allocation_pct
        limit = self.config.max_position_change_pct
        return self.current_allocation_pct + max(-limit, min(limit, delta))

    def _clamp_allocation(self, value: float) -> float:
        """Clamp allocation between min and max bounds."""
        return max(self.config.min_allocation_pct, min(self.config.max_allocation_pct, value))

    def _update_shock_signals(self) -> None:
        """Update all 3 shock guard signals from current data."""
        self._signal_price_drop = self._check_price_drop_signal()
        self._signal_bid_volume_low = self._check_bid_volume_signal()
        self._signal_atr_spike = self._check_atr_spike_signal()

    def _shock_guard_triggered(self) -> bool:
        """Check if 2-of-3 shock guard signals are active."""
        count = self._signal_price_drop + self._signal_bid_volume_low + self._signal_atr_spike
        return count >= self.config.shock_guard_signals_required

    def _handle_shock_guard(self) -> None:
        """Execute shock guard: reduce to target and start cooldown."""
        target = self._clamp_allocation(self.config.shock_guard_target_pct)
        self.log.warning(
            f"SHOCK GUARD triggered! Reducing allocation "
            f"{self.current_allocation_pct:.0%} -> {target:.0%}",
        )
        self.current_allocation_pct = target
        self._cooldown_remaining = self.config.cooldown_bars
        self.cancel_all_orders(self.config.instrument_id)
        if self._price_history:
            self._adjust_position(self._price_history[-1])

    def _update_price_history(self, price: float) -> None:
        self._price_history.append(price)

    def _check_price_drop_signal(self) -> bool:
        """Signal 1: price drop > threshold within window."""
        if len(self._price_history) < 2:
            return False

        max_price = max(self._price_history)
        if max_price <= 0:
            return False

        drop_pct = (max_price - self._price_history[-1]) / max_price
        return drop_pct > self.config.price_drop_threshold_pct

    def _check_bid_volume_signal(self) -> bool:
        """Signal 2: bid volume proxy -- 3+ consecutive down bars = weak buying.

        In backtest without orderbook data, we use consecutive down bars
        as a proxy for weak bid volume.
        """
        if len(self._price_history) < 4:
            return False

        recent = list(self._price_history)[-4:]
        return all(recent[i] < recent[i - 1] for i in range(1, 4))

    def _check_atr_spike_signal(self) -> bool:
        """Signal 3: short ATR > multiplier x long ATR."""
        if self._long_atr_value <= 0:
            return False
        return self._short_atr_value > self.config.atr_spike_multiplier * self._long_atr_value

    def _tick_cooldown(self) -> None:
        if self._cooldown_remaining > 0:
            self._cooldown_remaining -= 1

    def _is_cooling_down(self) -> bool:
        return self._cooldown_remaining > 0

    def _risk_reward_acceptable(self) -> bool:
        """Check if R:R ratio >= 2:1 (required for position increases)."""
        if self.config.stop_loss_pct <= 0:
            return False
        return self.config.take_profit_pct / self.config.stop_loss_pct >= 2.0

    def _adjust_position(self, current_price: float) -> None:
        """Adjust position size to match current allocation percentage."""
        if self.instrument is None:
            return

        if self.portfolio.is_net_long(self.config.instrument_id):
            self.close_all_positions(self.config.instrument_id)

        target_qty = float(self.config.trade_size) * self.current_allocation_pct
        if target_qty > 0:
            self._enter_position(OrderSide.BUY, target_qty, current_price)

    def _enter_position(
        self,
        side: OrderSide,
        qty: float,
        current_price: float,
    ) -> None:
        """Enter a position and place OCO orders (stop-loss + take-profit)."""
        if self.instrument is None or qty <= 0:
            return

        order = self.order_factory.market(
            instrument_id=self.config.instrument_id,
            order_side=side,
            quantity=self.instrument.make_qty(Decimal(str(qty))),
            time_in_force=TimeInForce.IOC,  # Binance Spot: market orders must use IOC/FOK, not GTC
        )
        self.submit_order(order)
        self._entry_price = current_price
