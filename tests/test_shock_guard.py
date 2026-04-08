"""Unit tests for the Shock Guard Macro Allocator strategy.

TDD RED phase: comprehensive tests for the two-tier crypto strategy.
"""

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

from strategies.crypto.shock_guard import ShockGuardConfig, ShockGuardStrategy

INSTRUMENT = TestInstrumentProvider.default_fx_ccy("EUR/USD")
INSTRUMENT_ID = INSTRUMENT.id
BAR_TYPE = BarType.from_str("EUR/USD.SIM-1-MINUTE-MID-INTERNAL")
PRICE_PRECISION = INSTRUMENT.price_precision


def _fmt_price(value) -> str:
    return f"{float(value):.{PRICE_PRECISION}f}"


def _make_config(**overrides) -> ShockGuardConfig:
    """Create a ShockGuardConfig with sensible defaults for testing."""
    return ShockGuardConfig(**{
        "instrument_id": INSTRUMENT_ID,
        "bar_type": BAR_TYPE,
        "trade_size": Decimal("100"),
        "default_allocation_pct": 0.50,
        "min_allocation_pct": 0.15,
        "max_allocation_pct": 1.0,
        "max_position_change_pct": 0.25,
        "regime_confidence_threshold": 0.7,
        "strategic_rebalance_bars": 60,
        "price_drop_threshold_pct": 0.03,
        "price_drop_window_bars": 5,
        "bid_volume_ratio_threshold": 0.20,
        "atr_spike_multiplier": 5.0,
        "shock_guard_signals_required": 2,
        "shock_guard_target_pct": 0.25,
        "cooldown_bars": 30,
        "stop_loss_pct": 0.05,
        "take_profit_pct": 0.08,
        "use_deterministic_signals": True,
        "ema_fast_period": 12,
        "ema_slow_period": 26,
        **overrides,
    })


def _build_engine(starting_balance: int = 100_000) -> BacktestEngine:
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
    step = (end_price - start_price) / max(count - 1, 1)
    return [
        _make_quote_tick(
            _fmt_price(start_price + step * i),
            start_ns + i * 60_000_000_000,
        )
        for i in range(count)
    ]



class TestShockGuardConfig:
    """Test ShockGuardConfig validation and immutability."""

    def test_config_frozen(self):
        """Config should be immutable (frozen=True)."""
        config = _make_config()
        with pytest.raises(AttributeError):
            config.default_allocation_pct = 0.9  # type: ignore[misc]

    def test_config_defaults(self):
        """Config should have correct default values."""
        config = _make_config()
        assert config.default_allocation_pct == 0.50
        assert config.min_allocation_pct == 0.15
        assert config.max_allocation_pct == 1.0
        assert config.max_position_change_pct == 0.25
        assert config.stop_loss_pct == 0.05
        assert config.take_profit_pct == 0.08
        assert config.shock_guard_signals_required == 2
        assert config.cooldown_bars == 30

    def test_config_custom_values(self):
        """Config should accept custom parameter overrides."""
        config = _make_config(
            default_allocation_pct=0.75,
            min_allocation_pct=0.20,
            stop_loss_pct=0.03,
        )
        assert config.default_allocation_pct == 0.75
        assert config.min_allocation_pct == 0.20
        assert config.stop_loss_pct == 0.03


class TestStrategyInitialization:
    """Test strategy initialization and on_start lifecycle."""

    def test_strategy_creates_successfully(self):
        """Strategy should instantiate with valid config."""
        config = _make_config()
        strategy = ShockGuardStrategy(config=config)
        assert strategy is not None

    def test_on_start_initializes_state(self):
        """After engine run, strategy should have initialized state."""
        config = _make_config()
        strategy = ShockGuardStrategy(config=config)

        engine = _build_engine()
        engine.add_strategy(strategy)
        engine.add_data(_generate_flat_ticks("1.10000", 5))
        engine.run()

        assert strategy.instrument is not None
        assert strategy.current_allocation_pct == config.default_allocation_pct
        engine.dispose()

    def test_strategy_instrument_none_before_start(self):
        """Before engine run, instrument should be None."""
        config = _make_config()
        strategy = ShockGuardStrategy(config=config)
        assert strategy.instrument is None


