"""ShockGuardPaperTradeRunner composition test.

Does NOT hit Testnet. Verifies the runner builds the right TradingNodeConfig
and attaches the right strategy with the right config dict.
"""

from __future__ import annotations

from nautilus_trader.adapters.binance import BINANCE
from nautilus_trader.adapters.binance.common.enums import BinanceEnvironment
from strategies.crypto.shock_guard_paper import ShockGuardPaperTradeRunner


def test_runner_builds_testnet_spot_config():
    runner = ShockGuardPaperTradeRunner(
        instrument_id="BTCUSDT.BINANCE",
        bar_type="BTCUSDT.BINANCE-1-MINUTE-LAST-EXTERNAL",
        trade_size="0.001",
    )
    config = runner.build_config()

    assert config.data_clients[BINANCE].environment == BinanceEnvironment.TESTNET
    assert len(config.strategies) == 1
    strat_entry = config.strategies[0]
    assert strat_entry.strategy_path == "strategies.crypto.shock_guard:ShockGuardStrategy"
    assert strat_entry.config_path == "strategies.crypto.shock_guard:ShockGuardConfig"
    assert strat_entry.config["instrument_id"] == "BTCUSDT.BINANCE"
    assert strat_entry.config["trade_size"] == "0.001"
