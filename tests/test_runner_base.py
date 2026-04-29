"""Tests for the BacktestRunner abstract base."""

from __future__ import annotations

import pytest


def test_backtest_runner_is_abstract():
    from nautilus_trading.backtest.runner_base import BacktestRunner

    with pytest.raises(TypeError):
        BacktestRunner()  # type: ignore[abstract]


def test_backtest_runner_has_required_methods():
    from nautilus_trading.backtest.runner_base import BacktestRunner

    for name in ("build_config", "add_data", "run", "print_results", "main"):
        assert hasattr(BacktestRunner, name), f"missing method: {name}"


def test_concrete_subclass_runs():
    from nautilus_trading.backtest.runner_base import BacktestRunner

    class _StubRunner(BacktestRunner):
        def __init__(self):
            self._cfg = {"built": True}

        def build_config(self):
            return self._cfg

        def add_data(self, engine):
            # Subclasses read whatever they need from ``self`` rather
            # than receiving the engine config as a parameter.
            engine.setdefault("data", []).append(self._cfg)

        def run(self, engine):
            return {"ok": True, "engine": engine}

        def print_results(self, results):
            return str(results)

        def main(self):
            pass

    r = _StubRunner()
    r.build_config()
    engine: dict = {}
    r.add_data(engine)
    assert r.run(engine) == {"ok": True, "engine": {"data": [{"built": True}]}}


def test_main_is_abstract():
    """main() is abstract — concrete subclasses must provide their own implementation."""
    from nautilus_trading.backtest.runner_base import BacktestRunner

    class _PartialRunner(BacktestRunner):
        def build_config(self):
            return {}

        def add_data(self, engine):
            pass

        def run(self, engine):
            return None

        def print_results(self, results):
            pass

        # Deliberately no main() override — instantiation must fail.

    with pytest.raises(TypeError):
        _PartialRunner()  # type: ignore[abstract]
