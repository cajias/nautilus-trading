"""Unit tests for the Kronos integration (KronosSignal, KronosActor, KronosStrategy).

Test strategy:
  - Config tests: no engine needed (pure Python)
  - Signal tests: verify KronosSignal fields and helpers
  - Actor tests: model loading fallback, buffer management, signal building
  - Integration tests: full BacktestEngine run in EMA-fallback mode
    (skipped when catalog data is absent — run 'make backtest' first)

All integration tests patch KRONOS_AVAILABLE=False so they don't require
the kronos package to be installed (mirrors how test_timesfm_swing.py
patches TIMESFM_AVAILABLE).
"""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
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

# Ensure repo root on sys.path
PROJECT_ROOT = str(Path(__file__).resolve().parents[1])
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from strategies.crypto.kronos.actor import KronosActor, KronosActorConfig
from strategies.crypto.kronos.signal import KronosSignal
from strategies.crypto.kronos.strategy import KronosStrategy, KronosStrategyConfig

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SIM_VENUE = Venue("SIM")
INSTRUMENT = TestInstrumentProvider.default_fx_ccy("EUR/USD")
INSTRUMENT_ID = INSTRUMENT.id
BAR_TYPE = BarType.from_str(f"{INSTRUMENT_ID}-1-MINUTE-MID-INTERNAL")
CATALOG_PATH = Path(PROJECT_ROOT) / "catalog"