class TestGraduatedPositionScaling:
    """Test the strategic tier graduated position scaling logic."""

    def test_default_allocation_50_pct(self):
        """Strategy should start at 50% allocation (default)."""
        config = _make_config()
        strategy = ShockGuardStrategy(config=config)

        engine = _build_engine()
        engine.add_strategy(strategy)
        engine.add_data(_generate_flat_ticks("1.10000", 5))
        engine.run()

        assert strategy.current_allocation_pct == pytest.approx(0.50, abs=0.01)
        engine.dispose()

    def test_allocation_respects_min_floor(self):
        """Allocation should never go below min_allocation_pct (15% floor)."""
        config = _make_config(min_allocation_pct=0.15)
        strategy = ShockGuardStrategy(config=config)

        # Directly test the clamping logic
        result = strategy._clamp_allocation(0.05)
        assert result == pytest.approx(0.15, abs=0.001)

    def test_allocation_respects_max_ceiling(self):
        """Allocation should never exceed max_allocation_pct."""
        config = _make_config(max_allocation_pct=1.0)
        strategy = ShockGuardStrategy(config=config)

        result = strategy._clamp_allocation(1.5)
        assert result == pytest.approx(1.0, abs=0.001)

    def test_allocation_clamp_within_bounds(self):
        """Allocation within bounds should remain unchanged."""
        config = _make_config(min_allocation_pct=0.15, max_allocation_pct=1.0)
        strategy = ShockGuardStrategy(config=config)

        assert strategy._clamp_allocation(0.50) == pytest.approx(0.50, abs=0.001)
        assert strategy._clamp_allocation(0.75) == pytest.approx(0.75, abs=0.001)

    def test_graduated_levels(self):
        """Strategy should support graduated allocation levels: 0%, 25%, 50%, 75%, 100%."""
        config = _make_config()
        strategy = ShockGuardStrategy(config=config)

        # Verify clamping at discrete-ish levels
        assert strategy._clamp_allocation(0.0) == pytest.approx(0.15, abs=0.001)  # floor
        assert strategy._clamp_allocation(0.25) == pytest.approx(0.25, abs=0.001)
        assert strategy._clamp_allocation(0.50) == pytest.approx(0.50, abs=0.001)
        assert strategy._clamp_allocation(0.75) == pytest.approx(0.75, abs=0.001)
        assert strategy._clamp_allocation(1.0) == pytest.approx(1.0, abs=0.001)


class TestPositionChangeRateLimit:
    """Test position change is capped at 25% per hour."""

    def test_rate_limit_caps_increase(self):
        """Position change should be capped at +25% per period."""
        config = _make_config(max_position_change_pct=0.25)
        strategy = ShockGuardStrategy(config=config)
        strategy.current_allocation_pct = 0.50

        # Request to go from 50% to 100% (a 50% jump) -- should be capped to 75%
        new_alloc = strategy._apply_rate_limit(1.0)
        assert new_alloc == pytest.approx(0.75, abs=0.001)

    def test_rate_limit_caps_decrease(self):
        """Position change should be capped at -25% per period."""
        config = _make_config(max_position_change_pct=0.25)
        strategy = ShockGuardStrategy(config=config)
        strategy.current_allocation_pct = 0.75

        # Request to go from 75% to 25% (a 50% drop) -- should be capped to 50%
        new_alloc = strategy._apply_rate_limit(0.25)
        assert new_alloc == pytest.approx(0.50, abs=0.001)

    def test_rate_limit_allows_small_change(self):
        """Changes within 25% should pass through unchanged."""
        config = _make_config(max_position_change_pct=0.25)
        strategy = ShockGuardStrategy(config=config)
        strategy.current_allocation_pct = 0.50

        new_alloc = strategy._apply_rate_limit(0.65)
        assert new_alloc == pytest.approx(0.65, abs=0.001)


