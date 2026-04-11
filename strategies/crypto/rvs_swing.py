"""RVS (Relative Volume Sentiment) Swing Trading Strategy.

Sentiment-driven swing trader for BTC/USDT targeting $500 capital:
- RVS signal: volume >2sigma + polarity >0.6 + engagement >1.5x baseline
- Reddit primary (1-14 day window), Twitter confirmation only
- TimesFM P50 forecast confirmation
- Whale filter: reject if top-10 wallet concentration increased >2% in 24h
- Trailing stop-loss: 3% initial, tightens to 1.5% after 2% profit
- Risk checklist: cash check, position limit, 2:1 R:R minimum
"""

from decimal import Decimal

from nautilus_trader.config import StrategyConfig
from nautilus_trader.indicators import ExponentialMovingAverage
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.trading.strategy import Strategy

from strategies.crypto.risk_guard import RiskGuard
from strategies.crypto.rvs_data import RVSSignal


class RVSSwingConfig(StrategyConfig, frozen=True):
    """Configuration for RVS Swing trading strategy."""

    instrument_id: InstrumentId
    bar_type: BarType
    trade_size: Decimal

    # RVS anomaly thresholds
    volume_zscore_threshold: float = 2.0
    polarity_threshold: float = 0.6
    engagement_ratio_threshold: float = 1.5

    # Whale filter
    whale_concentration_max_change: float = 2.0  # % increase in 24h

    # Trailing stop-loss
    initial_stop_loss_pct: float = 0.03  # 3% from entry
    tight_stop_loss_pct: float = 0.015  # 1.5% after profit threshold
    profit_threshold_to_tighten: float = 0.02  # 2% profit triggers tightening

    # Risk checklist
    min_reward_risk_ratio: float = 2.0  # minimum 2:1 R:R
    max_position_pct: float = 0.15  # max 15% of equity per position

    # TimesFM forecast
    forecast_threshold_pct: float = 0.01  # P50 must exceed current price by 1%

    # EMA trend filter
    ema_period: int = 200