catalog_exists = pytest.mark.skipif(
    not CATALOG_PATH.exists(),
    reason="No catalog data — run 'make backtest' to download",
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def default_strategy_config() -> KronosStrategyConfig:
    return KronosStrategyConfig(
        instrument_id=INSTRUMENT_ID,
        bar_type=BAR_TYPE,
        trade_size=Decimal("100_000"),
    )


@pytest.fixture()
def default_actor_config() -> KronosActorConfig:
    return KronosActorConfig(
        instrument_id=INSTRUMENT_ID,
        bar_type=BAR_TYPE,
        model_size="mini",
        forecast_horizon=12,
        inference_interval_bars=2,
        n_samples=10,
    )


def _build_engine() -> BacktestEngine:
    engine = BacktestEngine(
        config=BacktestEngineConfig(logging=LoggingConfig(log_level="ERROR")),
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
    catalog = ParquetDataCatalog(str(CATALOG_PATH))
    ticks = catalog.quote_ticks(instrument_ids=[str(INSTRUMENT_ID)])
    assert ticks, "No tick data — run 'make backtest' to download sample data"
    engine.add_data(ticks)


# ---------------------------------------------------------------------------
# KronosSignal tests
# ---------------------------------------------------------------------------


class TestKronosSignal:
    def test_bullish_signal(self) -> None:
        sig = KronosSignal(
            instrument_id="BTCUSDT.BINANCE",
            direction=1.0,
            confidence=0.8,
            predicted_return_pct=0.02,
            forecast_close=51000.0,
            forecast_high=51500.0,
            forecast_low=50500.0,
            model_size="mini",
            ts_event=0,
            ts_init=0,
        )
        assert sig.is_bullish()
        assert not sig.is_bearish()

    def test_bearish_signal(self) -> None:
        sig = KronosSignal(
            instrument_id="BTCUSDT.BINANCE",
            direction=-1.0,
            confidence=0.7,
            predicted_return_pct=-0.015,
            forecast_close=49000.0,
            forecast_high=50000.0,
            forecast_low=48500.0,
            model_size="mini",
            ts_event=0,
            ts_init=0,
        )
        assert sig.is_bearish()
        assert not sig.is_bullish()

    def test_neutral_signal(self) -> None:
        sig = KronosSignal(
            instrument_id="BTCUSDT.BINANCE",
            direction=0.0,
            confidence=0.0,
            predicted_return_pct=0.0,
            forecast_close=50000.0,
            forecast_high=50000.0,
            forecast_low=50000.0,
            model_size="mini",
            ts_event=0,
            ts_init=0,
        )
        assert not sig.is_bullish()
        assert not sig.is_bearish()

    def test_repr_contains_key_fields(self) -> None:
        sig = KronosSignal(
            instrument_id="BTCUSDT.BINANCE",
            direction=1.0,
            confidence=0.75,
            predicted_return_pct=0.012,
            forecast_close=51200.0,
            forecast_high=51500.0,
            forecast_low=50800.0,
            model_size="mini",
            ts_event=0,
            ts_init=0,
        )
        r = repr(sig)
        assert "BTCUSDT.BINANCE" in r
        assert "↑" in r
        assert "0.75" in r


# ---------------------------------------------------------------------------
# KronosActorConfig tests
# ---------------------------------------------------------------------------


class TestKronosActorConfig:
    def test_defaults(self, default_actor_config: KronosActorConfig) -> None:
        assert default_actor_config.model_size == "mini"
        assert default_actor_config.forecast_horizon == 12
        assert default_actor_config.inference_interval_bars == 2
        assert default_actor_config.n_samples == 10
        assert default_actor_config.huggingface_repo_id is None

    def test_frozen(self, default_actor_config: KronosActorConfig) -> None:
        with pytest.raises(AttributeError):
            default_actor_config.model_size = "base"  # type: ignore[misc]

    def test_custom_repo_id(self) -> None:
        cfg = KronosActorConfig(
            instrument_id=INSTRUMENT_ID,
            bar_type=BAR_TYPE,
            huggingface_repo_id="my-org/kronos-finetuned",
        )
        assert cfg.huggingface_repo_id == "my-org/kronos-finetuned"


# ---------------------------------------------------------------------------
# KronosStrategyConfig tests
# ---------------------------------------------------------------------------


class TestKronosStrategyConfig:
    def test_defaults(self, default_strategy_config: KronosStrategyConfig) -> None:
        assert default_strategy_config.min_confidence == 0.55
        assert default_strategy_config.min_predicted_return_pct == 0.008
        assert default_strategy_config.stop_loss_pct == 0.02
        assert default_strategy_config.take_profit_pct == 0.04
        assert default_strategy_config.max_drawdown_pct == 0.10
        assert default_strategy_config.use_fallback_ema is True
        assert default_strategy_config.fallback_ema_fast_period == 20
        assert default_strategy_config.fallback_ema_slow_period == 50

    def test_frozen(self, default_strategy_config: KronosStrategyConfig) -> None:
        with pytest.raises(AttributeError):
            default_strategy_config.stop_loss_pct = 0.05  # type: ignore[misc]

    def test_risk_reward_ratio_default(
        self, default_strategy_config: KronosStrategyConfig
    ) -> None:
        """Default TP/SL = 4%/2% = 2:1 reward:risk."""
        rr = (
            default_strategy_config.take_profit_pct
            / default_strategy_config.stop_loss_pct
        )
        assert rr == pytest.approx(2.0)


# ---------------------------------------------------------------------------
# KronosActor unit tests (no engine)
# ---------------------------------------------------------------------------


class TestKronosActorBuildSignal:
    """Test _build_signal() in isolation using a mock actor.

    Kronos returns a pandas DataFrame (not numpy array), so we build fake
    DataFrames matching the KronosPredictor.predict() output format:
        columns: ['open', 'high', 'low', 'close', 'volume', 'amount']
        index:   y_timestamp (future pd.DatetimeIndex)
    """

    def _make_actor(self) -> KronosActor:
        config = KronosActorConfig(
            instrument_id=INSTRUMENT_ID,
            bar_type=BAR_TYPE,
            model_size="mini",
            forecast_horizon=10,
        )
        actor = KronosActor.__new__(KronosActor)
        actor._config = config
        actor.log = MagicMock()
        actor.config = config
        actor._max_context = 2048
        return actor

    def _make_pred_df(
        self, horizon: int = 10, direction: str = "up"
    ) -> "pd.DataFrame":
        """Build a fake Kronos forecast DataFrame."""
        base_close = 50000.0
        delta = 500.0 if direction == "up" else -500.0
        rng = np.random.default_rng(42)
        closes = base_close + delta + rng.normal(0, 20, horizon)
        highs = closes + rng.uniform(80, 150, horizon)
        lows = closes - rng.uniform(80, 150, horizon)
        opens = closes + rng.normal(0, 15, horizon)
        volumes = rng.uniform(100, 200, horizon)
        idx = pd.date_range("2024-01-02", periods=horizon, freq="1h", tz="UTC")
        return pd.DataFrame(
            {"open": opens, "high": highs, "low": lows, "close": closes, "volume": volumes},
            index=idx,
        )

    def _make_bar(self) -> MagicMock:
        bar = MagicMock()
        bar.ts_event = 1_000_000_000
        bar.ts_init = 1_000_000_000
        return bar

    def test_bullish_forecast_produces_bullish_signal(self) -> None:
        actor = self._make_actor()
        pred_df = self._make_pred_df(direction="up")
        bar = self._make_bar()
        sig = actor._build_signal(pred_df, 50000.0, bar)

        assert sig is not None
        assert sig.is_bullish()
        assert sig.predicted_return_pct > 0
        assert 0.0 <= sig.confidence <= 1.0

    def test_bearish_forecast_produces_bearish_signal(self) -> None:
        actor = self._make_actor()
        pred_df = self._make_pred_df(direction="down")
        bar = self._make_bar()
        sig = actor._build_signal(pred_df, 50000.0, bar)

        assert sig is not None
        assert sig.is_bearish()
        assert sig.predicted_return_pct < 0

    def test_none_forecast_returns_none(self) -> None:
        actor = self._make_actor()
        bar = self._make_bar()
        sig = actor._build_signal(None, 50000.0, bar)  # type: ignore[arg-type]
        assert sig is None

    def test_empty_forecast_returns_none(self) -> None:
        actor = self._make_actor()
        bar = self._make_bar()
        empty_df = pd.DataFrame({"open": [], "high": [], "low": [], "close": []})
        sig = actor._build_signal(empty_df, 50000.0, bar)
        assert sig is None

    def test_forecast_close_reflects_terminal_bar(self) -> None:
        actor = self._make_actor()
        pred_df = self._make_pred_df(direction="up")
        bar = self._make_bar()
        sig = actor._build_signal(pred_df, 50000.0, bar)

        assert sig is not None
        # forecast_close should match the last row's close
        assert sig.forecast_close == pytest.approx(pred_df["close"].iloc[-1], abs=0.01)

    def test_model_size_stored_in_signal(self) -> None:
        actor = self._make_actor()
        pred_df = self._make_pred_df()
        bar = self._make_bar()
        sig = actor._build_signal(pred_df, 50000.0, bar)

        assert sig is not None
        assert sig.model_size == "mini"

    def test_forecast_high_is_max_over_horizon(self) -> None:
        actor = self._make_actor()
        pred_df = self._make_pred_df(direction="up")
        bar = self._make_bar()
        sig = actor._build_signal(pred_df, 50000.0, bar)

        assert sig is not None
        assert sig.forecast_high == pytest.approx(pred_df["high"].max(), abs=0.01)


# ---------------------------------------------------------------------------
# Integration tests (require catalog data)
# ---------------------------------------------------------------------------


class TestKronosStrategyIntegration:
    """Integration tests running KronosStrategy inside BacktestEngine.

    The actor is NOT added in these tests — we rely on the EMA fallback to
    produce trades. This keeps the tests fast and dependency-free.
    """

    def _run(self, **config_overrides: object) -> tuple[BacktestEngine, KronosStrategy]:
        """Build engine, load data, run strategy in fallback mode."""
        engine = _build_engine()
        _load_ticks(engine)

        kwargs = {
            "instrument_id": INSTRUMENT_ID,
            "bar_type": BAR_TYPE,
            "trade_size": Decimal("100_000"),
        }
        kwargs.update(config_overrides)
        config = KronosStrategyConfig(**kwargs)  # type: ignore[arg-type]

        with patch("strategies.crypto.kronos.actor.KRONOS_AVAILABLE", False):
            strategy = KronosStrategy(config=config)
            engine.add_strategy(strategy)
            engine.run()

        return engine, strategy

    @catalog_exists
    def test_strategy_starts_and_completes(self) -> None:
        """Strategy runs to completion without errors."""
        engine, strategy = self._run()
        # If it got this far without exception, it passed
        engine.dispose()

    @catalog_exists
    def test_fallback_mode_places_orders(self) -> None:
        """EMA fallback fires trades when no Kronos signals arrive."""
        engine, strategy = self._run(
            fallback_warmup_bars=10,  # short warmup for test
        )
        fills = engine.trader.generate_order_fills_report()
        assert len(fills) > 0, "Expected at least one trade in fallback mode"
        engine.dispose()

    @catalog_exists
    def test_kronos_signal_count_is_zero_without_actor(self) -> None:
        """Without actor, strategy never receives Kronos signals."""
        engine, strategy = self._run()
        assert strategy._kronos_signal_count == 0
        engine.dispose()

    @catalog_exists
    def test_tight_stop_loss_triggers_many_exits(self) -> None:
        """With a very tight stop-loss, positions get closed frequently."""
        engine, strategy = self._run(
            stop_loss_pct=0.0001,
            take_profit_pct=0.50,
            fallback_warmup_bars=5,
        )
        fills = engine.trader.generate_order_fills_report()
        assert len(fills) > 1, "Expected multiple fills with tight stop-loss"
        engine.dispose()

    @catalog_exists
    def test_ema_indicators_initialized_with_correct_periods(self) -> None:
        """EMA indicators are initialized with configured periods."""
        engine, strategy = self._run(
            fallback_ema_fast_period=10,
            fallback_ema_slow_period=30,
        )
        assert strategy._ema_fast.initialized
        assert strategy._ema_slow.initialized
        assert strategy._ema_fast.period == 10
        assert strategy._ema_slow.period == 30
        engine.dispose()

    @catalog_exists
    def test_no_fallback_when_disabled(self) -> None:
        """With use_fallback_ema=False and no actor, no trades are placed."""
        engine, strategy = self._run(
            use_fallback_ema=False,
        )
        fills = engine.trader.generate_order_fills_report()
        assert len(fills) == 0, "Expected no trades with fallback disabled and no actor"
        engine.dispose()