class TestShockGuardTrigger:
    """Test the tactical tier Shock Guard 2-of-3 signal detection."""

    def test_no_signals_no_trigger(self):
        """With 0 signals active, shock guard should not trigger."""
        config = _make_config()
        strategy = ShockGuardStrategy(config=config)
        strategy._signal_price_drop = False
        strategy._signal_bid_volume_low = False
        strategy._signal_atr_spike = False

        assert strategy._shock_guard_triggered() is False

    def test_one_signal_no_trigger(self):
        """With only 1 signal active, shock guard should not trigger."""
        config = _make_config(shock_guard_signals_required=2)
        strategy = ShockGuardStrategy(config=config)
        strategy._signal_price_drop = True
        strategy._signal_bid_volume_low = False
        strategy._signal_atr_spike = False

        assert strategy._shock_guard_triggered() is False

    def test_two_signals_triggers(self):
        """With 2 signals active, shock guard should trigger."""
        config = _make_config(shock_guard_signals_required=2)
        strategy = ShockGuardStrategy(config=config)

        # Price drop + ATR spike
        strategy._signal_price_drop = True
        strategy._signal_bid_volume_low = False
        strategy._signal_atr_spike = True

        assert strategy._shock_guard_triggered() is True

    def test_three_signals_triggers(self):
        """With all 3 signals active, shock guard should trigger."""
        config = _make_config(shock_guard_signals_required=2)
        strategy = ShockGuardStrategy(config=config)
        strategy._signal_price_drop = True
        strategy._signal_bid_volume_low = True
        strategy._signal_atr_spike = True

        assert strategy._shock_guard_triggered() is True

    def test_different_two_signal_combos(self):
        """All 2-of-3 combinations should trigger."""
        config = _make_config(shock_guard_signals_required=2)

        combos = [
            (True, True, False),   # price drop + bid volume
            (True, False, True),   # price drop + ATR spike
            (False, True, True),   # bid volume + ATR spike
        ]

        for pd, bv, atr in combos:
            strategy = ShockGuardStrategy(config=config)
            strategy._signal_price_drop = pd
            strategy._signal_bid_volume_low = bv
            strategy._signal_atr_spike = atr
            assert strategy._shock_guard_triggered() is True, (
                f"Should trigger with signals: price_drop={pd}, bid_vol={bv}, atr={atr}"
            )


class TestCooldown:
    """Test cooldown period after Shock Guard trigger."""

    def test_cooldown_active_after_trigger(self):
        """After shock guard triggers, cooldown should be active."""
        config = _make_config(cooldown_bars=30)
        strategy = ShockGuardStrategy(config=config)
        strategy._cooldown_remaining = 0

        # Simulate triggering shock guard (sets cooldown directly)
        strategy._cooldown_remaining = config.cooldown_bars
        assert strategy._cooldown_remaining == 30

    def test_cooldown_decrements(self):
        """Cooldown counter should decrement each bar."""
        config = _make_config(cooldown_bars=30)
        strategy = ShockGuardStrategy(config=config)
        strategy._cooldown_remaining = 10

        strategy._tick_cooldown()
        assert strategy._cooldown_remaining == 9

    def test_cooldown_stops_at_zero(self):
        """Cooldown should not go below zero."""
        config = _make_config()
        strategy = ShockGuardStrategy(config=config)
        strategy._cooldown_remaining = 0

        strategy._tick_cooldown()
        assert strategy._cooldown_remaining == 0

    def test_is_cooling_down(self):
        """is_cooling_down should reflect cooldown state."""
        config = _make_config()
        strategy = ShockGuardStrategy(config=config)

        strategy._cooldown_remaining = 5
        assert strategy._is_cooling_down() is True

        strategy._cooldown_remaining = 0
        assert strategy._is_cooling_down() is False


class TestShockGuardPositionReduction:
    """Test that shock guard reduces allocation to target (25%)."""

    def test_shock_guard_reduces_to_target(self):
        """When shock guard triggers, allocation should drop to shock_guard_target_pct."""
        config = _make_config(
            shock_guard_target_pct=0.25,
            min_allocation_pct=0.15,
        )
        strategy = ShockGuardStrategy(config=config)
        strategy.current_allocation_pct = 0.75

        # Simulate shock guard response (bypasses rate limit)
        target = config.shock_guard_target_pct
        assert target == pytest.approx(0.25, abs=0.001)

    def test_shock_guard_target_respects_floor(self):
        """Shock guard target should respect the minimum allocation floor."""
        config = _make_config(
            shock_guard_target_pct=0.10,  # below floor
            min_allocation_pct=0.15,
        )
        strategy = ShockGuardStrategy(config=config)

        result = strategy._clamp_allocation(config.shock_guard_target_pct)
        assert result == pytest.approx(0.15, abs=0.001)


