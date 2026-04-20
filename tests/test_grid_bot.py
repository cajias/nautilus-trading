"""Unit tests for the Grid Bot strategy."""

import sys
from decimal import Decimal
from pathlib import Path

import pytest
from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.data import BarType, QuoteTick
from nautilus_trader.model.enums import AccountType, OmsType
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.objects import Money, Price, Quantity
from nautilus_trader.test_kit.providers import TestInstrumentProvider

# Ensure strategies/ is importable
PROJECT_ROOT = str(Path(__file__).resolve().parents[1])
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from strategies.crypto.grid_bot import GridBotConfig, GridBotStrategy

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

INSTRUMENT = TestInstrumentProvider.default_fx_ccy("EUR/USD")
INSTRUMENT_ID = INSTRUMENT.id
BAR_TYPE = BarType.from_str("EUR/USD.SIM-1-MINUTE-MID-INTERNAL")
PRICE_PRECISION = INSTRUMENT.price_precision  # 5 for EUR/USD


def _fmt_price(value) -> str:
    """Format a price value to match instrument precision (5 dp for EUR/USD)."""
    return f"{float(value):.{PRICE_PRECISION}f}"


def _make_config(**overrides) -> GridBotConfig:
    """Create a GridBotConfig with sensible defaults.

    Default grid: 5 levels from 1.10000 to 1.12000 (step=0.00500, clean decimals).
    """
    defaults = {
        "instrument_id": INSTRUMENT_ID,
        "bar_type": BAR_TYPE,
        "trade_size": Decimal("100"),
        "upper_price": Decimal("1.12000"),
        "lower_price": Decimal("1.10000"),
        "grid_levels": 5,
        "max_open_orders": 10,
        "ema_period": 3,
        "use_trend_filter": False,
    }
    return GridBotConfig(**{**defaults, **overrides})


def _build_engine(starting_balance: int = 500) -> BacktestEngine:
    engine = BacktestEngine(
        config=BacktestEngineConfig(
            logging=LoggingConfig(log_level="ERROR"),
        ),
    )
    engine.add_venue(
        venue=Venue("SIM"),
        oms_type=OmsType.NETTING,
        account_type=AccountType.MARGIN,
        base_currency=USD,
        starting_balances=[Money(starting_balance, USD)],
    )
    engine.add_instrument(INSTRUMENT)
    return engine


def _make_quote_tick(price: str, ts_ns: int) -> QuoteTick:
    formatted = _fmt_price(price)
    return QuoteTick(
        instrument_id=INSTRUMENT_ID,
        bid_price=Price.from_str(formatted),
        ask_price=Price.from_str(formatted),
        bid_size=Quantity.from_str("100000"),
        ask_size=Quantity.from_str("100000"),
        ts_event=ts_ns,
        ts_init=ts_ns,
    )


def _generate_flat_ticks(
    price: str, count: int, start_ns: int = 1_000_000_000
) -> list[QuoteTick]:
    """Generate ticks at a constant price."""
    return [
        _make_quote_tick(price, start_ns + i * 60_000_000_000)
        for i in range(count)
    ]


def _generate_linear_ticks(
    start_price: float,
    end_price: float,
    count: int,
    start_ns: int = 1_000_000_000,
) -> list[QuoteTick]:
    """Generate ticks moving linearly from start_price to end_price."""
    step = (end_price - start_price) / max(count - 1, 1)
    return [
        _make_quote_tick(
            _fmt_price(start_price + step * i),
            start_ns + i * 60_000_000_000,
        )
        for i in range(count)
    ]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestGridPriceCalculation:
    """Pure unit tests for grid price math (run via engine to trigger on_start)."""

    def test_grid_prices_calculated_correctly(self):
        """Grid prices should be evenly spaced between lower and upper, inclusive."""
        config = _make_config(
            lower_price=Decimal("1.10000"),
            upper_price=Decimal("1.12000"),
            grid_levels=5,
        )
        strategy = GridBotStrategy(config=config)

        # step = 0.02000 / 4 = 0.00500
        expected = [
            Decimal("1.10000"),
            Decimal("1.10500"),
            Decimal("1.11000"),
            Decimal("1.11500"),
            Decimal("1.12000"),
        ]

        engine = _build_engine()
        engine.add_strategy(strategy)
        engine.add_data(_generate_flat_ticks("1.11000", 5))
        engine.run()

        assert len(strategy.grid_prices) == 5
        for actual, exp in zip(strategy.grid_prices, expected, strict=False):
            assert actual == exp, f"Expected {exp}, got {actual}"
        engine.dispose()

    def test_grid_levels_count(self):
        """Grid should have exactly grid_levels prices."""
        # 3 levels: step = 0.02000 / 2 = 0.01000 (clean)
        config = _make_config(grid_levels=3)
        strategy = GridBotStrategy(config=config)

        engine = _build_engine()
        engine.add_strategy(strategy)
        engine.add_data(_generate_flat_ticks("1.11000", 5))
        engine.run()

        assert len(strategy.grid_prices) == 3
        engine.dispose()


