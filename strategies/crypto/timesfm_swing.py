"""TimesFM-based swing trading strategy for crypto.

Uses Google's TimesFM foundation model for price prediction,
combined with a 200 EMA trend filter. Falls back to EMA-only
mode if TimesFM is not installed.

Design note (from adversarial debate):
- Best used as a SIGNAL OVERLAY, not standalone strategy
- Run on CPU only (GPU costs are 12-48% of $500 annually)
- Confidence thresholds filter bad signals (28% of ML models
  drop below coin-flip after regime changes)
- Ensemble with EMA empirically outperforms either alone
  (Sebastiao & Godinho 2021: up to 9.62% annualized)
"""

from decimal import Decimal

import numpy as np
from nautilus_trader.config import PositiveInt, StrategyConfig
from nautilus_trader.indicators import ExponentialMovingAverage
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.trading.strategy import Strategy

try:
    import timesfm

    TIMESFM_AVAILABLE = True
except ImportError:
    TIMESFM_AVAILABLE = False


class TimesFMSwingConfig(StrategyConfig, frozen=True):
    """Configuration for TimesFM swing trading strategy."""

    instrument_id: InstrumentId
    bar_type: BarType
    trade_size: Decimal
    lookback_bars: PositiveInt = 512
    forecast_horizon: PositiveInt = 24
    forecast_interval_bars: PositiveInt = 4
    confidence_threshold: float = 0.6
    ema_period: PositiveInt = 200
    fallback_fast_ema_period: PositiveInt = 50
    stop_loss_pct: float = 0.02
    take_profit_pct: float = 0.04