class RVSSwingStrategy(RiskGuard, Strategy):
    """RVS-driven swing trading strategy for crypto.

    Processes RVSSignal events and enters positions when:
    1. RVS anomaly detected (volume, polarity, engagement)
    2. Whale filter passes (<2% concentration increase)
    3. TimesFM forecast confirms direction
    4. Risk checklist passes (cash, position limit, R:R)

    Uses a trailing stop-loss that starts at 3% and tightens to 1.5%
    after the position reaches 2% profit.
    """

    def __init__(self, config: RVSSwingConfig) -> None:
        super().__init__(config)
        self.instrument: Instrument | None = None
        self.ema = ExponentialMovingAverage(config.ema_period)

        # Position tracking
        self._entry_price: float | None = None
        self._highest_since_entry: float | None = None
        self._profit_threshold_reached: bool = False
        self._current_price: float = 0.0

    # -- Lifecycle ---------------------------------------------------------------

    def on_start(self) -> None:
        self.instrument = self.cache.instrument(self.config.instrument_id)
        if self.instrument is None:
            self.log.error(f"Could not find instrument for {self.config.instrument_id}")
            self.stop()
            return

        self.register_indicator_for_bars(self.config.bar_type, self.ema)
        self.subscribe_bars(self.config.bar_type)

    def on_bar(self, bar: Bar) -> None:
        self._current_price = float(bar.close)

        if not self.indicators_initialized():
            self.log.info(
                f"Warming up indicators [{self.cache.bar_count(self.config.bar_type)}]",
            )
            return

        self._check_trailing_stop()

    def on_data(self, data) -> None:
        """Handle incoming RVSSignal data."""
        if not isinstance(data, RVSSignal):
            return
        if self._current_price <= 0 or self.instrument is None:
            return
        if not self.portfolio.is_flat(self.config.instrument_id):
            return
        if self.evaluate_signal(data):
            self._enter_long(self._current_price)

    def on_stop(self) -> None:
        self.cancel_all_orders(self.config.instrument_id)
        self.close_all_positions(self.config.instrument_id)
        self.unsubscribe_bars(self.config.bar_type)

    def on_reset(self) -> None:
        self.ema.reset()
        self._reset_position_state()
        self._current_price = 0.0

    # -- Signal evaluation -------------------------------------------------------

    def evaluate_signal(self, signal: RVSSignal) -> bool:
        """Run full signal evaluation: anomaly + whale + forecast checks.

        Returns True only if ALL checks pass.
        """
        return (
            self.is_rvs_anomaly(signal)
            and self.passes_whale_filter(signal)
            and self.passes_forecast_check(signal)
        )

    def is_rvs_anomaly(self, signal: RVSSignal) -> bool:
        """Check if signal meets RVS anomaly thresholds.

        Conditions (all must hold):
        - volume_zscore >= threshold (default 2.0)
        - polarity > threshold (default 0.6, strictly greater)
        - engagement_ratio > threshold (default 1.5, strictly greater)
        """
        return (
            signal.volume_zscore >= self.config.volume_zscore_threshold
            and signal.polarity > self.config.polarity_threshold
            and signal.engagement_ratio > self.config.engagement_ratio_threshold
        )

    def passes_whale_filter(self, signal: RVSSignal) -> bool:
        """Reject if top-10 wallet concentration increased >= 2% in 24h."""
        return signal.whale_concentration_change < self.config.whale_concentration_max_change

    def passes_forecast_check(self, signal: RVSSignal) -> bool:
        """Check TimesFM P50 forecast exceeds threshold."""
        return signal.forecast_delta_pct >= self.config.forecast_threshold_pct

    # -- Risk checklist ----------------------------------------------------------

    def check_cash_available(
        self,
        available_cash: Decimal,
        order_value: Decimal,
    ) -> bool:
        """Check that available cash covers the order value."""
        return available_cash >= order_value and available_cash > 0

    def check_position_limit(
        self,
        order_value: Decimal,
        total_equity: Decimal,
    ) -> bool:
        """Check that order doesn't exceed max_position_pct of total equity."""
        if total_equity <= 0:
            return False
        return float(order_value / total_equity) <= self.config.max_position_pct

    def check_reward_risk(self, target_pct: float, stop_pct: float) -> bool:
        """Check that reward:risk ratio meets minimum (default 2:1).

        Returns False if stop_pct is zero (avoids division by zero).
        """
        if stop_pct <= 0:
            return False
        return target_pct / stop_pct >= self.config.min_reward_risk_ratio

    # -- Trailing stop-loss ------------------------------------------------------

    def compute_trailing_stop(self, current_price: float) -> float:
        """Compute the current trailing stop-loss price.

        - Initial: 3% below entry price
        - After 2% profit: tightens to 1.5% below highest price since entry
        """
        if self._entry_price is None:
            return 0.0

        if self._profit_threshold_reached:
            highest = self._highest_since_entry or current_price
            return highest * (1 - self.config.tight_stop_loss_pct)
        return self._entry_price * (1 - self.config.initial_stop_loss_pct)

    def is_profit_above_threshold(self, current_price: float) -> bool:
        """Check if unrealized profit has reached the tightening threshold."""
        if self._entry_price is None or self._entry_price <= 0:
            return False
        pnl_pct = (current_price - self._entry_price) / self._entry_price
        return pnl_pct >= self.config.profit_threshold_to_tighten

    def _reset_position_state(self) -> None:
        """Clear all position-tracking state."""
        self._entry_price = None
        self._highest_since_entry = None
        self._profit_threshold_reached = False

    def _check_trailing_stop(self) -> None:
        """Check and execute trailing stop logic on each bar."""
        if self._entry_price is None:
            return

        if self.portfolio.is_flat(self.config.instrument_id):
            self._reset_position_state()
            return

        # Update highest price
        if self._highest_since_entry is None or self._current_price > self._highest_since_entry:
            self._highest_since_entry = self._current_price

        # Check if profit threshold reached
        if not self._profit_threshold_reached and self.is_profit_above_threshold(
            self._current_price
        ):
            self._profit_threshold_reached = True
            self.log.info(
                f"Profit threshold reached, tightening stop to "
                f"{self.config.tight_stop_loss_pct * 100:.1f}%"
            )

        # Compute and check stop
        stop_price = self.compute_trailing_stop(self._current_price)
        if self._current_price <= stop_price:
            self.log.info(
                f"Trailing stop triggered at {self._current_price:.2f} "
                f"(stop={stop_price:.2f})"
            )
            self.close_all_positions(self.config.instrument_id)
            self._reset_position_state()

    # -- Order management --------------------------------------------------------

    def _enter_long(self, price: float) -> None:
        """Submit a market buy order and initialize trailing stop state."""
        if self.instrument is None:
            return

        order = self.order_factory.market(
            instrument_id=self.config.instrument_id,
            order_side=OrderSide.BUY,
            quantity=self.instrument.make_qty(self.config.trade_size),
            time_in_force=TimeInForce.GTC,
        )
        self.submit_order(order)
        self._entry_price = price
        self._highest_since_entry = price
        self._profit_threshold_reached = False
