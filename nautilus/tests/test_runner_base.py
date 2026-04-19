"""Tests for the BacktestRunner abstract base."""

from __future__ import annotations

import pytest


def test_backtest_runner_is_abstract():
    from nautilus_trading.backtest.runner_base import BacktestRunner

    with pytest.raises(TypeError):
        BacktestRunner()  # type: ignore[abstract]


def test_backtest_runner_has_required_methods():
    from nautilus_trading.backtest.runner_base import BacktestRunner

    for name in ("build_config", "add_data", "run", "print_results"):
        assert hasattr(BacktestRunner, name), f"missing method: {name}"


def test_concrete_subclass_runs():
    from nautilus_trading.backtest.runner_base import BacktestRunner

    class _StubRunner(BacktestRunner):
        def build_config(self):
            return {"built": True}

        def add_data(self, engine, config):
            engine.setdefault("data", []).append(config)

        def run(self, engine):
            return {"ok": True, "engine": engine}

        def print_results(self, results):
            return str(results)

    r = _StubRunner()
    cfg = r.build_config()
    engine = {}
    r.add_data(engine, cfg)
    assert r.run(engine) == {"ok": True, "engine": {"data": [{"built": True}]}}
