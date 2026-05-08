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
from nautilus_trader.model.data import BarType
from nautilus_trader.model.identifiers import InstrumentId


def _validate_inputs(
    strategy_configs: list[ImportableStrategyConfig],
    bar_types: set[str],
    instrument_ids: set[InstrumentId],
) -> None:
    """Fail-fast validation on the public builder's inputs.

    All three checks surface bad inputs at builder time rather than at
    ``node.build()`` / ``node.run()`` time, where the diagnostics are
    deeper in nautilus internals and harder to map back to the call site.
    """
    if not strategy_configs:
        raise ValueError("strategy_configs must not be empty")

    # Validate instrument_ids BEFORE wrapping in frozenset — once frozen,
    # iteration order on error is non-deterministic and the diagnostic
    # would name an arbitrary bad member.
    for iid in instrument_ids:
        if not isinstance(iid, InstrumentId):
            raise TypeError(
                f"instrument_ids must contain InstrumentId instances; "
                f"got {type(iid).__name__} ({iid!r}). "
                f"Use InstrumentId.from_str(...) to convert strings.",
            )

    # Parse-check every bar_type at builder time — BarType.from_str is
    # strict about format and would otherwise raise at node start.
    for bt in bar_types:
        BarType.from_str(bt)  # parseability only; result is discarded

    # Detect duplicate component_id across the strategy configs. Only
    # explicit overrides count — nautilus auto-derives a component_id
    # when the entry is absent, and those derived ids are guaranteed
    # unique by class+suffix logic.
    seen: dict[str, int] = {}
    duplicates: list[str] = []
    for s in strategy_configs:
        cid = (s.config or {}).get("component_id")
        if cid is None:
            continue
        if cid in seen:
            duplicates.append(cid)
        seen[cid] = seen.get(cid, 0) + 1
    if duplicates:
        raise ValueError(
            f"Duplicate component_id values across strategy_configs: {sorted(set(duplicates))}",
        )


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
    _validate_inputs(strategy_configs, bar_types, instrument_ids)

    # frozenset is unordered — the prior ``sorted(...)`` was wasted work.
    instrument_provider = BinanceInstrumentProviderConfig(
        load_ids=frozenset(instrument_ids),
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
