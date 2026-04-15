"""Tests for competition/validate_strategy.py.

Creates temporary strategy files with known pass/fail properties and
runs the validator against them.
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

PROJECT_ROOT = str(Path(__file__).resolve().parents[1])
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

sys.path.insert(0, str(Path(PROJECT_ROOT) / "competition"))

from validate_strategy import (
    check_no_hardcoded_api_calls,
    check_no_pandas_backtest,
    validate,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_strategy(tmp_path: Path, code: str) -> Path:
    p = tmp_path / "strategy.py"
    p.write_text(textwrap.dedent(code))
    return p


# ---------------------------------------------------------------------------
# Static checks (no import needed)
# ---------------------------------------------------------------------------

class TestStaticChecks:
    def test_no_hardcoded_api_calls_passes(self, tmp_path):
        p = _write_strategy(tmp_path, """
            from nautilus_trader.trading.strategy import Strategy
            class MyStrategy(Strategy):
                pass
        """)
        assert check_no_hardcoded_api_calls(p) is True

    def test_requests_import_fails(self, tmp_path):
        p = _write_strategy(tmp_path, """
            import requests
            def run():
                requests.get("https://api.binance.com")
        """)
        assert check_no_hardcoded_api_calls(p) is False

    def test_urllib_request_fails(self, tmp_path):
        p = _write_strategy(tmp_path, """
            import urllib.request
        """)
        assert check_no_hardcoded_api_calls(p) is False

    def test_no_run_backtest_passes(self, tmp_path):
        p = _write_strategy(tmp_path, """
            class MyStrategy:
                def on_bar(self): pass
        """)
        assert check_no_pandas_backtest(p) is True

    def test_run_backtest_function_fails(self, tmp_path):
        p = _write_strategy(tmp_path, """
            def run_backtest(start, end, initial_capital):
                return {}
        """)
        assert check_no_pandas_backtest(p) is False


# ---------------------------------------------------------------------------
# Import + class structure checks
# ---------------------------------------------------------------------------

VALID_STRATEGY = """
from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.trading.strategy import Strategy
from decimal import Decimal

class MyConfig(StrategyConfig, frozen=True):
    instrument_id: InstrumentId
    bar_type: BarType
    trade_size: Decimal

class MyStrategy(Strategy):
    def __init__(self, config: MyConfig) -> None:
        super().__init__(config)

    def on_start(self) -> None:
        self.subscribe_bars(self.config.bar_type)

    def on_bar(self, bar: Bar) -> None:
        pass

    def on_stop(self) -> None:
        self.cancel_all_orders(self.config.instrument_id)
        self.close_all_positions(self.config.instrument_id)
"""


class TestClassStructureChecks:
    def test_valid_strategy_passes_all(self, tmp_path, capsys):
        p = _write_strategy(tmp_path, VALID_STRATEGY)
        result = validate(p)
        assert result is True

    def test_missing_on_stop_fails(self, tmp_path, capsys):
        code = """
from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.trading.strategy import Strategy
from decimal import Decimal

class MyConfig(StrategyConfig, frozen=True):
    instrument_id: InstrumentId
    bar_type: BarType
    trade_size: Decimal

class MyStrategy(Strategy):
    def __init__(self, config):
        super().__init__(config)

    def on_start(self):
        pass

    def on_bar(self, bar):
        pass
"""
        p = _write_strategy(tmp_path, code)
        result = validate(p)
        assert result is False

    def test_on_stop_without_cleanup_fails(self, tmp_path):
        code = """
from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.trading.strategy import Strategy
from decimal import Decimal

class MyConfig(StrategyConfig, frozen=True):
    instrument_id: InstrumentId
    bar_type: BarType
    trade_size: Decimal

class MyStrategy(Strategy):
    def __init__(self, config):
        super().__init__(config)

    def on_start(self):
        pass

    def on_bar(self, bar):
        pass

    def on_stop(self):
        pass  # intentionally empty — no cleanup
"""
        p = _write_strategy(tmp_path, code)
        result = validate(p)
        assert result is False

    def test_run_backtest_in_valid_strategy_fails(self, tmp_path):
        """Even a valid NT strategy fails if it also has run_backtest()."""
        code = VALID_STRATEGY + "\ndef run_backtest(start, end, capital):\n    return {}\n"
        p = _write_strategy(tmp_path, code)
        result = validate(p)
        assert result is False

    def test_reference_grid_bot_passes(self, tmp_path, capsys):
        """The reference GridBotStrategy should pass all checks."""
        grid_bot_path = Path(PROJECT_ROOT) / "strategies" / "crypto" / "grid_bot.py"
        if not grid_bot_path.exists():
            pytest.skip("grid_bot.py not found")
        result = validate(grid_bot_path)
        assert result is True
