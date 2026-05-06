"""Multi-strategy paper-trade TradingNodeConfig builder.

Companion to ``node_config.build_paper_trade_node_config`` (single-strategy).
This builder wires N strategies into a single Binance Spot Testnet node and
auto-pre-wires one :class:`BarFanoutActor` per unique ``bar_type`` to work
around nautilus_trader's multi-strategy shared-subscription dedup bug
(verified empirically 2026-05-06; see ``bar_fanout`` for details).

Each strategy passed in is expected to subscribe to ``DataType(FanoutBar)``
in ``on_start`` (and unwrap via ``on_data``) instead of calling
``self.subscribe_bars(bar_type)`` directly. Without that pattern, only the
first strategy's ``on_bar`` would fire.
"""

from __future__ import annotations

from nautilus_trader.adapters.binance import (
    BINANCE,
    BinanceInstrumentProviderConfig,
)
from nautilus_trader.adapters.binance.common.enums import (
    BinanceAccountType,
    BinanceEnvironment,
)
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


def build_multi_strategy_paper_node_config(
    *,
    strategy_configs: list[ImportableStrategyConfig],
    bar_types: set[str],
    instrument_ids: set[InstrumentId],
    trader_id: str = "PAPER-TRADER-001",
    log_level: str = "INFO",
) -> TradingNodeConfig:
    """Build a Binance Spot Testnet ``TradingNodeConfig`` with the
    multi-strategy bar-fanout actor pre-wired.

    One :class:`BarFanoutActor` is auto-generated per unique entry in
    ``bar_types`` (component_id = ``BarFanout-<i>`` for stable ordering).

    Parameters
    ----------
    strategy_configs : list[ImportableStrategyConfig]
        One ``ImportableStrategyConfig`` per consumer strategy. Each strategy
        is expected to subscribe via ``DataType(FanoutBar)`` rather than
        ``subscribe_bars(bar_type)`` directly — see
        ``nautilus_trading.paper_trade.bar_fanout`` for the contract.
    bar_types : set[str]
        Unique ``bar_type`` strings spanning all strategies. One
        ``BarFanoutActor`` is wired per entry.
    instrument_ids : set[InstrumentId]
        Unique instruments to load into the Binance ``InstrumentProvider``.
        Strategies subscribe to instruments individually at runtime.
    trader_id : str
        Trader identifier on the resulting node.
    log_level : str
        Logging level.

    Returns
    -------
    TradingNodeConfig
        A node config with Binance Spot Testnet data + exec clients (Ed25519),
        the bar-fanout actors, and the supplied strategy configs.
    """
    instrument_provider = BinanceInstrumentProviderConfig(
        load_ids=frozenset(sorted(instrument_ids, key=str)),
    )

    # One BarFanoutActor per unique bar_type. Sorted for deterministic
    # component_id assignment across runs/replays.
    actor_configs: list[ImportableActorConfig] = [
        ImportableActorConfig(
            actor_path="nautilus_trading.paper_trade.bar_fanout:BarFanoutActor",
            config_path="nautilus_trading.paper_trade.bar_fanout:BarFanoutActorConfig",
            config={
                "component_id": f"BarFanout-{i}",
                "bar_type": bt,
            },
        )
        for i, bt in enumerate(sorted(bar_types))
    ]

    account_type = BinanceAccountType.SPOT
    environment = BinanceEnvironment.TESTNET

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
        actors=actor_configs,
        strategies=list(strategy_configs),
    )
