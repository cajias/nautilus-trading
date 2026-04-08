"""Live/paper trading node configuration and execution."""

from __future__ import annotations

import os
import signal
import sys
from typing import Any

from nautilus_trader.adapters.binance import (
    BINANCE,
    BinanceInstrumentProviderConfig,
    BinanceLiveDataClientFactory,
    BinanceLiveExecClientFactory,
)
from nautilus_trader.adapters.binance.common.enums import BinanceAccountType, BinanceEnvironment
from nautilus_trader.adapters.binance.config import (
    BinanceDataClientConfig,
    BinanceExecClientConfig,
    BinanceKeyType,
)
from nautilus_trader.config import (
    ImportableStrategyConfig,
    LoggingConfig,
    TradingNodeConfig,
)
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.identifiers import InstrumentId


def build_live_config(
    *,
    strategy_path: str,
    config_path: str,
    strategy_config: dict[str, Any],
    instrument_id: str,
    account_type: str = "SPOT",
    testnet: bool = True,
    log_level: str = "INFO",
    trader_id: str = "TRADER-001",
) -> TradingNodeConfig:
    """Build a TradingNodeConfig for Binance live/paper trading.

    Parameters
    ----------
    strategy_path : str
        Full import path for the strategy class (e.g. "strategies.crypto.grid_bot:GridBotStrategy").
    config_path : str
        Full import path for the config class (e.g. "strategies.crypto.grid_bot:GridBotConfig").
    strategy_config : dict
        Strategy configuration parameters.
    instrument_id : str
        Instrument ID to load into the venue cache (e.g. "BTCUSDT.BINANCE").
    account_type : str
        Binance account type: SPOT, MARGIN, USDT_FUTURE, COIN_FUTURE.
    testnet : bool
        If True, use Binance testnet. If False, use production (requires real API keys).
    log_level : str
        Logging level.
    trader_id : str
        Trader identifier.
    """
    binance_account = BinanceAccountType[account_type]
    environment = BinanceEnvironment.TESTNET if testnet else BinanceEnvironment.LIVE
    instrument_provider = BinanceInstrumentProviderConfig(
        load_ids=frozenset([InstrumentId.from_str(instrument_id)]),
    )

    return TradingNodeConfig(
        trader_id=trader_id,
        logging=LoggingConfig(log_level=log_level),
        data_clients={
            BINANCE: BinanceDataClientConfig(
                account_type=binance_account,
                environment=environment,
                key_type=BinanceKeyType.ED25519,
                instrument_provider=instrument_provider,
            ),
        },
        exec_clients={
            BINANCE: BinanceExecClientConfig(
                account_type=binance_account,
                environment=environment,
                key_type=BinanceKeyType.ED25519,
                instrument_provider=instrument_provider,
            ),
        },
        strategies=[
            ImportableStrategyConfig(
                strategy_path=strategy_path,
                config_path=config_path,
                config=strategy_config,
            ),
        ],
    )


def run_live(config: TradingNodeConfig) -> None:
    """Start a live trading node. Blocks until interrupted."""
    _check_api_keys(config)

    node = TradingNode(config=config)
    node.add_data_client_factory(BINANCE, BinanceLiveDataClientFactory)
    node.add_exec_client_factory(BINANCE, BinanceLiveExecClientFactory)
    node.build()

    # Graceful shutdown on Ctrl+C
    def _shutdown(signum, frame):
        print("\nShutting down trading node...")
        node.stop()
        node.dispose()
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    node.run()


def _check_api_keys(config: TradingNodeConfig) -> None:
    """Verify that Binance API keys are set in the environment."""
    binance_config = config.exec_clients.get(BINANCE)
    if binance_config is None:
        return

    is_testnet = getattr(binance_config, "environment", None) == BinanceEnvironment.TESTNET

    if is_testnet:
        key = os.environ.get("BINANCE_TESTNET_API_KEY") or os.environ.get("BINANCE_API_KEY")
        secret = os.environ.get("BINANCE_TESTNET_API_SECRET") or os.environ.get("BINANCE_API_SECRET")
        env_label = "BINANCE_TESTNET_API_KEY / BINANCE_TESTNET_API_SECRET"
    else:
        key = os.environ.get("BINANCE_API_KEY")
        secret = os.environ.get("BINANCE_API_SECRET")
        env_label = "BINANCE_API_KEY / BINANCE_API_SECRET"

    if not key or not secret:
        print("ERROR: Binance API keys not found in environment.")
        print(f"Set {env_label} before running.")
        if is_testnet:
            print("Get testnet keys at: https://testnet.binance.vision/")
        sys.exit(1)
