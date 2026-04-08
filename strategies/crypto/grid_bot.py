from decimal import Decimal

from nautilus_trader.config import PositiveInt, StrategyConfig
from nautilus_trader.indicators import ExponentialMovingAverage
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.events import OrderFilled
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.objects import Price
from nautilus_trader.trading.strategy import Strategy


class GridBotConfig(StrategyConfig, frozen=True):
    instrument_id: InstrumentId
    bar_type: BarType
    trade_size: Decimal
    upper_price: Decimal
    lower_price: Decimal
    grid_levels: PositiveInt = 20
    max_open_orders: PositiveInt = 10
    # Trend detection: pause grid when EMA slope indicates strong trend
    ema_period: PositiveInt = 200
    trend_threshold: float = 0.001  # Min EMA slope to consider "trending"
    use_trend_filter: bool = True
    # Hard stop-loss below grid floor (debate finding: capital trap risk)
    stop_loss_pct: float = 0.03  # 3% below lower_price


class GridBotStrategy(Strategy):
    """Automated grid trading strategy for crypto markets.

    Places limit buy orders below and limit sell orders above the current price
    at evenly spaced grid levels. When a buy fills, a sell is placed one level up;
    when a sell fills, a buy is placed one level down -- recycling through the grid.
    """

    def __init__(self, config: GridBotConfig) -> None:
        super().__init__(config)
        self.instrument: Instrument | None = None
        self.grid_prices: list[Price] = []
        # Maps grid level index -> client_order_id (or None if no active order)
        self.grid_orders: dict[int, str | None] = {}
        # Maps client_order_id -> grid level index for fill lookups
        self._order_to_level: dict[str, int] = {}
        # Trend filter (debate finding: BTC trends 70% of the time)
        self._ema = ExponentialMovingAverage(config.ema_period)
        self._prev_ema_value: float = 0.0
        self._is_trending: bool = False
        # Stop-loss price (debate finding: capital trap on crashes)
        self._stop_loss_price: Decimal = Decimal("0")

    def on_start(self) -> None:
        self.instrument = self.cache.instrument(self.config.instrument_id)
        if self.instrument is None:
            self.log.error(f"Could not find instrument for {self.config.instrument_id}")
            self.stop()
            return

        if self.config.lower_price >= self.config.upper_price:
            self.log.error("lower_price must be less than upper_price")
            self.stop()
            return

        if self.config.grid_levels < 2:
            self.log.error("grid_levels must be at least 2")
            self.stop()
            return

        # Calculate evenly spaced grid prices (inclusive of bounds), then snap
        # each level to the instrument's tick size via make_price(). This is the
        # NautilusTrader-idiomatic way to align prices with the venue's
        # PRICE_FILTER rules -- a plain decimal round() can still leave sub-tick
        # residue for instruments where tick size != 10^-price_precision.
        step = (self.config.upper_price - self.config.lower_price) / (
            self.config.grid_levels - 1
        )
        raw_levels = [
            self.config.lower_price + step * i for i in range(self.config.grid_levels)
        ]

        snapped: list[Price] = []
        for raw in raw_levels:
            price = self.instrument.make_price(raw)
            # De-duplicate: consecutive levels may collapse to the same Price
            # after rounding when the grid spacing is finer than tick size.
            if snapped and snapped[-1] == price:
                continue
            snapped.append(price)
        self.grid_prices = snapped

        self.log.info(f"Grid levels: {[str(p) for p in self.grid_prices]}")

        # Initialize grid state -- no active orders yet
        self.grid_orders = dict.fromkeys(range(len(self.grid_prices)))

        # Register trend filter EMA
        self.register_indicator_for_bars(self.config.bar_type, self._ema)
        self.subscribe_bars(self.config.bar_type)

        # Calculate stop-loss price (3% below grid floor by default)
        self._stop_loss_price = self.config.lower_price * (
            1 - Decimal(str(self.config.stop_loss_pct))
        )

        self.log.info(
            f"Grid initialized: {len(self.grid_prices)} levels "
            f"(requested {self.config.grid_levels}) "
            f"from {self.config.lower_price} to {self.config.upper_price} "
            f"| stop-loss at {self._stop_loss_price} "
            f"| trend filter: {'ON' if self.config.use_trend_filter else 'OFF'}",
        )

    def on_bar(self, bar: Bar) -> None:
        current_price = Decimal(str(bar.close))

        # Hard stop-loss: close everything if price crashes below grid
        if current_price <= self._stop_loss_price:
            self.log.warning(
                f"Stop-loss triggered at {current_price} "
                f"(below {self._stop_loss_price}). Closing all.",
            )
            self.cancel_all_orders(self.config.instrument_id)
            self.close_all_positions(self.config.instrument_id)
            return

        # Trend detection: pause grid when market is trending
        if self._ema.initialized:
            if self._prev_ema_value == 0.0:
                # First bar after EMA warmup — seed previous value, skip slope calc
                self._prev_ema_value = self._ema.value
            else:
                ema_slope = (self._ema.value - self._prev_ema_value) / self._prev_ema_value
                self._prev_ema_value = self._ema.value

                if self.config.use_trend_filter:
                    self._is_trending = abs(ema_slope) > self.config.trend_threshold
                    if self._is_trending:
                        self.log.info(
                            f"Market trending (EMA slope={ema_slope:.6f}). Grid paused.",
                        )
                        return

        self._place_grid_orders(current_price)

    def _count_open_orders(self) -> int:
        """Return the number of grid levels with an active order."""
        return sum(1 for oid in self.grid_orders.values() if oid is not None)

    def _place_grid_orders(self, current_price: Decimal) -> None:
        """Place limit orders at grid levels that don't already have one."""
        open_count = self._count_open_orders()

        for i, grid_price in enumerate(self.grid_prices):
            if open_count >= self.config.max_open_orders:
                break

            # Skip levels that already have an active order
            if self.grid_orders.get(i) is not None:
                continue

            # Skip the level closest to current price (no edge)
            if grid_price < current_price:
                self._place_order(i, grid_price, OrderSide.BUY)
            elif grid_price > current_price:
                self._place_order(i, grid_price, OrderSide.SELL)
            else:
                continue

            open_count += 1

    def _place_order(self, level: int, grid_price: Price, side: OrderSide) -> None:
        """Place a limit order at the given grid level."""
        if self.instrument is None:
            return

        order = self.order_factory.limit(
            instrument_id=self.config.instrument_id,
            order_side=side,
            quantity=self.instrument.make_qty(self.config.trade_size),
            price=grid_price,
            time_in_force=TimeInForce.GTC,
        )
        self.grid_orders[level] = order.client_order_id.value
        self._order_to_level[order.client_order_id.value] = level
        self.submit_order(order)
        self.log.info(
            f"Grid {side.name} at level {level} price={grid_price}",
        )

    def on_event(self, event) -> None:
        """Handle order fills to recycle grid orders."""
        if not isinstance(event, OrderFilled):
            return

        order_id = event.client_order_id.value
        level = self._order_to_level.pop(order_id, None)
        if level is None:
            return

        # Clear the filled level
        self.grid_orders[level] = None

        filled_side = event.order_side

        if filled_side == OrderSide.BUY and level + 1 < len(self.grid_prices):
            # Buy filled -> place sell one level up
            next_level = level + 1
            if self.grid_orders.get(next_level) is None:
                self._place_order(next_level, self.grid_prices[next_level], OrderSide.SELL)
        elif filled_side == OrderSide.SELL and level - 1 >= 0:
            # Sell filled -> place buy one level down
            next_level = level - 1
            if self.grid_orders.get(next_level) is None:
                self._place_order(next_level, self.grid_prices[next_level], OrderSide.BUY)

    def on_stop(self) -> None:
        self.cancel_all_orders(self.config.instrument_id)
        self.close_all_positions(self.config.instrument_id)
        self.unsubscribe_bars(self.config.bar_type)

    def on_reset(self) -> None:
        self.grid_prices.clear()
        self.grid_orders.clear()
        self._order_to_level.clear()
        self._prev_ema_value = 0.0
        self._is_trending = False
        self._stop_loss_price = Decimal("0")