class TimesFMSwingStrategy(Strategy):
    """Swing trading strategy using TimesFM price forecasting with EMA trend filter.

    When TimesFM is available, generates signals from model forecasts filtered
    by the 200 EMA trend direction. When TimesFM is unavailable, degrades
    gracefully to a simple fast/slow EMA crossover.
    """

    def __init__(self, config: TimesFMSwingConfig) -> None:
        super().__init__(config)
        self.instrument: Instrument | None = None

        # Indicators
        self.ema = ExponentialMovingAverage(config.ema_period)
        self.fast_ema = ExponentialMovingAverage(config.fallback_fast_ema_period)

        # Price buffer for TimesFM context window
        self._price_buffer: list[float] = []
        self._bars_since_forecast: int = 0
        self._model = None
        self._model_available: bool = False

        # Position tracking
        self._entry_price: float | None = None
        # EMA crossover state (True = fast above slow on previous bar)
        self._prev_fast_above_slow: bool | None = None

    # -- Lifecycle ---------------------------------------------------------------

    def on_start(self) -> None:
        self.instrument = self.cache.instrument(self.config.instrument_id)
        if self.instrument is None:
            self.log.error(f"Could not find instrument for {self.config.instrument_id}")
            self.stop()
            return

        self.register_indicator_for_bars(self.config.bar_type, self.ema)
        self.register_indicator_for_bars(self.config.bar_type, self.fast_ema)
        self.subscribe_bars(self.config.bar_type)

        self._load_model()

    def on_bar(self, bar: Bar) -> None:
        # Collect close prices
        close = float(bar.close)
        self._price_buffer.append(close)
        if len(self._price_buffer) > self.config.lookback_bars:
            self._price_buffer = self._price_buffer[-self.config.lookback_bars :]

        if not self.indicators_initialized():
            self.log.info(
                f"Warming up indicators [{self.cache.bar_count(self.config.bar_type)}]",
            )
            return

        # Check exits first
        self._check_exits(close)

        # Forecast logic
        self._bars_since_forecast += 1
        if self._model_available and len(self._price_buffer) >= self.config.lookback_bars:
            if self._bars_since_forecast >= self.config.forecast_interval_bars:
                self._run_forecast(close)
        elif not self._model_available:
            # Fallback: EMA crossover
            self._ema_fallback_signal()

    def on_stop(self) -> None:
        self.cancel_all_orders(self.config.instrument_id)
        self.close_all_positions(self.config.instrument_id)
        self.unsubscribe_bars(self.config.bar_type)

    def on_reset(self) -> None:
        self.ema.reset()
        self.fast_ema.reset()
        self._price_buffer.clear()
        self._bars_since_forecast = 0
        self._entry_price = None
        self._prev_fast_above_slow = None

    # -- Model loading -----------------------------------------------------------

    def _load_model(self) -> None:
        """Lazy-load TimesFM model. Falls back to EMA-only on failure."""
        if not TIMESFM_AVAILABLE:
            self.log.warning(
                "TimesFM not installed -- falling back to EMA crossover mode. "
                "Install with: pip install timesfm",
            )
            return

        try:
            self._model = timesfm.TimesFm(
                hparams=timesfm.TimesFmHparams(
                    per_core_batch_size=1,
                    horizon_len=self.config.forecast_horizon,
                    context_len=self.config.lookback_bars,
                    num_layers=50,
                    model_dims=1280,
                    use_positional_embedding=False,
                    backend="cpu",
                ),
                checkpoint=timesfm.TimesFmCheckpoint(
                    version="torch",
                    huggingface_repo_id="google/timesfm-2.0-500m-pytorch",
                ),
            )
            self._model_available = True
            self.log.info("TimesFM model loaded successfully")
        except Exception as e:
            self.log.warning(f"Failed to load TimesFM model: {e} -- falling back to EMA mode")
            self._model = None
            self._model_available = False

    # -- Forecasting -------------------------------------------------------------

    def _run_forecast(self, current_price: float) -> None:
        """Run TimesFM forecast and generate a trading signal."""
        self._bars_since_forecast = 0

        prices = np.array(self._price_buffer[-self.config.lookback_bars :])
        try:
            forecasts, quantiles = self._model.forecast(
                [prices],
                freq=[0],
            )
        except Exception as e:
            self.log.warning(f"TimesFM forecast failed: {e}")
            return

        # forecasts shape: (1, horizon_len)
        predicted_prices = forecasts[0]
        avg_forecast = float(np.mean(predicted_prices))
        predicted_change_pct = (avg_forecast - current_price) / current_price

        # Confidence from quantile spread (narrower = more confident)
        confidence = self._compute_confidence(quantiles, current_price)

        self._generate_signal(current_price, predicted_change_pct, confidence)

    def _compute_confidence(self, quantiles: np.ndarray, current_price: float) -> float:
        """Derive confidence score from forecast quantile spread.

        Narrower quantile bands relative to price = higher confidence.
        Returns a value in [0, 1].
        """
        if quantiles is None or quantiles.size == 0:
            return 0.5

        try:
            # quantiles shape varies by TimesFM version; handle gracefully
            q = np.array(quantiles)
            if q.ndim >= 2:
                spread = float(np.mean(np.max(q, axis=-1) - np.min(q, axis=-1)))
            else:
                spread = float(np.max(q) - np.min(q))

            relative_spread = spread / current_price if current_price > 0 else 1.0
            # Map spread to confidence: small spread -> high confidence
            confidence = max(0.0, min(1.0, 1.0 - relative_spread * 10))
        except Exception:
            confidence = 0.5

        return confidence

    def _generate_signal(
        self,
        current_price: float,
        predicted_change_pct: float,
        confidence: float,
    ) -> None:
        """Generate BUY/SELL signal from forecast + EMA trend filter."""
        if confidence <= self.config.confidence_threshold:
            return

        ema_value = self.ema.value

        if predicted_change_pct > self.config.stop_loss_pct and current_price > ema_value:
            self._enter_or_flip(OrderSide.BUY, current_price)
        elif predicted_change_pct < -self.config.stop_loss_pct and current_price < ema_value:
            self._enter_or_flip(OrderSide.SELL, current_price)

    # -- Fallback EMA crossover --------------------------------------------------

    def _ema_fallback_signal(self) -> None:
        """Simple fast/slow EMA crossover when TimesFM is unavailable.

        Only fires on actual crossover events (not while EMAs remain crossed)
        to prevent immediate re-entry after stop-loss exits.
        """
        fast_above_slow = self.fast_ema.value >= self.ema.value

        if self._prev_fast_above_slow is None:
            # First bar after warmup — seed state, don't signal
            self._prev_fast_above_slow = fast_above_slow
            return

        # Only signal on crossover transitions
        if fast_above_slow and not self._prev_fast_above_slow:
            self._enter_or_flip(OrderSide.BUY, self.fast_ema.value)
        elif not fast_above_slow and self._prev_fast_above_slow:
            self._enter_or_flip(OrderSide.SELL, self.fast_ema.value)

        self._prev_fast_above_slow = fast_above_slow

    def _enter_or_flip(self, side: OrderSide, price: float) -> None:
        """Enter a position or flip from the opposite side.

        NOTE: In live trading, close_all_positions() may not fill before the
        new entry, risking double position size. For live use, implement
        fill-based state machine via on_event(OrderFilled).
        """
        instrument_id = self.config.instrument_id
        is_flat = self.portfolio.is_flat(instrument_id)
        is_opposite = (
            self.portfolio.is_net_short(instrument_id)
            if side == OrderSide.BUY
            else self.portfolio.is_net_long(instrument_id)
        )

        if is_opposite:
            self.close_all_positions(instrument_id)
        if is_flat or is_opposite:
            self._enter(side, price)

    # -- Order management --------------------------------------------------------

    def _enter(self, side: OrderSide, price: float) -> None:
        """Submit a market order and record entry price."""
        order = self.order_factory.market(
            instrument_id=self.config.instrument_id,
            order_side=side,
            quantity=self.instrument.make_qty(self.config.trade_size),
            time_in_force=TimeInForce.GTC,
        )
        self.submit_order(order)
        self._entry_price = price

    def _check_exits(self, current_price: float) -> None:
        """Check stop-loss and take-profit against entry price."""
        if self._entry_price is None:
            return

        instrument_id = self.config.instrument_id

        if self.portfolio.is_flat(instrument_id):
            self._entry_price = None
            return

        is_long = self.portfolio.is_net_long(instrument_id)
        direction = "long" if is_long else "short"
        pnl_pct = (
            (current_price - self._entry_price) / self._entry_price
            if is_long
            else (self._entry_price - current_price) / self._entry_price
        )

        if pnl_pct <= -self.config.stop_loss_pct:
            self.log.info(f"Stop loss triggered ({direction}): {pnl_pct:.4f}")
            self.close_all_positions(instrument_id)
            self._entry_price = None
        elif pnl_pct >= self.config.take_profit_pct:
            self.log.info(f"Take profit triggered ({direction}): {pnl_pct:.4f}")
            self.close_all_positions(instrument_id)
            self._entry_price = None
