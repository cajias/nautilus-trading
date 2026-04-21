"""TimesFMSwingPaperTradeRunner composition test.

Does NOT hit Testnet. Verifies the runner builds the right TradingNodeConfig
and attaches the right strategy with the right config dict. Also does NOT
trigger any ML model load — `build_config` only constructs a config; the
strategy class (and its TimesFM checkpoint load) are resolved lazily by the
TradingNode at run-time.
"""

from __future__ import annotations

from nautilus_trader.adapters.binance import BINANCE
from nautilus_trader.adapters.binance.common.enums import BinanceEnvironment
from strategies.crypto.timesfm_swing_paper import TimesFMSwingPaperTradeRunner


def test_runner_builds_testnet_spot_config():
    runner = TimesFMSwingPaperTradeRunner(
        instrument_id="BTCUSDT.BINANCE",
        bar_type="BTCUSDT.BINANCE-1-MINUTE-LAST-EXTERNAL",
        trade_size="0.001",
        fast_ema=5,
        slow_ema=30,
    )
    config = runner.build_config()

    assert config.data_clients[BINANCE].environment == BinanceEnvironment.TESTNET
    assert len(config.strategies) == 1
    strat_entry = config.strategies[0]
    assert strat_entry.strategy_path == "strategies.crypto.timesfm_swing:TimesFMSwingStrategy"
    assert strat_entry.config_path == "strategies.crypto.timesfm_swing:TimesFMSwingConfig"
    # TimesFMConfigBuilder emits ema_period (from slow) + fallback_fast_ema_period (from fast);
    # no fast_ema_period / slow_ema_period (those belong to the ema_cross builder).
    assert strat_entry.config["ema_period"] == 30
    assert strat_entry.config["fallback_fast_ema_period"] == 5
    assert "fast_ema_period" not in strat_entry.config
