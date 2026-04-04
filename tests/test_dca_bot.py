"""Unit tests for the DCA Bot strategy."""

import sys
from decimal import Decimal
from pathlib import Path

import pytest
from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.data import BarType
from nautilus_trader.model.enums import AccountType, OmsType, OrderSide
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.objects import Money
from nautilus_trader.persistence.catalog import ParquetDataCatalog
from nautilus_trader.test_kit.providers import TestInstrumentProvider

PROJECT_ROOT = str(Path(__file__).resolve().parents[1])
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from strategies.crypto.dca_bot import DCABotConfig, DCABotStrategy

CATALOG_PATH = Path(PROJECT_ROOT) / "catalog"


def _make_engine() -> BacktestEngine:
    """Create a BacktestEngine with standard SIM venue."""
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
        starting_balances=[Money(1_000_000, USD)],
    )
    return engine


def _load_ticks(engine: BacktestEngine, instrument):
    """Load tick data from catalog into the engine."""
    catalog = ParquetDataCatalog(str(CATALOG_PATH))
    ticks = catalog.quote_ticks(instrument_ids=[str(instrument.id)])
    assert ticks, "No tick data in catalog — run 'make backtest' to download sample data"
    engine.add_data(ticks)
    return ticks


def _run_dca_engine(**config_overrides) -> tuple[BacktestEngine, DCABotStrategy]:
    """Set up and run a DCA bot backtest, returning (engine, strategy)."""
    instrument = TestInstrumentProvider.default_fx_ccy("EUR/USD")
    engine = _make_engine()
    engine.add_instrument(instrument)
    _load_ticks(engine, instrument)

    bar_type = BarType.from_str(f"{instrument.id}-1-MINUTE-MID-INTERNAL")

    defaults = {
        "instrument_id": instrument.id,
        "bar_type": bar_type,
        "buy_amount": Decimal("25.0"),
        "buy_interval_bars": 60,
        "use_rsi_filter": False,
        "use_rsi_exit": False,
        "take_profit_pct": 0.0,
        "stop_loss_pct": 0.0,
    }
    defaults.update(config_overrides)
    config = DCABotConfig(**defaults)
    strategy = DCABotStrategy(config=config)
    engine.add_strategy(strategy)
    engine.run()
    return engine, strategy


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def _make_config(**overrides) -> DCABotConfig:
    """Create a DCABotConfig with sensible defaults for testing."""
    instrument = TestInstrumentProvider.default_fx_ccy("EUR/USD")
    bar_type = BarType.from_str(f"{instrument.id}-1-MINUTE-MID-INTERNAL")
    defaults = {
        "instrument_id": instrument.id,
        "bar_type": bar_type,
        "buy_amount": Decimal("25.0"),
    }
    defaults.update(overrides)
    return DCABotConfig(**defaults)


class TestDCABotConfig:
    """Pure unit tests for DCABotConfig."""

    def test_dca_config_frozen(self):
        """DCABotConfig is frozen (immutable) — assigning to a field should raise."""
        config = _make_config()
        with pytest.raises(AttributeError):
            config.buy_amount = Decimal("50.0")  # type: ignore[misc]

    def test_rsi_exit_enabled(self):
        """DCABotConfig accepts and stores use_rsi_exit field."""
        config_on = _make_config(use_rsi_exit=True)
        config_off = _make_config(use_rsi_exit=False)
        assert config_on.use_rsi_exit is True
        assert config_off.use_rsi_exit is False


