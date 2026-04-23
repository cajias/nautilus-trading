"""HybridSMAR10PaperTradeRunner composition test.

Does NOT hit Testnet. Verifies the runner builds the right TradingNodeConfig
and attaches the HybridSMAR10Strategy with the right config dict. Notably,
hybrid_sma_r10 sizes from equity and therefore does NOT carry `trade_size`
in its strategy config dict.
"""

from __future__ import annotations

from nautilus_trader.adapters.binance import BINANCE
from nautilus_trader.adapters.binance.common.enums import BinanceEnvironment
from strategies.crypto.hybrid_sma_r10_paper import HybridSMAR10PaperTradeRunner


def test_runner_builds_testnet_spot_config():
    runner = HybridSMAR10PaperTradeRunner(
        instrument_id="BTCUSDT.BINANCE",
        bar_type="BTCUSDT.BINANCE-1-MINUTE-LAST-EXTERNAL",
        sma_fast=10,
        sma_slow=30,
        stop_fast="0.05",
        stop_slow="0.10",
    )
    config = runner.build_config()

    assert config.data_clients[BINANCE].environment == BinanceEnvironment.TESTNET
    assert len(config.strategies) == 1
    strat_entry = config.strategies[0]
    assert strat_entry.strategy_path == "strategies.crypto.hybrid_sma_r10:HybridSMAR10Strategy"
    assert strat_entry.config_path == "strategies.crypto.hybrid_sma_r10:HybridSMAR10Config"
    assert strat_entry.config["sma_fast"] == 10
    assert strat_entry.config["sma_slow"] == 30
    # stop fields are stringified by the builder (Decimal round-trips through str()).
    assert strat_entry.config["stop_fast"] == "0.05"
    assert strat_entry.config["stop_slow"] == "0.10"
    # No trade_size in the config — HybridSMA sizes from equity, not per-trade.
    assert "trade_size" not in strat_entry.config
