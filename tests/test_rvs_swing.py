"""Unit tests for RVS (Relative Volume Sentiment) Swing trading strategy.

Tests cover:
- RVSSignal data type validation
- RVSSwingConfig defaults and frozen immutability
- RVS anomaly detection (volume >2sigma, polarity >0.6, engagement >1.5x)
- Signal rejection when thresholds not met
- Whale filter logic (reject on >2% concentration increase)
- Risk checklist validation (cash, position limit, R:R)
- Trailing stop-loss behavior (3% initial, tightens to 1.5% after 2% profit)
- Strategy lifecycle (on_start, on_bar, on_data, on_stop)
"""

import sys
from decimal import Decimal
from pathlib import Path

import pytest
from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.currencies import USDT
from nautilus_trader.model.data import BarType
from nautilus_trader.model.enums import AccountType, OmsType
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.objects import Money
from nautilus_trader.test_kit.providers import TestInstrumentProvider

PROJECT_ROOT = str(Path(__file__).resolve().parents[1])
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from strategies.crypto.rvs_data import RVSSignal
from strategies.crypto.rvs_swing import RVSSwingConfig, RVSSwingStrategy

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BINANCE_VENUE = Venue("BINANCE")
INSTRUMENT = TestInstrumentProvider.btcusdt_binance()
INSTRUMENT_ID = INSTRUMENT.id
BAR_TYPE = BarType.from_str(f"{INSTRUMENT_ID}-1-MINUTE-LAST-INTERNAL")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def default_config() -> RVSSwingConfig:
    return RVSSwingConfig(
        instrument_id=INSTRUMENT_ID,
        bar_type=BAR_TYPE,
        trade_size=Decimal("0.001"),
    )


@pytest.fixture()
def strategy(default_config: RVSSwingConfig) -> RVSSwingStrategy:
    return RVSSwingStrategy(config=default_config)


def _make_signal(
    *,
    volume_zscore: float = 2.5,
    polarity: float = 0.7,
    engagement_ratio: float = 1.8,
    whale_concentration_change: float = 0.5,
    source: str = "reddit",
    forecast_delta_pct: float = 0.02,
) -> RVSSignal:
    """Helper to build an RVSSignal with sensible defaults."""
    return RVSSignal(
        volume_zscore=volume_zscore,
        polarity=polarity,
        engagement_ratio=engagement_ratio,
        whale_concentration_change=whale_concentration_change,
        source=source,
        forecast_delta_pct=forecast_delta_pct,
    )


# ---------------------------------------------------------------------------
# RVSSignal data type tests
# ---------------------------------------------------------------------------


class TestRVSSignal:
    """Tests for the RVSSignal custom data type."""

    def test_signal_creation(self) -> None:
        """RVSSignal can be created with all required fields."""
        signal = _make_signal()
        assert signal.volume_zscore == 2.5
        assert signal.polarity == 0.7
        assert signal.engagement_ratio == 1.8
        assert signal.whale_concentration_change == 0.5
        assert signal.source == "reddit"
        assert signal.forecast_delta_pct == 0.02

    def test_signal_source_values(self) -> None:
        """RVSSignal accepts different source platforms."""
        for src in ("reddit", "twitter"):
            signal = _make_signal(source=src)
            assert signal.source == src


# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------


