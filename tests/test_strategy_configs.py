"""Tests for the cli._strategy_configs registry."""

from __future__ import annotations

import pytest


def test_strategy_config_builder_is_protocol():
    from nautilus_trading.cli._strategy_configs import StrategyConfigBuilder

    # Protocols don't subclass ABC but are runtime-checkable when decorated.
    assert hasattr(StrategyConfigBuilder, "build")


def test_grid_bot_builder_outputs_expected_dict():
    from nautilus_trading.cli._strategy_configs import GridBotConfigBuilder

    builder = GridBotConfigBuilder()
    out = builder.build(
        {
            "instrument_id": "BTCUSDT.BINANCE",
            "bar_type": "BTCUSDT.BINANCE-1-HOUR-LAST-EXTERNAL",
            "trade_size": "0.001",
            "upper_price": "50000",
            "lower_price": "40000",
            "grid_levels": 8,
        }
    )
    assert out == {
        "instrument_id": "BTCUSDT.BINANCE",
        "bar_type": "BTCUSDT.BINANCE-1-HOUR-LAST-EXTERNAL",
        "trade_size": "0.001",
        "upper_price": "50000",
        "lower_price": "40000",
        "grid_levels": 8,
    }


def test_grid_bot_builder_raises_when_prices_missing():
    from nautilus_trading.cli._strategy_configs import GridBotConfigBuilder

    builder = GridBotConfigBuilder()
    with pytest.raises(ValueError, match="upper-price"):
        builder.build(
            {
                "instrument_id": "BTCUSDT.BINANCE",
                "bar_type": "X",
                "trade_size": "0.001",
            }
        )


def test_dca_bot_builder():
    from nautilus_trading.cli._strategy_configs import DCABotConfigBuilder

    out = DCABotConfigBuilder().build(
        {
            "instrument_id": "BTCUSDT.BINANCE",
            "bar_type": "X",
            "trade_size": "0.001",
            "buy_amount": "5.0",
            "buy_interval_bars": 60,
        }
    )
    assert out["buy_amount"] == "5.0"
    assert out["buy_interval_bars"] == 60


def test_dca_bot_builder_omits_buy_amount_when_absent():
    from nautilus_trading.cli._strategy_configs import DCABotConfigBuilder

    out = DCABotConfigBuilder().build(
        {
            "instrument_id": "BTCUSDT.BINANCE",
            "bar_type": "X",
            "trade_size": "0.001",
            "buy_amount": None,
            "buy_interval_bars": 60,
        }
    )
    assert "buy_amount" not in out
    assert out["buy_interval_bars"] == 60


def test_ema_cross_builder():
    from nautilus_trading.cli._strategy_configs import EMAConfigBuilder

    out = EMAConfigBuilder().build(
        {
            "instrument_id": "BTCUSDT.BINANCE",
            "bar_type": "X",
            "trade_size": "0.001",
            "fast_ema": 20,
            "slow_ema": 50,
            "module_name": "ema_cross",
        }
    )
    assert out["fast_ema_period"] == 20
    assert out["slow_ema_period"] == 50
    assert out["ema_period"] == 50


def test_timesfm_swing_builder():
    from nautilus_trading.cli._strategy_configs import TimesFMConfigBuilder

    out = TimesFMConfigBuilder().build(
        {
            "instrument_id": "BTCUSDT.BINANCE",
            "bar_type": "X",
            "trade_size": "0.01",
            "fast_ema": 20,
            "slow_ema": 100,
        }
    )
    assert out["fallback_fast_ema_period"] == 20
    assert out["ema_period"] == 100
    assert "fast_ema_period" not in out


def test_hybrid_sma_builder_omits_trade_size_and_decimalizes():
    from nautilus_trading.cli._strategy_configs import HybridSMAConfigBuilder

    out = HybridSMAConfigBuilder().build(
        {
            "instrument_id": "BTCUSDT.BINANCE",
            "bar_type": "X",
            "trade_size": "0.01",
            "sma_fast": 10,
            "sma_slow": 30,
            "stop_fast": "0.5",
            "stop_slow": "1.0",
        }
    )
    assert "trade_size" not in out
    assert out["sma_fast"] == 10
    assert out["stop_fast"] == "0.5"
    assert isinstance(out["stop_fast"], str)


