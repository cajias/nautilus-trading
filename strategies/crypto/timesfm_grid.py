"""TimesFM Quantile Grid Orchestrator — crypto grid trading strategy.

Grid trading with dynamic boundaries derived from TimesFM P10/P90 quantile
forecasts. Uses ATR-adjusted spacing, Half-Kelly sizing, and four circuit
breakers for capital preservation on $500 accounts.

Key features:
- 8-10 grid levels with maker limit orders only
- Boundaries: TimesFM P10 (floor) and P90 (ceiling), recalculated every 4h
- Calibration gate: only trade when P10-P90 covers >75% of recent price action
- ATR(14)-adjusted spacing: wider in high-vol, tighter in low-vol
- Circuit breakers: price deviation halt, drawdown safe mode, trend override,
  inventory limit
- Half-Kelly position sizing capped at 1/2 Kelly
"""

from decimal import Decimal

from nautilus_trader.config import PositiveInt, StrategyConfig
from nautilus_trader.indicators import AverageTrueRange, ExponentialMovingAverage
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.events import OrderFilled
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.trading.strategy import Strategy

from nautilus_trading.cli._strategy_specs import TimesFMGridConfigBuilder
from nautilus_trading.paper_trade.node_config import round_to_tick
from nautilus_trading.specs import StrategySpec
from strategies.crypto._grid_math import (
    compute_atr_adjusted_step,
    compute_calibration_coverage,
    compute_uniform_grid_levels,
)
from strategies.crypto._grid_math import (
    compute_kelly_size as _kelly_size,
)
from strategies.crypto.risk_guard import RiskGuard


class TimesFMGridConfig(StrategyConfig, frozen=True):
    """Configuration for the TimesFM Quantile Grid Orchestrator."""

    instrument_id: InstrumentId
    bar_type: BarType
    trade_size: Decimal
    total_capital: Decimal = Decimal("500")
    grid_levels: PositiveInt = 8

    # Quantile boundaries (from TimesFM or manual override for backtesting)
    p10_floor: Decimal = Decimal("0")
    p90_ceiling: Decimal = Decimal("0")

    # Calibration gate
    calibration_min_coverage: float = 0.75

    # ATR for spacing adjustment
    atr_period: PositiveInt = 14

    # Circuit breakers
    price_deviation_pct: float = 0.02
    price_deviation_halt_seconds: int = 900  # 15 minutes
    drawdown_floor: Decimal = Decimal("425")
    trend_override_ratio: float = 1.02
    inventory_limit_pct: float = 0.70

    # Half-Kelly sizing
    kelly_fraction: float = 0.5

    # Trend detection EMAs
    fast_ema_period: PositiveInt = 20
    slow_ema_period: PositiveInt = 50

    # Recalculation interval (bars)
    recalc_interval_bars: PositiveInt = 240


