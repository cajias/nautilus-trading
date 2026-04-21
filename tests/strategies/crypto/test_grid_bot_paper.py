"""GridBotPaperTradeRunner composition test.

Does NOT hit Testnet. Verifies the runner builds the right TradingNodeConfig
and attaches the right strategy with the right config dict.
"""

from __future__ import annotations

from nautilus_trader.adapters.binance import BINANCE
from nautilus_trader.adapters.binance.common.enums import BinanceEnvironment
from strategies.crypto.grid_bot_paper import GridBotPaperTradeRunner


def test_runner_builds_testnet_spot_config():
    runner = GridBotPaperTradeRunner(
        instrument_id="BTCUSDT.BINANCE",
        bar_type="BTCUSDT.BINANCE-1-MINUTE-LAST-EXTERNAL",
        trade_size="0.001",
        upper_price="72000",
        lower_price="60000",
        grid_levels=8,
    )
    config = runner.build_config()

    assert config.data_clients[BINANCE].environment == BinanceEnvironment.TESTNET
    assert len(config.strategies) == 1
    strat_entry = config.strategies[0]
    assert strat_entry.strategy_path == "strategies.crypto.grid_bot:GridBotStrategy"
    assert strat_entry.config_path == "strategies.crypto.grid_bot:GridBotConfig"
    assert strat_entry.config["grid_levels"] == 8
    assert strat_entry.config["upper_price"] == "72000"
    assert strat_entry.config["lower_price"] == "60000"
