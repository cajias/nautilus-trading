"""Forward-port of strategies/crypto/kronos/paper_trade.py's config construction.

This snapshot exists ONLY to anchor the PR 7 parity test. It translates the
old imperative add_actor()/add_strategy() pattern into a declarative
TradingNodeConfig so field-by-field parity can be asserted against the new
KronosPaperTradeRunner.build_config(). After PR 7 merges and paper_trade.py
is deleted, this file is frozen — do not edit it for new features.
"""

from __future__ import annotations

from nautilus_trader.adapters.binance import BINANCE, BinanceDataClientConfig, BinanceExecClientConfig
from nautilus_trader.adapters.binance.common.enums import BinanceAccountType, BinanceEnvironment
from nautilus_trader.config import ImportableActorConfig, ImportableStrategyConfig, LoggingConfig, TradingNodeConfig


def build_quarantined_config(
    *,
    instrument_id: str = "BTCUSDT.BINANCE",
    bar_type: str = "BTCUSDT.BINANCE-1-HOUR-LAST-EXTERNAL",
    trade_size: str = "0.001",
    model_size: str = "mini",
    n_samples: int = 10,
    forecast_horizon: int = 24,
    inference_interval_bars: int = 4,
) -> TradingNodeConfig:
    """Rebuild the TradingNodeConfig the old paper_trade.py script would have produced if declarative."""
    return TradingNodeConfig(
        logging=LoggingConfig(log_level="INFO"),
        data_clients={
            BINANCE: BinanceDataClientConfig(
                account_type=BinanceAccountType.SPOT,
                environment=BinanceEnvironment.TESTNET,
            ),
        },
        exec_clients={
            BINANCE: BinanceExecClientConfig(
                account_type=BinanceAccountType.SPOT,
                environment=BinanceEnvironment.TESTNET,
            ),
        },
        actors=[
            ImportableActorConfig(
                actor_path="strategies.crypto.kronos.actor:KronosActor",
                config_path="strategies.crypto.kronos.actor:KronosActorConfig",
                config={
                    "instrument_id": instrument_id,
                    "bar_type": bar_type,
                    "model_size": model_size,
                    "forecast_horizon": forecast_horizon,
                    "inference_interval_bars": inference_interval_bars,
                    "n_samples": n_samples,
                },
            ),
        ],
        strategies=[
            ImportableStrategyConfig(
                strategy_path="strategies.crypto.kronos.strategy:KronosStrategy",
                config_path="strategies.crypto.kronos.strategy:KronosStrategyConfig",
                config={
                    "instrument_id": instrument_id,
                    "bar_type": bar_type,
                    "trade_size": trade_size,
                },
            ),
        ],
    )
