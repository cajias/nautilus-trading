"""Smoke tests for module imports."""


def test_runner_module_imports():
    """Verify nautilus_trading.backtest.runner is importable and has run_backtest."""
    from nautilus_trading.backtest import runner

    assert hasattr(runner, "run_backtest"), "runner module missing run_backtest function"


def test_cli_module_imports():
    """Verify nautilus_trading.cli.app is importable."""
    from nautilus_trading.cli import app

    assert app is not None


def test_strategy_importable():
    """Verify EMACrossStrategy can be imported from strategies package."""
    from strategies.forex.ema_cross import EMACrossStrategy

    assert EMACrossStrategy is not None
