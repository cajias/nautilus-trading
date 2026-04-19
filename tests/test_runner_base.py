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


def test_main_calls_lifecycle_in_order(monkeypatch):
    """main() default implementation invokes build_config, add_data, run, print_results in order."""
    from nautilus_trading.backtest.runner_base import BacktestRunner

    events = []

    class _R(BacktestRunner):
        def build_config(self):
            events.append("build")
            return {}

        def add_data(self, engine, config):
            events.append("add_data")

        def run(self, engine):
            events.append("run")
            return "results"

        def print_results(self, results):
            events.append(("print", results))

    class _StubEngine:
        def __init__(self, config=None):  # noqa: ARG002
            pass

        def add_venue(self, **_kwargs):
            events.append("add_venue")

    import nautilus_trader.backtest.engine as _engine_mod
    monkeypatch.setattr(_engine_mod, "BacktestEngine", _StubEngine)

    _R().main()
    assert events == ["build", "add_data", "run", ("print", "results")]