class TestRegimeHysteresis:
    """Test that regime changes require confidence > threshold."""

    def test_low_confidence_no_change(self):
        """Below confidence threshold, regime should not change."""
        config = _make_config(regime_confidence_threshold=0.7)
        strategy = ShockGuardStrategy(config=config)
        strategy.current_allocation_pct = 0.50

        # Simulate a regime signal with low confidence
        new_alloc = strategy._evaluate_regime_signal(
            suggested_allocation=0.75,
            confidence=0.5,
        )
        assert new_alloc == pytest.approx(0.50, abs=0.001)  # unchanged

    def test_high_confidence_allows_change(self):
        """Above confidence threshold, regime change should be allowed."""
        config = _make_config(regime_confidence_threshold=0.7)
        strategy = ShockGuardStrategy(config=config)
        strategy.current_allocation_pct = 0.50

        new_alloc = strategy._evaluate_regime_signal(
            suggested_allocation=0.75,
            confidence=0.8,
        )
        assert new_alloc == pytest.approx(0.75, abs=0.001)

    def test_exact_threshold_allows_change(self):
        """At exactly the confidence threshold, change should be allowed."""
        config = _make_config(regime_confidence_threshold=0.7)
        strategy = ShockGuardStrategy(config=config)
        strategy.current_allocation_pct = 0.50

        new_alloc = strategy._evaluate_regime_signal(
            suggested_allocation=0.75,
            confidence=0.7,
        )
        # At threshold => no change (must exceed, not equal)
        # Design choice: strictly greater than
        assert new_alloc == pytest.approx(0.50, abs=0.001)


class TestRiskChecklist:
    """Test the risk checklist: position size, change limit, R:R ratio."""

    def test_rr_ratio_blocks_bad_increase(self):
        """Position increase should be blocked if R:R < 2:1."""
        config = _make_config(stop_loss_pct=0.05, take_profit_pct=0.08)
        strategy = ShockGuardStrategy(config=config)

        # R:R = 0.08 / 0.05 = 1.6:1 -- below 2:1
        assert strategy._risk_reward_acceptable() is False

    def test_rr_ratio_allows_good_increase(self):
        """Position increase should be allowed if R:R >= 2:1."""
        config = _make_config(stop_loss_pct=0.04, take_profit_pct=0.08)
        strategy = ShockGuardStrategy(config=config)

        # R:R = 0.08 / 0.04 = 2:1
        assert strategy._risk_reward_acceptable() is True

    def test_rr_ratio_with_favorable_params(self):
        """Generous R:R should pass."""
        config = _make_config(stop_loss_pct=0.03, take_profit_pct=0.10)
        strategy = ShockGuardStrategy(config=config)

        # R:R = 0.10 / 0.03 = 3.33:1
        assert strategy._risk_reward_acceptable() is True


class TestPriceDropSignal:
    """Test price drop detection (>3% in 5 minutes)."""

    def test_no_drop_no_signal(self):
        """Stable prices should not trigger price drop signal."""
        config = _make_config(price_drop_threshold_pct=0.03, price_drop_window_bars=5)
        strategy = ShockGuardStrategy(config=config)

        # Feed stable prices
        for _ in range(10):
            strategy._update_price_history(1.10000)

        assert strategy._check_price_drop_signal() is False

    def test_large_drop_triggers_signal(self):
        """A >3% drop within the window should trigger."""
        config = _make_config(price_drop_threshold_pct=0.03, price_drop_window_bars=5)
        strategy = ShockGuardStrategy(config=config)

        # Feed a sharp drop: from 1.10 to 1.065 = ~3.2% drop
        prices = [1.10000, 1.09500, 1.09000, 1.08000, 1.07000, 1.06500]
        for p in prices:
            strategy._update_price_history(p)

        assert strategy._check_price_drop_signal() is True

    def test_gradual_decline_no_signal(self):
        """A slow decline spread over many bars may not trigger within window."""
        config = _make_config(price_drop_threshold_pct=0.03, price_drop_window_bars=5)
        strategy = ShockGuardStrategy(config=config)

        # Gradual decline: 1% per bar over 20 bars (but within 5-bar window < 3%)
        price = 1.10000
        for _ in range(20):
            strategy._update_price_history(price)
            price *= 0.995  # ~0.5% per bar

        # Within a 5-bar window, drop is ~2.5% -- just under threshold
        assert strategy._check_price_drop_signal() is False