def test_dca_bot_builder_raises_when_interval_missing():
    from nautilus_trading.cli._strategy_configs import DCABotConfigBuilder

    builder = DCABotConfigBuilder()
    with pytest.raises(ValueError, match="buy_interval_bars"):
        builder.build(
            {
                "instrument_id": "BTCUSDT.BINANCE",
                "bar_type": "X",
                "trade_size": "0.001",
                "buy_amount": "5.0",
                "buy_interval_bars": None,
            }
        )


def test_hybrid_sma_builder_raises_when_sma_periods_missing():
    from nautilus_trading.cli._strategy_configs import HybridSMAConfigBuilder

    builder = HybridSMAConfigBuilder()
    with pytest.raises(ValueError, match="sma_fast"):
        builder.build(
            {
                "instrument_id": "BTCUSDT.BINANCE",
                "bar_type": "X",
                "trade_size": "0.01",
                "sma_fast": None,
                "sma_slow": None,
                "stop_fast": "0.5",
                "stop_slow": "1.0",
            }
        )


def test_hybrid_sma_builder_raises_when_stop_periods_missing():
    from nautilus_trading.cli._strategy_configs import HybridSMAConfigBuilder

    builder = HybridSMAConfigBuilder()
    with pytest.raises(ValueError, match="stop_fast"):
        builder.build(
            {
                "instrument_id": "BTCUSDT.BINANCE",
                "bar_type": "X",
                "trade_size": "0.01",
                "sma_fast": 10,
                "sma_slow": 30,
                "stop_fast": None,
                "stop_slow": None,
            }
        )


def test_timesfm_grid_builder_base_only():
    from nautilus_trading.cli._strategy_configs import TimesFMGridConfigBuilder

    out = TimesFMGridConfigBuilder().build(
        {
            "instrument_id": "BTCUSDT.BINANCE",
            "bar_type": "BTCUSDT.BINANCE-1-HOUR-LAST-EXTERNAL",
            "trade_size": "0.001",
        }
    )
    assert out == {
        "instrument_id": "BTCUSDT.BINANCE",
        "bar_type": "BTCUSDT.BINANCE-1-HOUR-LAST-EXTERNAL",
        "trade_size": "0.001",
    }


def test_rvs_swing_builder_base_only():
    from nautilus_trading.cli._strategy_configs import RVSSwingConfigBuilder

    out = RVSSwingConfigBuilder().build(
        {
            "instrument_id": "BTCUSDT.BINANCE",
            "bar_type": "X",
            "trade_size": "0.01",
        }
    )
    assert out["instrument_id"] == "BTCUSDT.BINANCE"
    assert out["trade_size"] == "0.01"
    assert len(out) == 3


def test_shock_guard_builder_base_only():
    from nautilus_trading.cli._strategy_configs import ShockGuardConfigBuilder

    out = ShockGuardConfigBuilder().build(
        {
            "instrument_id": "BTCUSDT.BINANCE",
            "bar_type": "X",
            "trade_size": "0.01",
        }
    )
    assert out["instrument_id"] == "BTCUSDT.BINANCE"
    assert out["trade_size"] == "0.01"
    assert len(out) == 3


def test_all_registered_strategies_resolvable():
    """Sanity: every key in STRATEGY_BUILDERS resolves to an object with .build()."""
    from nautilus_trading.cli._strategy_configs import STRATEGY_BUILDERS

    for name, builder in STRATEGY_BUILDERS.items():
        assert hasattr(builder, "build"), f"{name} builder missing .build()"
    # Lock in the current set so drift is obvious in diffs.
    assert set(STRATEGY_BUILDERS.keys()) == {
        "grid_bot",
        "dca_bot",
        "ema_cross",
        "timesfm_swing",
        "hybrid_sma_r10",
        "timesfm_grid",
        "rvs_swing",
        "shock_guard",
    }
