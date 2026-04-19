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
    out = builder.build({
        "instrument_id": "BTCUSDT.BINANCE",
        "bar_type": "BTCUSDT.BINANCE-1-HOUR-LAST-EXTERNAL",
        "trade_size": "0.001",
        "upper_price": "50000",
        "lower_price": "40000",
        "grid_levels": 8,
    })
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
    with pytest.raises(ValueError, match="upper_price"):
        builder.build({
            "instrument_id": "BTCUSDT.BINANCE",
            "bar_type": "X",
            "trade_size": "0.001",
        })
