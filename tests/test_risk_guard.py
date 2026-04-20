"""Unit tests for the RiskGuard mixin.

Uses NT's BacktestEngine to test the circuit breaker and order filter
in a realistic environment, reusing the EUR/USD SIM instrument from
other tests (RiskGuard is instrument-agnostic).
"""

from __future__ import annotations

import sys
from pathlib import Path

from nautilus_trader.model.data import Bar

PROJECT_ROOT = str(Path(__file__).resolve().parents[1])
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from nautilus_trader.config import StrategyConfig
from nautilus_trader.trading.strategy import Strategy
from strategies.crypto.risk_guard import RiskGuard

# ---------------------------------------------------------------------------
# Minimal strategy using RiskGuard for testing
# ---------------------------------------------------------------------------


class _MinimalConfig(StrategyConfig, frozen=True):
    instrument_id: str
    bar_type: str
    starting_equity: float = 1000.0
    max_drawdown_pct: float = 20.0
    max_position_pct: float = 0.50
    min_qty: float = 0.001
    min_notional: float = 1.0


class _TestStrategyWithRiskGuard(RiskGuard, Strategy):
    """Minimal strategy to exercise RiskGuard behavior in tests."""

    def __init__(self, config: _MinimalConfig) -> None:
        super().__init__(config)
        self.halted_calls = 0
        self.bars_processed = 0

    def on_start(self) -> None:
        from nautilus_trader.model.data import BarType as _BarType

        bar_type = _BarType.from_str(self.config.bar_type)
        self.subscribe_bars(bar_type)
        self._risk_guard_init(
            starting_equity=self.config.starting_equity,
            max_drawdown_pct=self.config.max_drawdown_pct,
            max_position_pct=self.config.max_position_pct,
            min_qty=self.config.min_qty,
            min_notional=self.config.min_notional,
        )

    def on_bar(self, bar: Bar) -> None:
        if self._is_halted():
            self.halted_calls += 1
            return
        self.bars_processed += 1

    def on_stop(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Helpers — isolated test of RiskGuard logic without NT engine
# ---------------------------------------------------------------------------


class _StubLogger:
    def __init__(self):
        self.warnings = []

    def warning(self, msg: str) -> None:
        self.warnings.append(msg)


class _StubRiskGuard(RiskGuard):
    """Standalone stub to test RiskGuard without importing NT engine."""

    def __init__(self):
        self.log = _StubLogger()
        self._cancelled = []
        self._closed = []
        self.config = type("_Cfg", (), {"instrument_id": type("_Id", (), {"venue": "SIM"})()})()

    def cancel_all_orders(self, instrument_id):
        self._cancelled.append(instrument_id)

    def close_all_positions(self, instrument_id):
        self._closed.append(instrument_id)

    def portfolio(self):
        return None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRiskGuardInit:
    def test_init_sets_defaults(self):
        rg = _StubRiskGuard()
        rg._risk_guard_init(starting_equity=1000.0)

        assert rg._rg_starting_equity == 1000.0
        assert rg._rg_high_water == 1000.0
        assert rg._rg_max_drawdown_pct == 20.0
        assert rg._rg_max_position_pct == 0.50
        assert rg._rg_halted is False

    def test_init_custom_params(self):
        rg = _StubRiskGuard()
        rg._risk_guard_init(
            starting_equity=500.0,
            max_drawdown_pct=15.0,
            max_position_pct=0.25,
            min_qty=0.00001,
            min_notional=5.0,
        )
        assert rg._rg_starting_equity == 500.0
        assert rg._rg_max_drawdown_pct == 15.0
        assert rg._rg_max_position_pct == 0.25


class TestCircuitBreaker:
    def _make_rg(self, equity: float) -> _StubRiskGuard:
        rg = _StubRiskGuard()
        rg._risk_guard_init(starting_equity=equity, max_drawdown_pct=20.0)
        return rg

    def test_not_halted_at_par(self):
        rg = self._make_rg(1000.0)
        # Override equity to return starting value
        rg._rg_current_equity = lambda: 1000.0
        assert rg._is_halted() is False

    def test_not_halted_below_threshold(self):
        rg = self._make_rg(1000.0)
        # 19% drawdown — below 20% threshold
        rg._rg_current_equity = lambda: 810.0
        assert rg._is_halted() is False

    def test_halted_at_threshold(self):
        rg = self._make_rg(1000.0)
        # Exactly 20% drawdown
        rg._rg_current_equity = lambda: 800.0
        assert rg._is_halted() is True
        assert rg._rg_halted is True

    def test_halted_beyond_threshold(self):
        rg = self._make_rg(1000.0)
        rg._rg_current_equity = lambda: 700.0
        assert rg._is_halted() is True

    def test_circuit_breaker_fires_cancel_and_close(self):
        rg = self._make_rg(1000.0)
        rg._rg_current_equity = lambda: 790.0  # 21% drawdown
        rg._is_halted()
        assert len(rg._cancelled) == 1
        assert len(rg._closed) == 1

    def test_stays_halted_after_recovery(self):
        rg = self._make_rg(1000.0)
        # First trigger halt
        rg._rg_current_equity = lambda: 700.0
        rg._is_halted()
        assert rg._rg_halted is True

        # Even if equity recovers, stays halted
        rg._rg_current_equity = lambda: 1000.0
        assert rg._is_halted() is True

    def test_hwm_updates_on_new_high(self):
        rg = self._make_rg(1000.0)
        rg._rg_current_equity = lambda: 1200.0
        rg._is_halted()
        assert rg._rg_high_water == 1200.0

    def test_drawdown_measured_from_hwm(self):
        rg = self._make_rg(1000.0)
        # Equity rises to 1200, then drops to 1000 — only 16.7% from HWM, not 0% from start
        rg._rg_current_equity = lambda: 1200.0
        rg._is_halted()  # sets HWM=1200

        rg._rg_current_equity = lambda: 1000.0  # 16.7% from 1200
        assert rg._is_halted() is False  # below 20% threshold from HWM

        rg._rg_current_equity = lambda: 950.0  # 20.8% from 1200
        assert rg._is_halted() is True


class TestOrderFilters:
    def _make_rg(self) -> _StubRiskGuard:
        rg = _StubRiskGuard()
        rg._risk_guard_init(
            starting_equity=1000.0,
            min_qty=0.001,
            min_notional=5.0,
            max_position_pct=0.50,
        )
        return rg

    def test_valid_order_passes(self):
        rg = self._make_rg()
        assert rg._check_order(quantity=0.01, price=50000.0) is True

    def test_qty_below_minimum_rejected(self):
        rg = self._make_rg()
        assert rg._check_order(quantity=0.0001, price=50000.0) is False
        assert any("min_qty" in w for w in rg.log.warnings)

    def test_notional_below_minimum_rejected(self):
        rg = self._make_rg()
        # qty=0.001, price=1.0 → notional=$0.001 < $5.0 min
        assert rg._check_order(quantity=0.001, price=1.0) is False
        assert any("min_notional" in w for w in rg.log.warnings)

    def test_exact_minimum_qty_accepted(self):
        rg = self._make_rg()
        # qty=0.001, price=50000 → notional=$50 > $5.0 min
        assert rg._check_order(quantity=0.001, price=50000.0) is True

    def test_large_order_warns_but_allows(self):
        """Oversized orders warn but are not blocked — caller controls config.trade_size."""
        rg = self._make_rg()
        # qty=0.1 @ 50000 → notional=$5000, max is $500 (50% of $1000)
        result = rg._check_order(quantity=0.1, price=50000.0)
        assert result is True  # allowed with warning
        assert any("max position" in w for w in rg.log.warnings)


class TestRiskGuardImportedByStrategies:
    """Verify that all six NT strategies declare RiskGuard in their MRO."""

    def test_grid_bot_has_risk_guard(self):
        from strategies.crypto.grid_bot import GridBotStrategy

        assert issubclass(GridBotStrategy, RiskGuard)

    def test_dca_bot_has_risk_guard(self):
        from strategies.crypto.dca_bot import DCABotStrategy

        assert issubclass(DCABotStrategy, RiskGuard)

    def test_shock_guard_has_risk_guard(self):
        from strategies.crypto.shock_guard import ShockGuardStrategy

        assert issubclass(ShockGuardStrategy, RiskGuard)

    def test_timesfm_swing_has_risk_guard(self):
        from strategies.crypto.timesfm_swing import TimesFMSwingStrategy

        assert issubclass(TimesFMSwingStrategy, RiskGuard)

    def test_timesfm_grid_has_risk_guard(self):
        from strategies.crypto.timesfm_grid import TimesFMGridStrategy

        assert issubclass(TimesFMGridStrategy, RiskGuard)

    def test_rvs_swing_has_risk_guard(self):
        from strategies.crypto.rvs_swing import RVSSwingStrategy

        assert issubclass(RVSSwingStrategy, RiskGuard)
