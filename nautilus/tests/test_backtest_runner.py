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
