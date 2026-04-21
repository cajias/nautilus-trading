"""EMACrossPaperTradeRunner composition test.

Does NOT hit Testnet. Verifies the runner builds the right TradingNodeConfig
and attaches the right strategy with the right config dict.
"""

from __future__ import annotations

from nautilus_trader.adapters.binance import BINANCE
from nautilus_trader.adapters.binance.common.enums import BinanceEnvironment
from strategies.crypto.ema_cross_paper import EMACrossPaperTradeRunner


def test_runner_builds_testnet_spot_config():
    runner = EMACrossPaperTradeRunner(
        instrument_id="BTCUSDT.BINANCE",
        bar_type="BTCUSDT.BINANCE-1-MINUTE-LAST-EXTERNAL",
        trade_size="0.001",
        fast_ema=10,
        slow_ema=20,
    )
    config = runner.build_config()

    assert config.data_clients[BINANCE].environment == BinanceEnvironment.TESTNET
    assert len(config.strategies) == 1
    strat_entry = config.strategies[0]
    assert strat_entry.strategy_path == "strategies.forex.ema_cross:EMACrossStrategy"
    assert strat_entry.config_path == "strategies.forex.ema_cross:EMACrossConfig"
    assert strat_entry.config["fast_ema_period"] == 10
    assert strat_entry.config["slow_ema_period"] == 20
