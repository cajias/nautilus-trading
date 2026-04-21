"""TradingNodeConfig builder for Binance Spot Testnet paper-trade runs.

Centralizes the three Binance-Testnet blocker fixes so every PaperTradeRunner
subclass inherits them for free:

    1. Ed25519 key type for user-data WebSocket (§7.1 of spec).
    2. InstrumentProviderConfig populated with the run's target instrument (§7.2).
    3. Tick-size rounding helper for LIMIT orders (§7.3; added in PR 2).
"""

from __future__ import annotations

import os
import signal
import sys
from decimal import ROUND_FLOOR, Decimal
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
from nautilus_trader.model.objects import Price


def round_to_tick(price: Decimal, instrument: Any) -> Price:
    """Floor `price` to the instrument's tick grid.

    Binance rejects LIMIT orders whose price is not on the tick grid (2026-04-08
    incident with grid_bot). Strategies that construct LIMIT prices arithmetically
    must call this helper before submit_order().

    Floor (not round-half-even) is chosen for two reasons:
      1. Symmetry with Binance's own validator, which truncates.
      2. A floored BUY-limit price can never overshoot the user's ceiling;
         the SELL side is handled by callers mirroring the offset.
    """
    tick = instrument.price_increment.as_decimal()
    floored = (price / tick).quantize(Decimal("1"), rounding=ROUND_FLOOR) * tick
    return Price(floored, precision=instrument.price_precision)


def build_paper_trade_node_config(
    *,
    strategy_path: str,
    config_path: str,
    strategy_config: dict[str, Any],
    instrument_id: str,
    log_level: str = "INFO",
    trader_id: str = "PAPER-TRADER-001",
) -> TradingNodeConfig:
    """Build a TradingNodeConfig for Binance Spot Testnet paper trading.

    Parameters
    ----------
    strategy_path : str
        Full import path for the strategy class
        (e.g. "strategies.crypto.grid_bot:GridBotStrategy").
    config_path : str
        Full import path for the strategy's config class.
    strategy_config : dict
        Strategy configuration parameters — emitted by STRATEGY_BUILDERS[name].build(args).
    instrument_id : str
        Instrument ID to load into the venue cache (e.g. "BTCUSDT.BINANCE").
    log_level : str
        Logging level.
    trader_id : str
        Trader identifier.
    """
    account_type = BinanceAccountType.SPOT
    environment = BinanceEnvironment.TESTNET
    instrument_provider = BinanceInstrumentProviderConfig(
        load_ids=frozenset([InstrumentId.from_str(instrument_id)]),
    )

    return TradingNodeConfig(
        trader_id=trader_id,
        logging=LoggingConfig(log_level=log_level),
        data_clients={
            BINANCE: BinanceDataClientConfig(
                account_type=account_type,
                environment=environment,
                key_type=BinanceKeyType.ED25519,
                instrument_provider=instrument_provider,
            ),
        },
        exec_clients={
            BINANCE: BinanceExecClientConfig(
                account_type=account_type,
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


def run_paper_trade(config: TradingNodeConfig) -> None:
    """Start a paper-trade node. Blocks until SIGINT/SIGTERM."""
    _check_testnet_api_keys()

    node = TradingNode(config=config)
    node.add_data_client_factory(BINANCE, BinanceLiveDataClientFactory)
    node.add_exec_client_factory(BINANCE, BinanceLiveExecClientFactory)
    node.build()

    def _shutdown(_signum, _frame):
        print("\nShutting down paper-trade node...")
        node.stop()
        node.dispose()
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    node.run()


def _check_testnet_api_keys() -> None:
    """Fail fast with an actionable message if Testnet credentials are missing."""
    key = os.environ.get("BINANCE_TESTNET_API_KEY")
    secret = os.environ.get("BINANCE_TESTNET_API_SECRET")
    ed25519_path = os.environ.get("BINANCE_TESTNET_ED25519_KEY_PATH")

    if not key:
        print("ERROR: BINANCE_TESTNET_API_KEY not found in environment.")
        print("Set it in .env.local or export it. Get keys at https://testnet.binance.vision/")
        sys.exit(1)
    if not secret:
        print("ERROR: BINANCE_TESTNET_API_SECRET not found in environment.")
        print("Set it in .env.local or export it. Get keys at https://testnet.binance.vision/")
        sys.exit(1)
    if not ed25519_path:
        print("ERROR: BINANCE_TESTNET_ED25519_KEY_PATH not found in environment.")
        print("User-data WebSocket requires Ed25519 signing.")
        print("Generate a key with:")
        print("  openssl genpkey -algorithm ed25519 -out ed25519_private.pem")
        print("Then set BINANCE_TESTNET_ED25519_KEY_PATH to its absolute path.")
        sys.exit(1)

    from pathlib import Path

    pem = Path(ed25519_path)
    if not pem.is_file() or not os.access(pem, os.R_OK):
        print(f"ERROR: Ed25519 PEM at {ed25519_path} is not readable or does not exist.")
        print("Check the path in BINANCE_TESTNET_ED25519_KEY_PATH.")
        sys.exit(1)