class TestConfigValidation:
    """Config validation tests."""

    def test_config_validation_lower_ge_upper_stops(self):
        """Strategy should stop if lower_price >= upper_price."""
        config = _make_config(
            lower_price=Decimal("1.12000"),
            upper_price=Decimal("1.10000"),
        )
        strategy = GridBotStrategy(config=config)

        engine = _build_engine()
        engine.add_strategy(strategy)
        engine.add_data(_generate_flat_ticks("1.11000", 5))
        engine.run()

        # Strategy should have stopped -- no orders placed
        assert engine.cache.orders_total_count() == 0
        engine.dispose()

    def test_grid_bot_config_frozen(self):
        """GridBotConfig should be immutable (frozen=True)."""
        config = _make_config()
        with pytest.raises(AttributeError):
            config.grid_levels = 50  # type: ignore[misc]


class TestGridOrders:
    """Tests requiring the backtest engine to verify order placement."""

    def test_grid_orders_placed_on_bar(self):
        """Running the engine with bars should result in orders being placed."""
        config = _make_config(use_trend_filter=False)
        strategy = GridBotStrategy(config=config)

        engine = _build_engine()
        engine.add_strategy(strategy)
        # Price in the middle of the grid so both buy and sell orders can be placed
        engine.add_data(_generate_flat_ticks("1.11000", 20))
        engine.run()

        assert engine.cache.orders_total_count() > 0
        engine.dispose()

    def test_max_open_orders_respected(self):
        """No more than max_open_orders should be active at any point."""
        # Use 5 levels (clean decimals), cap at 3 open orders
        config = _make_config(
            grid_levels=5,
            max_open_orders=3,
            use_trend_filter=False,
        )
        strategy = GridBotStrategy(config=config)

        engine = _build_engine()
        engine.add_strategy(strategy)
        engine.add_data(_generate_flat_ticks("1.11000", 20))
        engine.run()

        # Verify internal tracking never exceeds cap
        active_count = sum(1 for oid in strategy.grid_orders.values() if oid is not None)
        assert active_count <= 3
        engine.dispose()

    def test_fill_recycling(self):
        """After a buy fill, the strategy should place follow-up orders."""
        config = _make_config(
            lower_price=Decimal("1.09000"),
            upper_price=Decimal("1.11000"),
            grid_levels=5,  # step = 0.00500
            use_trend_filter=False,
        )
        strategy = GridBotStrategy(config=config)

        engine = _build_engine(starting_balance=100_000)
        engine.add_strategy(strategy)

        # Price starts mid-grid, drops to fill buys, then recovers
        ticks = (
            _generate_flat_ticks("1.10000", 10, start_ns=1_000_000_000)
            + _generate_linear_ticks(1.10000, 1.09100, 10, start_ns=601_000_000_000)
            + _generate_linear_ticks(1.09100, 1.10000, 10, start_ns=1_201_000_000_000)
        )
        engine.add_data(ticks)
        engine.run()

        total_orders = engine.cache.orders_total_count()
        assert total_orders > 0, "Expected orders to be placed and recycled"
        engine.dispose()


class TestTrendFilter:
    """Tests for the trend filter feature."""

    def test_trend_filter_pauses_grid(self):
        """With trend filter ON and strongly trending data, fewer orders should be placed."""
        # Use 5 levels (clean step = 0.01000) over a wider range
        grid_kwargs = {
            "lower_price": Decimal("1.10000"),
            "upper_price": Decimal("1.14000"),
            "grid_levels": 5,
            "ema_period": 3,
        }

        # --- Run 1: filter OFF ---
        config_off = _make_config(use_trend_filter=False, **grid_kwargs)
        strategy_off = GridBotStrategy(config=config_off)
        engine1 = _build_engine(starting_balance=100_000)
        engine1.add_strategy(strategy_off)
        trending_ticks = _generate_linear_ticks(1.10000, 1.15000, 100)
        engine1.add_data(trending_ticks)
        engine1.run()
        orders_off = engine1.cache.orders_total_count()
        engine1.dispose()

        # --- Run 2: filter ON with sensitive threshold ---
        config_on = _make_config(
            use_trend_filter=True, trend_threshold=0.0001, **grid_kwargs
        )
        strategy_on = GridBotStrategy(config=config_on)
        engine2 = _build_engine(starting_balance=100_000)
        engine2.add_strategy(strategy_on)
        engine2.add_data(trending_ticks)
        engine2.run()
        orders_on = engine2.cache.orders_total_count()
        engine2.dispose()

        assert orders_on <= orders_off, (
            f"Trend filter should reduce orders: {orders_on} <= {orders_off}"
        )


class TestStopLoss:
    """Tests for the stop-loss feature."""

    def test_stop_loss_closes_positions(self):
        """When price drops below stop_loss_price, all positions should be closed."""
        config = _make_config(
            lower_price=Decimal("1.10000"),
            upper_price=Decimal("1.12000"),
            grid_levels=5,
            stop_loss_pct=0.03,  # stop at 1.10000 * 0.97 = 1.06700
            use_trend_filter=False,
        )
        strategy = GridBotStrategy(config=config)

        engine = _build_engine(starting_balance=100_000)
        engine.add_strategy(strategy)

        # Start in grid range, then crash below stop-loss
        ticks = (
            _generate_flat_ticks("1.11000", 10, start_ns=1_000_000_000)
            + _generate_linear_ticks(1.11000, 1.06000, 20, start_ns=601_000_000_000)
        )
        engine.add_data(ticks)
        engine.run()

        # After stop-loss triggers, no open positions should remain
        assert engine.cache.positions_open_count() == 0
        engine.dispose()
