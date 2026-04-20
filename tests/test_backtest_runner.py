"""Smoke tests for module imports."""

import pytest


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


@pytest.mark.integration
def test_run_backtest_end_to_end_ema_cross(crypto_catalog_path):
    """Smoke: build → run → inspect results. Uses real engine + fixture data."""
    from nautilus_trader.persistence.catalog import ParquetDataCatalog

    from nautilus_trading.backtest.runner import build_backtest_config, run_backtest

    catalog = ParquetDataCatalog(str(crypto_catalog_path))
    config = build_backtest_config(
        catalog,
        strategy_path="strategies.forex.ema_cross:EMACrossStrategy",
        config_path="strategies.forex.ema_cross:EMACrossConfig",
        bar_interval="1-HOUR-LAST-EXTERNAL",
        trade_size="0.001",
        fast_ema_period=5,
        slow_ema_period=15,
        venue_name="BINANCE",
        base_currency="USDT",
        starting_balance="10_000 USDT",
        end_time=None,
    )
    results = run_backtest(config)
    assert len(results) == 1
    assert results[0].elapsed_time >= 0
