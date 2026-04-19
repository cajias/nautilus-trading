"""Characterization tests for nautilus_trading.backtest.runner."""

from __future__ import annotations

import pytest

from nautilus_trading.backtest.runner import build_backtest_config

# ---------------------------------------------------------------------------
# Sub-project A characterization tests — capture current per-strategy branches
# in build_backtest_config before PRs 5-6 refactor them behind a registry.
# ---------------------------------------------------------------------------


def test_build_backtest_config_ema_cross_includes_ema_params(crypto_catalog_path):
    from nautilus_trader.persistence.catalog import ParquetDataCatalog

    catalog = ParquetDataCatalog(str(crypto_catalog_path))
    config = build_backtest_config(
        catalog,
        strategy_path="strategies.forex.ema_cross:EMACrossStrategy",
        config_path="strategies.forex.ema_cross:EMACrossConfig",
        bar_interval="1-HOUR-LAST-EXTERNAL",
        trade_size="0.01",
        fast_ema_period=10,
        slow_ema_period=20,
        venue_name="BINANCE",
        base_currency="USDT",
        starting_balance="10_000 USDT",
        end_time=None,
    )
    strat_cfg = config.engine.strategies[0].config
    assert strat_cfg["trade_size"] == "0.01"
    assert strat_cfg["fast_ema_period"] == 10
    assert strat_cfg["slow_ema_period"] == 20


def test_build_backtest_config_non_ema_omits_ema_params(crypto_catalog_path):
    from nautilus_trader.persistence.catalog import ParquetDataCatalog

    catalog = ParquetDataCatalog(str(crypto_catalog_path))
    config = build_backtest_config(
        catalog,
        strategy_path="strategies.crypto.grid_bot:GridBotStrategy",
        config_path="strategies.crypto.grid_bot:GridBotConfig",
        bar_interval="1-HOUR-LAST-EXTERNAL",
        trade_size="0.01",
        venue_name="BINANCE",
        base_currency="USDT",
        starting_balance="10_000 USDT",
        end_time=None,
        strategy_config_overrides={
            "upper_price": "50000",
            "lower_price": "40000",
            "grid_levels": 10,
        },
    )
    strat_cfg = config.engine.strategies[0].config
    assert "fast_ema_period" not in strat_cfg
    assert "slow_ema_period" not in strat_cfg
    assert strat_cfg["upper_price"] == "50000"
    assert strat_cfg["grid_levels"] == 10


def test_build_backtest_config_raises_when_catalog_empty(tmp_path):
    from nautilus_trader.persistence.catalog import ParquetDataCatalog

    empty = ParquetDataCatalog(str(tmp_path / "empty"))
    with pytest.raises(RuntimeError, match="No instruments found"):
        build_backtest_config(empty)


def test_build_backtest_config_raises_on_bad_instrument_index(crypto_catalog_path):
    from nautilus_trader.persistence.catalog import ParquetDataCatalog

    catalog = ParquetDataCatalog(str(crypto_catalog_path))
    with pytest.raises(RuntimeError, match="instrument_index 99 out of range"):
        build_backtest_config(catalog, instrument_index=99)


# ---------------------------------------------------------------------------
# EMABacktestRunner — BacktestRunner ABC conformance
# ---------------------------------------------------------------------------


def test_ema_backtest_runner_matches_function_output(crypto_catalog_path):
    """EMABacktestRunner.build_config() must equal build_backtest_config()
    output for the same kwargs — the wrapper adds no behavioral drift."""
    from nautilus_trader.persistence.catalog import ParquetDataCatalog

    from nautilus_trading.backtest.runner import EMABacktestRunner

    catalog = ParquetDataCatalog(str(crypto_catalog_path))
    kwargs = {
        "strategy_path": "strategies.forex.ema_cross:EMACrossStrategy",
        "config_path": "strategies.forex.ema_cross:EMACrossConfig",
        "bar_interval": "1-HOUR-LAST-EXTERNAL",
        "trade_size": "0.01",
        "fast_ema_period": 5,
        "slow_ema_period": 15,
        "venue_name": "BINANCE",
        "base_currency": "USDT",
        "starting_balance": "10_000 USDT",
        "end_time": None,
    }
    runner = EMABacktestRunner(catalog, **kwargs)
    assert runner.build_config() == build_backtest_config(catalog, **kwargs)


def test_ema_backtest_runner_main_skips_engine_creation(crypto_catalog_path, monkeypatch):
    """EMABacktestRunner.main() must NOT construct a BacktestEngine (the
    BacktestNode owns its own engine). If the default BacktestRunner.main()
    ever fires for this subclass, this test catches it."""
    from nautilus_trader.persistence.catalog import ParquetDataCatalog

    from nautilus_trading.backtest import runner as runner_module

    def _boom(*args, **kwargs):
        raise AssertionError(
            "EMABacktestRunner.main() must not construct BacktestEngine"
        )

    import nautilus_trader.backtest.engine as nt_engine

    monkeypatch.setattr(nt_engine, "BacktestEngine", _boom)

    call_log: list[str] = []
    monkeypatch.setattr(
        runner_module, "run_backtest", lambda cfg: (call_log.append("run"), [])[1]
    )
    monkeypatch.setattr(
        runner_module, "print_results", lambda r: call_log.append("print")
    )

    catalog = ParquetDataCatalog(str(crypto_catalog_path))
    runner = runner_module.EMABacktestRunner(
        catalog,
        strategy_path="strategies.forex.ema_cross:EMACrossStrategy",
        config_path="strategies.forex.ema_cross:EMACrossConfig",
        bar_interval="1-HOUR-LAST-EXTERNAL",
        trade_size="0.01",
        fast_ema_period=5,
        slow_ema_period=15,
        venue_name="BINANCE",
        base_currency="USDT",
        starting_balance="10_000 USDT",
        end_time=None,
    )
    runner.main()
    assert call_log == ["run", "print"]
