"""DCABotPaperTradeRunner composition test.

Does NOT hit Testnet. Verifies the runner builds the right TradingNodeConfig
and attaches the right strategy with the right config dict.
"""

from __future__ import annotations

from nautilus_trader.adapters.binance import BINANCE
from nautilus_trader.adapters.binance.common.enums import BinanceEnvironment
from strategies.crypto.dca_bot_paper import DCABotPaperTradeRunner


def test_runner_builds_testnet_spot_config():
    runner = DCABotPaperTradeRunner(
        instrument_id="BTCUSDT.BINANCE",
        bar_type="BTCUSDT.BINANCE-1-MINUTE-LAST-EXTERNAL",
        trade_size="0.001",
        buy_interval_bars=60,
        buy_amount="10",
    )
    config = runner.build_config()

    assert config.data_clients[BINANCE].environment == BinanceEnvironment.TESTNET
    assert len(config.strategies) == 1
    strat_entry = config.strategies[0]
    assert strat_entry.strategy_path == "strategies.crypto.dca_bot:DCABotStrategy"
    assert strat_entry.config_path == "strategies.crypto.dca_bot:DCABotConfig"
    assert strat_entry.config["buy_interval_bars"] == 60
    assert strat_entry.config["buy_amount"] == "10"


def test_runner_omits_buy_amount_when_not_provided():
    """buy_amount is optional; builder only forwards it when truthy."""
    runner = DCABotPaperTradeRunner(
        instrument_id="BTCUSDT.BINANCE",
        bar_type="BTCUSDT.BINANCE-1-MINUTE-LAST-EXTERNAL",
        trade_size="0.001",
        buy_interval_bars=60,
    )
    config = runner.build_config()

    strat_entry = config.strategies[0]
    assert "buy_amount" not in strat_entry.config
    assert strat_entry.config["buy_interval_bars"] == 60