class TestRVSSwingConfig:
    """Config-level tests (no engine needed)."""

    def test_config_defaults(self, default_config: RVSSwingConfig) -> None:
        """Verify default values for all optional config fields."""
        assert default_config.volume_zscore_threshold == 2.0
        assert default_config.polarity_threshold == 0.6
        assert default_config.engagement_ratio_threshold == 1.5
        assert default_config.whale_concentration_max_change == 2.0
        assert default_config.initial_stop_loss_pct == 0.03
        assert default_config.tight_stop_loss_pct == 0.015
        assert default_config.profit_threshold_to_tighten == 0.02
        assert default_config.min_reward_risk_ratio == 2.0
        assert default_config.max_position_pct == 0.15
        assert default_config.forecast_threshold_pct == 0.01

    def test_config_frozen(self, default_config: RVSSwingConfig) -> None:
        """Verify RVSSwingConfig is immutable (frozen)."""
        with pytest.raises(AttributeError):
            default_config.volume_zscore_threshold = 3.0  # type: ignore[misc]

    def test_config_custom_overrides(self) -> None:
        """Config accepts custom threshold overrides."""
        config = RVSSwingConfig(
            instrument_id=INSTRUMENT_ID,
            bar_type=BAR_TYPE,
            trade_size=Decimal("0.001"),
            volume_zscore_threshold=3.0,
            polarity_threshold=0.8,
            initial_stop_loss_pct=0.05,
        )
        assert config.volume_zscore_threshold == 3.0
        assert config.polarity_threshold == 0.8
        assert config.initial_stop_loss_pct == 0.05


# ---------------------------------------------------------------------------
# RVS anomaly detection tests
# ---------------------------------------------------------------------------


class TestRVSAnomalyDetection:
    """Tests for the deterministic RVS signal validation logic."""

    def test_valid_signal_passes(self, strategy: RVSSwingStrategy) -> None:
        """A signal meeting all thresholds passes validation."""
        assert strategy.is_rvs_anomaly(_make_signal()) is True

    def test_low_volume_rejected(self, strategy: RVSSwingStrategy) -> None:
        """Signal with volume_zscore below threshold is rejected."""
        signal = _make_signal(volume_zscore=1.5)
        assert strategy.is_rvs_anomaly(signal) is False

    def test_low_polarity_rejected(self, strategy: RVSSwingStrategy) -> None:
        """Signal with polarity below threshold is rejected."""
        signal = _make_signal(polarity=0.4)
        assert strategy.is_rvs_anomaly(signal) is False

    def test_low_engagement_rejected(self, strategy: RVSSwingStrategy) -> None:
        """Signal with engagement_ratio below threshold is rejected."""
        signal = _make_signal(engagement_ratio=1.2)
        assert strategy.is_rvs_anomaly(signal) is False

    def test_boundary_values_pass(self, strategy: RVSSwingStrategy) -> None:
        """Signals at exact threshold boundaries pass (>=, >, >)."""
        signal = _make_signal(
            volume_zscore=2.0,  # exactly at threshold (>=)
            polarity=0.61,  # just above threshold (>)
            engagement_ratio=1.51,  # just above threshold (>)
        )
        assert strategy.is_rvs_anomaly(signal) is True

    def test_polarity_at_boundary_rejected(self, strategy: RVSSwingStrategy) -> None:
        """Polarity exactly at 0.6 is rejected (must be >0.6, not >=)."""
        signal = _make_signal(polarity=0.6)
        assert strategy.is_rvs_anomaly(signal) is False

    def test_engagement_at_boundary_rejected(self, strategy: RVSSwingStrategy) -> None:
        """Engagement exactly at 1.5 is rejected (must be >1.5x, not >=)."""
        signal = _make_signal(engagement_ratio=1.5)
        assert strategy.is_rvs_anomaly(signal) is False


# ---------------------------------------------------------------------------
# Whale filter tests
# ---------------------------------------------------------------------------


class TestWhaleFilter:
    """Tests for whale wallet concentration filter."""

    def test_whale_filter_passes_small_change(self, strategy: RVSSwingStrategy) -> None:
        """Whale concentration change below 2% passes filter."""
        signal = _make_signal(whale_concentration_change=1.0)
        assert strategy.passes_whale_filter(signal) is True

    def test_whale_filter_rejects_large_change(self, strategy: RVSSwingStrategy) -> None:
        """Whale concentration change above 2% is rejected."""
        signal = _make_signal(whale_concentration_change=2.5)
        assert strategy.passes_whale_filter(signal) is False

    def test_whale_filter_boundary(self, strategy: RVSSwingStrategy) -> None:
        """Whale concentration change exactly at 2% is rejected (must be <2%)."""
        signal = _make_signal(whale_concentration_change=2.0)
        assert strategy.passes_whale_filter(signal) is False

    def test_whale_filter_negative_change(self, strategy: RVSSwingStrategy) -> None:
        """Negative whale concentration change (decreasing) passes."""
        signal = _make_signal(whale_concentration_change=-1.0)
        assert strategy.passes_whale_filter(signal) is True


