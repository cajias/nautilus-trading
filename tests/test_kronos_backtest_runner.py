"""Characterization test for KronosBacktestRunner.main() engine_cfg handling.

Guards PR #16 Copilot Finding #4: BacktestEngine(config=...) must receive the
engine_cfg value verbatim, including empty dicts. The pre-Option-D ABC default
silently replaced empty dicts with BacktestEngine() no-arg — this test pins
the Kronos code path against that regression shape.
"""

from __future__ import annotations

from decimal import Decimal


def test_kronos_runner_main_passes_empty_engine_cfg(monkeypatch):
    import strategies.crypto.kronos.backtest as kronos_mod
    from strategies.crypto.kronos.backtest import KronosBacktestRunner

    recorded: dict = {}

    class _StubEngine:
        def __init__(self, config=None):
            recorded["config"] = config

        def add_venue(self, **kwargs):
            recorded["venue_kwargs"] = kwargs

        def dispose(self):
            recorded["disposed"] = True

    monkeypatch.setattr(kronos_mod, "BacktestEngine", _StubEngine)

    runner = KronosBacktestRunner(
        symbol="BTCUSDT",
        interval="1h",
        start="2024-01-01",
        end="2024-01-02",
        initial_capital=Decimal("500"),
        trade_size=Decimal("0.001"),
        model_size="mini",
        forecast_bars=24,
        n_samples=50,
        inference_interval=4,
    )

    # Stub build_config to return the minimal-but-complete shape main() consumes:
    # engine_cfg must be an EMPTY dict (the characterization point).
    class _VenueSpec:
        name = "BINANCE"
        oms_type = None
        account_type = None
        base_currency = None
        starting_balances = ()

    monkeypatch.setattr(
        runner,
        "build_config",
        lambda: {"engine_cfg": {}, "venue": _VenueSpec(), "instrument": None},
    )
    # Stub add_data / run / print_results — we only care about the engine ctor call.
    monkeypatch.setattr(runner, "add_data", lambda engine, config: None)
    monkeypatch.setattr(runner, "run", lambda engine: engine)
    monkeypatch.setattr(runner, "print_results", lambda results: None)

    # Stub Venue() and Money.from_str() bindings used inside main() — wrap to noops.
    monkeypatch.setattr(kronos_mod, "Venue", lambda name: name)
    monkeypatch.setattr(kronos_mod, "Money", type("M", (), {"from_str": staticmethod(lambda s: s)}))

    runner.main()

    assert recorded["config"] == {}, (
        f"KronosBacktestRunner.main() must pass engine_cfg verbatim; got {recorded.get('config')!r}"
    )
    assert recorded.get("disposed") is True, "engine.dispose() must run via try/finally"