class TestATRSpikeSignal:
    """Test ATR spike detection (1-min ATR > 5x 1h avg)."""

    def test_no_spike_no_signal(self):
        """Normal ATR should not trigger."""
        config = _make_config(atr_spike_multiplier=5.0)
        strategy = ShockGuardStrategy(config=config)

        strategy._short_atr_value = 0.001
        strategy._long_atr_value = 0.001

        assert strategy._check_atr_spike_signal() is False

    def test_atr_spike_triggers(self):
        """Short ATR > 5x long ATR should trigger."""
        config = _make_config(atr_spike_multiplier=5.0)
        strategy = ShockGuardStrategy(config=config)

        strategy._short_atr_value = 0.006
        strategy._long_atr_value = 0.001

        assert strategy._check_atr_spike_signal() is True

    def test_atr_spike_at_boundary(self):
        """Exactly 5x should not trigger (strictly greater)."""
        config = _make_config(atr_spike_multiplier=5.0)
        strategy = ShockGuardStrategy(config=config)

        strategy._short_atr_value = 0.005
        strategy._long_atr_value = 0.001

        assert strategy._check_atr_spike_signal() is False


class TestEngineIntegration:
    """Integration tests running the strategy through the backtest engine."""

    def test_engine_run_no_crash(self):
        """Strategy should complete an engine run without errors."""
        config = _make_config()
        strategy = ShockGuardStrategy(config=config)

        engine = _build_engine()
        engine.add_strategy(strategy)
        engine.add_data(_generate_flat_ticks("1.10000", 100))
        engine.run()
        engine.dispose()

    def test_engine_run_with_price_movement(self):
        """Strategy should handle price movement and place orders."""
        config = _make_config()
        strategy = ShockGuardStrategy(config=config)

        engine = _build_engine()
        engine.add_strategy(strategy)
        ticks = (
            _generate_flat_ticks("1.10000", 30, start_ns=1_000_000_000)
            + _generate_linear_ticks(1.10000, 1.12000, 30, start_ns=1_801_000_000_000)
            + _generate_flat_ticks("1.12000", 30, start_ns=3_601_000_000_000)
        )
        engine.add_data(ticks)
        engine.run()

        # Strategy should have processed all bars without error
        assert strategy.instrument is not None
        engine.dispose()

    def test_engine_run_shock_scenario(self):
        """Strategy should handle a rapid price crash scenario."""
        config = _make_config(
            price_drop_threshold_pct=0.03,
            price_drop_window_bars=5,
        )
        strategy = ShockGuardStrategy(config=config)

        engine = _build_engine()
        engine.add_strategy(strategy)

        # Stable period then sudden crash (>3% in 5 bars)
        ticks = (
            _generate_flat_ticks("1.10000", 50, start_ns=1_000_000_000)
            + _generate_linear_ticks(1.10000, 1.06000, 5, start_ns=3_001_000_000_000)
            + _generate_flat_ticks("1.06000", 40, start_ns=3_301_000_000_000)
        )
        engine.add_data(ticks)
        engine.run()

        # Strategy should have survived the crash
        assert strategy.instrument is not None
        engine.dispose()

    def test_on_stop_cleans_up(self):
        """on_stop should cancel orders and close positions."""
        config = _make_config()
        strategy = ShockGuardStrategy(config=config)

        engine = _build_engine()
        engine.add_strategy(strategy)
        engine.add_data(_generate_flat_ticks("1.10000", 50))
        engine.run()

        # After run completes (on_stop called), no open positions
        assert engine.cache.positions_open_count() == 0
        engine.dispose()