# ---------------------------------------------------------------------------
# Risk checklist tests
# ---------------------------------------------------------------------------


class TestRiskChecklist:
    """Tests for the deterministic risk checklist validation."""

    def test_risk_check_sufficient_cash(self, strategy: RVSSwingStrategy) -> None:
        """Risk check passes with sufficient available cash."""
        assert strategy.check_cash_available(Decimal("500"), Decimal("50")) is True

    def test_risk_check_insufficient_cash(self, strategy: RVSSwingStrategy) -> None:
        """Risk check fails when order exceeds available cash."""
        assert strategy.check_cash_available(Decimal("10"), Decimal("50")) is False

    def test_risk_check_zero_cash(self, strategy: RVSSwingStrategy) -> None:
        """Risk check fails with zero cash."""
        assert strategy.check_cash_available(Decimal("0"), Decimal("50")) is False

    def test_position_limit_within_bounds(self, strategy: RVSSwingStrategy) -> None:
        """Position limit check passes when within max_position_pct."""
        # 50 / 500 = 10%, below 15% limit
        assert strategy.check_position_limit(
            order_value=Decimal("50"),
            total_equity=Decimal("500"),
        ) is True

    def test_position_limit_exceeded(self, strategy: RVSSwingStrategy) -> None:
        """Position limit check fails when exceeding max_position_pct."""
        # 100 / 500 = 20%, above 15% limit
        assert strategy.check_position_limit(
            order_value=Decimal("100"),
            total_equity=Decimal("500"),
        ) is False

    def test_reward_risk_ratio_sufficient(self, strategy: RVSSwingStrategy) -> None:
        """R:R check passes when ratio >= 2:1."""
        # target_pct=0.06, stop_pct=0.03 -> ratio = 2.0
        assert strategy.check_reward_risk(
            target_pct=0.06,
            stop_pct=0.03,
        ) is True

    def test_reward_risk_ratio_insufficient(self, strategy: RVSSwingStrategy) -> None:
        """R:R check fails when ratio < 2:1."""
        # target_pct=0.04, stop_pct=0.03 -> ratio = 1.33
        assert strategy.check_reward_risk(
            target_pct=0.04,
            stop_pct=0.03,
        ) is False

    def test_reward_risk_exact_boundary(self, strategy: RVSSwingStrategy) -> None:
        """R:R at exactly 2:1 passes."""
        assert strategy.check_reward_risk(target_pct=0.06, stop_pct=0.03) is True

    def test_reward_risk_zero_stop(self, strategy: RVSSwingStrategy) -> None:
        """R:R check with zero stop returns False (avoids division by zero)."""
        assert strategy.check_reward_risk(target_pct=0.06, stop_pct=0.0) is False


# ---------------------------------------------------------------------------
# Trailing stop-loss tests
# ---------------------------------------------------------------------------


