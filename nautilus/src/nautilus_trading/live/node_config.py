"""TradingNodeConfig builder for Binance PROD live trading (SCAFFOLD ONLY).

Mirrors :func:`nautilus_trading.paper_trade.node_config.build_paper_trade_node_config`
but flips the Binance environment to ``LIVE``. Keeping the two builders as
parallel functions (rather than a shared one with a ``live: bool`` flag) is
deliberate:

1. Each function declares its environment at the top — code-search for
   ``BinanceEnvironment.LIVE`` reliably surfaces every real-money codepath
   without having to trace boolean flags.
2. The paper-trade builder's docstring is allowed to mention "Testnet
   blocker fixes" (Ed25519 etc.) without that copy leaking into the live
   path's docs.

Real-money execution is out of scope per the 2026-04-21 no-real-money
directive. This builder produces a valid ``TradingNodeConfig`` — but
:class:`~nautilus_trading.live.strategy_runner.LiveStrategyRunner.main`
refuses to boot it. ``build_config()`` is allowed to succeed for shape
symmetry with the paper-trade and backtest runners; the failure happens at
boot, not at config-validation time.
"""

from __future__ import annotations

from typing import Any

from nautilus_trader.adapters.binance import (
    BINANCE,
    BinanceInstrumentProviderConfig,
)
from nautilus_trader.adapters.binance.common.enums import BinanceAccountType, BinanceEnvironment
from nautilus_trader.adapters.binance.config import (
    BinanceDataClientConfig,
    BinanceExecClientConfig,
    BinanceKeyType,
)
from nautilus_trader.config import (
    ImportableActorConfig,
    ImportableStrategyConfig,
    LoggingConfig,
    TradingNodeConfig,
)
from nautilus_trader.model.identifiers import InstrumentId


def build_live_node_config(
    *,
    strategy_path: str,
    config_path: str,
    strategy_config: dict[str, Any],
    instrument_id: str,
    log_level: str = "INFO",
    trader_id: str = "LIVE-TRADER-001",
    actors: list[ImportableActorConfig] | None = None,
) -> TradingNodeConfig:
    """Build a :class:`TradingNodeConfig` targeting Binance PROD.

    Parallel to :func:`build_paper_trade_node_config`, with two structural
    differences:

    - ``environment`` is :data:`BinanceEnvironment.LIVE` (not ``TESTNET``).
    - ``trader_id`` defaults to ``LIVE-TRADER-001`` so logs / journals make
      the runtime mode unambiguous when both runners are deployed side by
      side in the future.

    Parameters mirror ``build_paper_trade_node_config`` exactly so a future
    real-money implementer can point :class:`LiveStrategyRunner.main` at
    this config without re-shaping any caller.
    """
    account_type = BinanceAccountType.SPOT
    environment = BinanceEnvironment.LIVE
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
        actors=list(actors) if actors else [],
        strategies=[
            ImportableStrategyConfig(
                strategy_path=strategy_path,
                config_path=config_path,
                config=strategy_config,
            ),
        ],
    )
