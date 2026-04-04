"""Unit tests for TimesFM Swing trading strategy."""

import sys
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest
from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.data import BarType
from nautilus_trader.model.enums import AccountType, OmsType
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.objects import Money
from nautilus_trader.persistence.catalog import ParquetDataCatalog
from nautilus_trader.test_kit.providers import TestInstrumentProvider

PROJECT_ROOT = str(Path(__file__).resolve().parents[1])
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from strategies.crypto.timesfm_swing import (
    TimesFMSwingConfig,
    TimesFMSwingStrategy,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SIM_VENUE = Venue("SIM")
INSTRUMENT = TestInstrumentProvider.default_fx_ccy("EUR/USD")
INSTRUMENT_ID = INSTRUMENT.id
BAR_TYPE = BarType.from_str(f"{INSTRUMENT_ID}-1-MINUTE-MID-INTERNAL")
CATALOG_PATH = Path(PROJECT_ROOT) / "catalog"

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def default_config() -> TimesFMSwingConfig:
    return TimesFMSwingConfig(
        instrument_id=INSTRUMENT_ID,
        bar_type=BAR_TYPE,
        trade_size=Decimal("100_000"),
    )


def _build_engine() -> BacktestEngine:
    """Build a BacktestEngine with SIM venue and EUR/USD instrument."""
    engine = BacktestEngine(
        config=BacktestEngineConfig(
            logging=LoggingConfig(log_level="ERROR"),
        ),
    )
    engine.add_venue(
        venue=SIM_VENUE,
        oms_type=OmsType.NETTING,
        account_type=AccountType.MARGIN,
        base_currency=USD,
        starting_balances=[Money(1_000_000, USD)],
    )
    engine.add_instrument(INSTRUMENT)
    return engine


def _load_ticks(engine: BacktestEngine) -> None:
    """Load tick data from the parquet catalog into the engine."""
    catalog = ParquetDataCatalog(str(CATALOG_PATH))
    ticks = catalog.quote_ticks(instrument_ids=[str(INSTRUMENT_ID)])
    assert ticks, "No tick data in catalog -- run 'make backtest' to download sample data"
    engine.add_data(ticks)


def _run_strategy(**config_overrides) -> tuple[BacktestEngine, TimesFMSwingStrategy]:
    """Build engine, load data, run strategy in fallback mode. Returns (engine, strategy)."""
    engine = _build_engine()
    _load_ticks(engine)

    config_kwargs = {
        "instrument_id": INSTRUMENT_ID,
        "bar_type": BAR_TYPE,
        "trade_size": Decimal("100_000"),
    }
    config_kwargs.update(config_overrides)
    config = TimesFMSwingConfig(**config_kwargs)

    with patch("strategies.crypto.timesfm_swing.TIMESFM_AVAILABLE", False):
        strategy = TimesFMSwingStrategy(config=config)
        engine.add_strategy(strategy)
        engine.run()

    return engine, strategy


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

catalog_exists = pytest.mark.skipif(
    not CATALOG_PATH.exists(),
    reason="No catalog data -- run 'make backtest' to download",
)


class TestTimesFMSwingConfig:
    """Config-level tests (no engine needed)."""

    def test_config_defaults(self, default_config: TimesFMSwingConfig) -> None:
        """Verify default values for all optional config fields."""
        assert default_config.lookback_bars == 512
        assert default_config.forecast_horizon == 24
        assert default_config.confidence_threshold == 0.6
        assert default_config.ema_period == 200
        assert default_config.stop_loss_pct == 0.02
        assert default_config.take_profit_pct == 0.04
        assert default_config.fallback_fast_ema_period == 50
        assert default_config.forecast_interval_bars == 4

    def test_timesfm_config_frozen(self, default_config: TimesFMSwingConfig) -> None:
        """Verify TimesFMSwingConfig is immutable (frozen)."""
        with pytest.raises(AttributeError):
            default_config.lookback_bars = 256  # type: ignore[misc]


class TestBacktestIntegration:
    """Integration tests running the strategy inside BacktestEngine."""

    @catalog_exists
    def test_fallback_mode_without_timesfm(self) -> None:
        """Strategy initializes and runs in EMA-only mode when TIMESFM_AVAILABLE=False."""
        engine, strategy = _run_strategy()

        assert not strategy._model_available
        assert strategy._model is None
        engine.dispose()

    @catalog_exists
    def test_strategy_runs_and_places_orders(self) -> None:
        """Strategy completes a backtest run without error and places orders."""
        engine, strategy = _run_strategy()

        assert engine.trader.generate_order_fills_report().shape[0] > 0
        engine.dispose()

    @catalog_exists
    def test_stop_loss_exit(self) -> None:
        """With a very tight stop_loss_pct, positions should get closed."""
        engine, _ = _run_strategy(
            stop_loss_pct=0.001,
            take_profit_pct=0.50,
        )

        assert engine.trader.generate_order_fills_report().shape[0] > 1
        engine.dispose()

    @catalog_exists
    def test_take_profit_exit(self) -> None:
        """With a very tight take_profit_pct, positions should get closed."""
        engine, _ = _run_strategy(
            stop_loss_pct=0.50,
            take_profit_pct=0.001,
        )

        assert engine.trader.generate_order_fills_report().shape[0] > 1
        engine.dispose()

    @catalog_exists
    def test_price_buffer_respects_lookback_limit(self) -> None:
        """Price buffer grows but never exceeds lookback_bars."""
        engine, strategy = _run_strategy()

        assert 0 < len(strategy._price_buffer) <= strategy.config.lookback_bars
        engine.dispose()

    @catalog_exists
    def test_ema_indicators_initialized(self) -> None:
        """Both fast and slow EMAs initialize with correct periods."""
        engine, strategy = _run_strategy(
            ema_period=200,
            fallback_fast_ema_period=50,
        )

        assert strategy.ema.initialized
        assert strategy.fast_ema.initialized
        assert strategy.ema.period == 200
        assert strategy.fast_ema.period == 50
        engine.dispose()