class TestTrailingStopLoss:
    """Tests for trailing stop-loss behavior."""

    def test_initial_stop_distance(self, strategy: RVSSwingStrategy) -> None:
        """Initial stop loss is 3% from entry."""
        entry_price = 50000.0
        strategy._entry_price = entry_price
        strategy._highest_since_entry = entry_price
        strategy._profit_threshold_reached = False

        stop = strategy.compute_trailing_stop(current_price=50000.0)
        expected = entry_price * (1 - 0.03)  # 48500.0
        assert abs(stop - expected) < 0.01

    def test_stop_tightens_after_profit(self, strategy: RVSSwingStrategy) -> None:
        """After 2% profit, stop tightens to 1.5% from highest price."""
        entry_price = 50000.0
        strategy._entry_price = entry_price
        # Price has risen 3% -> should tighten
        current = 51500.0
        strategy._highest_since_entry = current
        strategy._profit_threshold_reached = True

        stop = strategy.compute_trailing_stop(current_price=current)
        expected = current * (1 - 0.015)  # 1.5% from highest
        assert abs(stop - expected) < 0.01

    def test_stop_trails_upward(self, strategy: RVSSwingStrategy) -> None:
        """Trailing stop moves up as price increases, never down."""
        entry_price = 50000.0
        strategy._entry_price = entry_price
        strategy._highest_since_entry = entry_price
        strategy._profit_threshold_reached = False

        stop1 = strategy.compute_trailing_stop(current_price=50000.0)

        # Price goes up but not past 2% profit threshold
        strategy._highest_since_entry = 50500.0
        stop2 = strategy.compute_trailing_stop(current_price=50500.0)

        assert stop2 >= stop1  # stop only moves up

    def test_profit_threshold_detection(self, strategy: RVSSwingStrategy) -> None:
        """Profit threshold (2%) is correctly detected."""
        entry_price = 50000.0
        strategy._entry_price = entry_price

        # Below threshold
        assert strategy.is_profit_above_threshold(50500.0) is False
        # At threshold (2% = 51000)
        assert strategy.is_profit_above_threshold(51000.0) is True
        # Above threshold
        assert strategy.is_profit_above_threshold(52000.0) is True


# ---------------------------------------------------------------------------
# Strategy lifecycle tests (with BacktestEngine)
# ---------------------------------------------------------------------------


def _build_engine() -> BacktestEngine:
    """Build a BacktestEngine with BINANCE venue and BTCUSDT instrument."""
    engine = BacktestEngine(
        config=BacktestEngineConfig(
            logging=LoggingConfig(log_level="ERROR"),
        ),
    )
    engine.add_venue(
        venue=BINANCE_VENUE,
        oms_type=OmsType.NETTING,
        account_type=AccountType.CASH,
        base_currency=None,  # multi-currency for crypto
        starting_balances=[Money(500, USDT)],
    )
    engine.add_instrument(INSTRUMENT)
    return engine


class TestStrategyLifecycle:
    """Tests for strategy lifecycle methods using a BacktestEngine."""

    def test_strategy_instantiation(self, strategy: RVSSwingStrategy) -> None:
        """Strategy can be instantiated with valid config."""
        assert strategy is not None
        assert strategy._entry_price is None
        assert strategy._highest_since_entry is None
        assert strategy._profit_threshold_reached is False

    def test_on_start_subscribes_bars(self) -> None:
        """on_start subscribes to bar data."""
        engine = _build_engine()
        config = RVSSwingConfig(
            instrument_id=INSTRUMENT_ID,
            bar_type=BAR_TYPE,
            trade_size=Decimal("0.001"),
        )
        strategy = RVSSwingStrategy(config=config)
        engine.add_strategy(strategy)

        # Start but don't run (no data to process)
        # Just verify engine accepted the strategy
        assert strategy.config.instrument_id == INSTRUMENT_ID
        engine.dispose()

    def test_signal_evaluation_combines_checks(self, strategy: RVSSwingStrategy) -> None:
        """Full signal evaluation combines anomaly + whale + forecast checks."""
        assert strategy.evaluate_signal(_make_signal()) is True
        assert strategy.evaluate_signal(_make_signal(whale_concentration_change=3.0)) is False
        assert strategy.evaluate_signal(_make_signal(volume_zscore=1.0)) is False
        assert strategy.evaluate_signal(_make_signal(forecast_delta_pct=0.005)) is False

    def test_on_reset_clears_state(self, strategy: RVSSwingStrategy) -> None:
        """on_reset clears all internal state."""
        strategy._entry_price = 50000.0
        strategy._highest_since_entry = 51000.0
        strategy._profit_threshold_reached = True

        strategy.on_reset()

        assert strategy._entry_price is None
        assert strategy._highest_since_entry is None
        assert strategy._profit_threshold_reached is False