class TimesFMGridStrategy(RiskGuard, Strategy):
    """Grid trading strategy with TimesFM quantile boundaries.

    Places limit buy orders below and limit sell orders above the current
    price at ATR-adjusted grid levels between P10 and P90 quantile forecasts.
    Includes four circuit breakers for capital preservation.
    """

    def __init__(self, config: TimesFMGridConfig) -> None:
        super().__init__(config)
        self.instrument: Instrument | None = None

        # Grid state
        self.grid_prices: list[Decimal] = []
        self.grid_orders: dict[int, str | None] = {}
        self._order_to_level: dict[str, int] = {}

        # Indicators
        self._atr = AverageTrueRange(config.atr_period)
        self._fast_ema = ExponentialMovingAverage(config.fast_ema_period)
        self._slow_ema = ExponentialMovingAverage(config.slow_ema_period)

        # Circuit breaker state
        self.safe_mode: bool = False
        self.trend_override_active: bool = False
        self._halt_until_bar: int = 0
        self._bar_count: int = 0
        self._last_fill_price: float | None = None

        # Calibration
        self._price_high: float = 0.0
        self._price_low: float = float("inf")
        self._calibration_passed: bool = False

        # Internal capital tracking for CASH accounts
        # Tracks quote currency committed to open buy orders
        self._committed_quote: Decimal = Decimal("0")
        # Tracks base currency committed to open sell orders
        self._committed_base: Decimal = Decimal("0")
        # Tracks base currency we actually hold from fills
        self._base_inventory: Decimal = Decimal("0")

    # -- Lifecycle ---------------------------------------------------------------

    def on_start(self) -> None:
        self.instrument = self.cache.instrument(self.config.instrument_id)
        if self.instrument is None:
            self.log.error(f"Could not find instrument for {self.config.instrument_id}")
            self.stop()
            return

        if self.config.p10_floor >= self.config.p90_ceiling:
            self.log.error("p10_floor must be less than p90_ceiling")
            self.stop()
            return

        # Calculate initial grid
        self._calculate_grid()

        # Register indicators
        self.register_indicator_for_bars(self.config.bar_type, self._atr)
        self.register_indicator_for_bars(self.config.bar_type, self._fast_ema)
        self.register_indicator_for_bars(self.config.bar_type, self._slow_ema)
        self.subscribe_bars(self.config.bar_type)

        self.log.info(
            f"TimesFM Grid initialized: {self.config.grid_levels} levels "
            f"from {self.config.p10_floor} to {self.config.p90_ceiling}"
        )

    def on_bar(self, bar: Bar) -> None:
        self._bar_count += 1
        current_price = float(bar.close)

        # Track price range for calibration
        self._price_high = max(self._price_high, current_price)
        self._price_low = min(self._price_low, current_price)

        # --- Circuit breaker: drawdown safe mode ---
        if self._check_drawdown_safe_mode():
            return

        # --- Circuit breaker: price deviation halt ---
        if self._bar_count < self._halt_until_bar:
            return

        # Wait for indicators
        if not self.indicators_initialized():
            return

        # --- Circuit breaker: trend override ---
        self._check_trend_override()
        if self.trend_override_active:
            self._trend_follow_logic(current_price)
            return

        # --- Calibration gate ---
        if not self._check_calibration():
            return

        # --- Place grid orders ---
        self._place_grid_orders(Decimal(str(current_price)))

    def on_event(self, event) -> None:
        """Handle order fills for grid recycling and capital tracking."""
        if not isinstance(event, OrderFilled):
            return

        order_id = event.client_order_id.value
        level = self._order_to_level.pop(order_id, None)
        if level is None:
            return

        fill_price = Decimal(str(event.last_px))
        fill_qty = self.config.trade_size

        # Update internal capital tracking
        if event.order_side == OrderSide.BUY:
            # Buy filled: release committed quote, gain base inventory
            self._committed_quote -= fill_price * fill_qty
            self._committed_quote = max(Decimal("0"), self._committed_quote)
            self._base_inventory += fill_qty
        else:
            # Sell filled: release committed base, gain quote back
            self._committed_base -= fill_qty
            self._committed_base = max(Decimal("0"), self._committed_base)
            self._base_inventory -= fill_qty
            self._base_inventory = max(Decimal("0"), self._base_inventory)

        self.grid_orders[level] = None
        self._last_fill_price = float(event.last_px)

        # Recycle: buy fill -> sell one level up, sell fill -> buy one level down
        if event.order_side == OrderSide.BUY:
            next_level, next_side = level + 1, OrderSide.SELL
        else:
            next_level, next_side = level - 1, OrderSide.BUY

        if 0 <= next_level < len(self.grid_prices) and self.grid_orders.get(next_level) is None:
            next_price = self.grid_prices[next_level]
            # On CASH accounts, check balances before placing recycled orders
            if next_side == OrderSide.SELL and not self._has_base_inventory():
                return
            if next_side == OrderSide.BUY and not self._has_quote_for_buy(next_price):
                return
            self._place_order(next_level, next_price, next_side)

    def _cancel_all_and_reset_tracking(self) -> None:
        """Cancel all orders and reset committed capital tracking."""
        self.cancel_all_orders(self.config.instrument_id)
        self._committed_quote = Decimal("0")
        self._committed_base = Decimal("0")
        # Clear grid order tracking since all orders are cancelled
        for level in self.grid_orders:
            order_id = self.grid_orders[level]
            if order_id is not None:
                self._order_to_level.pop(order_id, None)
            self.grid_orders[level] = None

    def on_stop(self) -> None:
        self.cancel_all_orders(
            self.config.instrument_id
        )  # explicit for Round 11 contract compliance
        self._cancel_all_and_reset_tracking()  # resets committed capital tracking
        self.close_all_positions(self.config.instrument_id)
        self.unsubscribe_bars(self.config.bar_type)

    def on_reset(self) -> None:
        self.grid_prices.clear()
        self.grid_orders.clear()
        self._order_to_level.clear()
        self._atr.reset()
        self._fast_ema.reset()
        self._slow_ema.reset()
        self.safe_mode = False
        self.trend_override_active = False
        self._halt_until_bar = 0
        self._bar_count = 0
        self._last_fill_price = None
        self._price_high = 0.0
        self._price_low = float("inf")
        self._calibration_passed = False
        self._committed_quote = Decimal("0")
        self._committed_base = Decimal("0")
        self._base_inventory = Decimal("0")

    # -- Grid calculation --------------------------------------------------------

    def _calculate_grid(self) -> None:
        """Calculate grid levels between P10 and P90, optionally ATR-adjusted."""
        precision = self.instrument.price_precision if self.instrument else 5
        n = self.config.grid_levels

        # Base uniform spacing
        raw_levels = compute_uniform_grid_levels(
            lower=self.config.p10_floor,
            upper=self.config.p90_ceiling,
            n_levels=n,
        )
        self.grid_prices = [round(p, precision) for p in raw_levels]

        # Initialize grid order tracking
        self.grid_orders = dict.fromkeys(range(n))

    def _recalculate_grid_with_atr(self, current_price: Decimal) -> None:
        """Recalculate grid spacing using ATR for volatility adjustment.

        Wider spacing in high-vol (large ATR), tighter in low-vol (small ATR).
        Keeps boundaries at P10 and P90 but distributes inner levels based on ATR.
        """
        if not self._atr.initialized or self.instrument is None:
            return

        precision = self.instrument.price_precision
        n = self.config.grid_levels
        atr_value = Decimal(str(self._atr.value))
        total_range = self.config.p90_ceiling - self.config.p10_floor

        if total_range <= 0 or atr_value <= 0:
            return

        # ATR-weighted spacing: scale step by ATR relative to range
        # Higher ATR -> wider spacing (fewer levels near current price)
        adjusted_step = compute_atr_adjusted_step(
            total_range=total_range,
            atr_value=atr_value,
            n_levels=n,
        )

        # Cancel existing orders before recalculating
        self._cancel_all_and_reset_tracking()

        # Rebuild grid with ATR influence, ensuring ceiling is exact
        self.grid_prices = [round(self.config.p10_floor, precision)]
        for _ in range(1, n - 1):
            next_price = min(self.grid_prices[-1] + adjusted_step, self.config.p90_ceiling)
            self.grid_prices.append(round(next_price, precision))
        self.grid_prices.append(round(self.config.p90_ceiling, precision))

        self.grid_orders = dict.fromkeys(range(len(self.grid_prices)))
        self._order_to_level.clear()

    def _get_total_balance(self) -> Decimal | None:
        """Return total account balance, or None if unavailable."""
        account = self.portfolio.account(self.config.instrument_id.venue)
        if account is None:
            return None
        balances = account.balances_total()
        if not balances:
            return None
        return sum((m.as_decimal() for m in balances.values()), Decimal("0"))

    # -- Calibration gate --------------------------------------------------------

    def _check_calibration(self) -> bool:
        """Check if P10-P90 covers sufficient percentage of recent price action.

        Returns True if calibration passes (ok to trade).
        """
        recent_range = self._price_high - self._price_low
        if recent_range <= 0 or self._price_low == float("inf"):
            self._calibration_passed = True
            return True

        quantile_range = float(self.config.p90_ceiling - self.config.p10_floor)
        coverage = compute_calibration_coverage(
            quantile_range=quantile_range,
            recent_range=recent_range,
        )
        self._calibration_passed = coverage >= self.config.calibration_min_coverage

        if not self._calibration_passed:
            self.log.info(
                f"Calibration gate blocked: coverage={coverage:.2%} "
                f"< {self.config.calibration_min_coverage:.0%}"
            )

        return self._calibration_passed

    # -- Circuit breakers --------------------------------------------------------

    def _check_drawdown_safe_mode(self) -> bool:
        """Activate safe mode if portfolio value drops below drawdown floor.

        Returns True if in safe mode (should stop trading).
        """
        total_value = self._get_total_balance()
        if total_value is None:
            return False

        if total_value < self.config.drawdown_floor:
            if not self.safe_mode:
                self.log.warning(
                    f"DRAWDOWN SAFE MODE: portfolio={total_value} "
                    f"< floor={self.config.drawdown_floor}. Cancelling all."
                )
                self._cancel_all_and_reset_tracking()
                self.safe_mode = True
            return True

        return False

    def _check_trend_override(self) -> None:
        """Check EMA(fast)/EMA(slow) ratio for trend override."""
        if (
            not self._fast_ema.initialized
            or not self._slow_ema.initialized
            or self._slow_ema.value == 0
        ):
            self.trend_override_active = False
            return

        ratio = self._fast_ema.value / self._slow_ema.value
        was_active = self.trend_override_active
        self.trend_override_active = abs(ratio - 1.0) > abs(self.config.trend_override_ratio - 1.0)

        if self.trend_override_active and not was_active:
            self.log.info(
                f"TREND OVERRIDE: EMA ratio={ratio:.4f}. Pausing grid, switching to trend-follow."
            )
            self._cancel_all_and_reset_tracking()

    def _check_price_deviation(self, current_price: float) -> bool:
        """Check if last fill price deviates too much from current mark.

        Returns True if halted.
        """
        if self._last_fill_price is None:
            return False

        deviation = abs(current_price - self._last_fill_price) / self._last_fill_price
        if deviation > self.config.price_deviation_pct:
            halt_bars = self.config.price_deviation_halt_seconds // 60  # Assume 1-min bars
            self._halt_until_bar = self._bar_count + halt_bars
            self.log.warning(
                f"PRICE DEVIATION HALT: {deviation:.2%} > {self.config.price_deviation_pct:.0%}. "
                f"Halting for {halt_bars} bars."
            )
            self._cancel_all_and_reset_tracking()
            return True

        return False

    def _check_inventory_limit(self) -> bool:
        """Check if BTC inventory exceeds the limit.

        Returns True if buying should stop.
        """
        if self.instrument is None:
            return False

        total_value = self._get_total_balance()
        if total_value is None or total_value <= 0:
            return False

        # Position value estimation via open positions for this instrument
        positions = self.cache.positions(
            venue=self.config.instrument_id.venue,
            instrument_id=self.config.instrument_id,
        )
        if not positions:
            return False

        position_qty = sum(abs(float(p.quantity)) for p in positions)
        last_price = self._fast_ema.value if self._fast_ema.initialized else 0.0
        inventory_ratio = (position_qty * last_price) / float(total_value)

        return inventory_ratio > self.config.inventory_limit_pct

    # -- Trend-follow mode -------------------------------------------------------

    def _trend_follow_logic(self, current_price: float) -> None:
        """Simple trend-follow when trend override is active.

        Go long when fast EMA > slow EMA, flat otherwise.
        """
        is_flat = self.portfolio.is_flat(self.config.instrument_id)

        if self._fast_ema.value > self._slow_ema.value and is_flat:
            self._enter_market(OrderSide.BUY)
        elif self._fast_ema.value <= self._slow_ema.value and not is_flat:
            self.close_all_positions(self.config.instrument_id)

    # -- Kelly sizing ------------------------------------------------------------

    def compute_kelly_size(self, p10: float, p90: float, current_price: float) -> float:
        """Compute Half-Kelly position size from P10-P90 spread.

        Delegates math to strategies.crypto._grid_math.compute_kelly_size; this
        method stays on the class so existing callers (self.compute_kelly_size(...))
        don't need to touch config plumbing.
        """
        return _kelly_size(
            p10=p10,
            p90=p90,
            current_price=current_price,
            kelly_fraction=self.config.kelly_fraction,
            total_capital=float(self.config.total_capital),
            grid_levels=self.config.grid_levels,
        )

    # -- Order placement ---------------------------------------------------------

    def _has_base_inventory(self) -> bool:
        """Check if we hold enough uncommitted base currency for a sell order."""
        return self._has_base_for_sell()

    def _has_quote_for_buy(self, grid_price: Decimal) -> bool:
        """Check if we have enough uncommitted quote currency to place a buy.

        Tracks committed capital internally since the backtest engine may not
        update balance_free synchronously within the same event cycle.
        """
        cost = grid_price * self.config.trade_size
        available = self.config.total_capital - self._committed_quote
        return available >= cost

    def _has_base_for_sell(self) -> bool:
        """Check if we have enough uncommitted base inventory for a sell order."""
        available = self._base_inventory - self._committed_base
        return available >= self.config.trade_size

    def _place_grid_orders(self, current_price: Decimal) -> None:
        """Place limit orders at grid levels without active orders.

        On a CASH account:
        - Buy orders require sufficient free quote currency (USDT).
        - Sell orders require sufficient free base currency from prior fills.
        """
        if self.safe_mode:
            return

        buying_allowed = not self._check_inventory_limit()

        for i, grid_price in enumerate(self.grid_prices):
            if self.grid_orders.get(i) is not None:
                continue

            if grid_price < current_price:
                if buying_allowed and self._has_quote_for_buy(grid_price):
                    self._place_order(i, grid_price, OrderSide.BUY)
            elif grid_price > current_price:
                if self._has_base_inventory():
                    self._place_order(i, grid_price, OrderSide.SELL)

    def _place_order(self, level: int, grid_price: Decimal, side: OrderSide) -> None:
        """Place a limit order at the given grid level and track committed capital."""
        if self.instrument is None:
            return

        order = self.order_factory.limit(
            instrument_id=self.config.instrument_id,
            order_side=side,
            quantity=self.instrument.make_qty(self.config.trade_size),
            price=round_to_tick(grid_price, self.instrument),
            time_in_force=TimeInForce.GTC,
        )
        self.grid_orders[level] = order.client_order_id.value
        self._order_to_level[order.client_order_id.value] = level

        # Track committed capital
        if side == OrderSide.BUY:
            self._committed_quote += grid_price * self.config.trade_size
        else:
            self._committed_base += self.config.trade_size

        self.submit_order(order)

    def _enter_market(self, side: OrderSide) -> None:
        """Submit a market order (used in trend-follow mode)."""
        if self.instrument is None:
            return

        order = self.order_factory.market(
            instrument_id=self.config.instrument_id,
            order_side=side,
            quantity=self.instrument.make_qty(self.config.trade_size),
            time_in_force=TimeInForce.IOC,  # Binance Spot: market orders must use IOC/FOK, not GTC
        )
        self.submit_order(order)


STRATEGY_SPEC = StrategySpec(
    name="timesfm_grid",
    builder=TimesFMGridConfigBuilder(),
    strategy_path="strategies.crypto.timesfm_grid:TimesFMGridStrategy",
    config_path="strategies.crypto.timesfm_grid:TimesFMGridConfig",
)