@pytest.mark.skipif(not CATALOG_PATH.exists(), reason="No catalog data")
class TestDCABotBacktest:
    """Integration tests using BacktestEngine with test data."""

    def test_dca_buys_at_interval(self):
        """Engine should place DCA buy orders at the configured interval."""
        engine, _strategy = _run_dca_engine()
        assert engine.cache.orders_total_count() > 0, "Expected at least one DCA buy order"
        engine.dispose()

    def test_rsi_filter_skips_overbought(self):
        """With a very low RSI overbought threshold, many buys should be skipped.

        Note: NautilusTrader RSI returns values in [0, 1] range, not [0, 100].
        Setting rsi_overbought=0.3 means most RSI readings will exceed the threshold.
        """
        # Baseline: no filter, frequent buys
        engine_no_filter, _ = _run_dca_engine(
            use_rsi_filter=False,
            buy_interval_bars=15,
        )
        orders_no_filter = engine_no_filter.cache.orders_total_count()
        engine_no_filter.dispose()

        # With aggressive RSI filter (threshold=0.3 in [0,1] range)
        engine_filtered, _ = _run_dca_engine(
            use_rsi_filter=True,
            rsi_overbought=0.3,
            buy_interval_bars=15,
        )
        orders_filtered = engine_filtered.cache.orders_total_count()
        engine_filtered.dispose()

        assert orders_filtered < orders_no_filter, (
            f"RSI filter should reduce buys: {orders_filtered} filtered >= {orders_no_filter} unfiltered"
        )

    def test_take_profit_closes_position(self):
        """A very tight take-profit should trigger sell orders to close positions.

        In NETTING mode, close_all_positions flattens the net position but
        doesn't create a separate 'closed' position record. We check for sell
        orders in the cache as evidence of TP exits.
        """
        engine, _strategy = _run_dca_engine(
            take_profit_pct=0.0001,    # 0.01% — extremely tight
            buy_interval_bars=2,       # Buy very frequently to build position fast
            buy_amount=Decimal("5000"),  # Larger amount to ensure fills
        )
        orders = engine.cache.orders()
        sell_orders = [o for o in orders if o.side == OrderSide.SELL]
        assert len(sell_orders) > 0, "Expected sell orders from take-profit exits"
        engine.dispose()

    def test_stop_loss_closes_position(self):
        """A very tight stop-loss should trigger sell orders to close positions."""
        engine, _strategy = _run_dca_engine(
            stop_loss_pct=0.0001,      # 0.01% — extremely tight
            buy_interval_bars=2,       # Buy very frequently
            buy_amount=Decimal("5000"),  # Larger amount to ensure fills
        )
        orders = engine.cache.orders()
        sell_orders = [o for o in orders if o.side == OrderSide.SELL]
        assert len(sell_orders) > 0, "Expected sell orders from stop-loss exits"
        engine.dispose()

    def test_avg_entry_tracking(self):
        """After buys, _avg_entry_price and _total_invested should be updated."""
        engine, strategy = _run_dca_engine(buy_interval_bars=30)
        # The strategy should have executed multiple buys
        assert strategy._avg_entry_price > 0, "Average entry price should be positive after buys"
        assert strategy._total_invested > 0, "Total invested should be positive after buys"
        engine.dispose()

    def test_rsi_exit_partial_sell(self):
        """RSI exit should trigger partial sell orders when RSI exceeds threshold."""
        engine, strategy = _run_dca_engine(
            use_rsi_exit=True,
            rsi_exit_threshold=0.40,  # Low threshold so it triggers frequently
            partial_exit_pct=0.5,
            buy_interval_bars=15,     # Frequent buys to build position
            buy_amount=Decimal("5000"),
        )
        orders = engine.cache.orders()
        sell_orders = [o for o in orders if o.side == OrderSide.SELL]
        buy_orders = [o for o in orders if o.side == OrderSide.BUY]
        assert len(sell_orders) > 0, "Expected sell orders from RSI exits"
        # Cooldown should prevent cascade: sell count should be much less than buy count
        assert len(sell_orders) < len(buy_orders), (
            f"RSI exit cooldown should limit sells ({len(sell_orders)}) "
            f"vs buys ({len(buy_orders)})"
        )
        engine.dispose()

    def test_on_stop_keeps_positions(self):
        """on_stop should cancel orders but NOT close positions (long-term hold)."""
        engine, strategy = _run_dca_engine(buy_interval_bars=30)

        orders = engine.cache.orders()
        assert len(orders) > 0, "Should have placed buy orders"

        sell_orders = [o for o in orders if o.side == OrderSide.SELL]
        buy_orders = [o for o in orders if o.side == OrderSide.BUY]
        assert len(sell_orders) == 0, (
            f"on_stop should not close positions — found {len(sell_orders)} sell orders"
        )
        assert len(buy_orders) == len(orders), "All orders should be buys"
        engine.dispose()
