"""PaperTradeStrategyRunner — generic Binance Spot Testnet runner.

One concrete :class:`PaperTradeRunner` subclass parameterized by a
:class:`~nautilus_trading.cli._strategy_specs.StrategySpec` + a params dict.
Replaces the 8 ``*_paper.py`` shims and ``strategies/crypto/kronos/paper_runner.py``
(those get deleted in Task C of sub-project B.5 PR 1).

Signal-flow ordering
--------------------
Actor configs are built **before** the strategy config. The emitted
:class:`TradingNodeConfig` carries them in its ``actors`` list, which
NautilusTrader starts before anything in ``strategies`` at node boot.
This preserves the Kronos contract documented in
``strategies/crypto/kronos/strategy.py``: the actor publishes signals on
the message bus, the strategy consumes them — so the actor must be up first.

The runner itself does no TradingNode wiring — it delegates to
:func:`~nautilus_trading.paper_trade.node_config.build_paper_trade_node_config`,
which centralizes the Binance Testnet adapter setup (Ed25519, instrument
provider, account/environment).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from nautilus_trader.config import ImportableActorConfig, TradingNodeConfig

from nautilus_trading.cli._strategy_specs import StrategySpec
from nautilus_trading.paper_trade.node_config import (
    build_paper_trade_node_config,
    run_paper_trade,
)
from nautilus_trading.paper_trade.runner_base import PaperTradeRunner


@dataclass
class PaperTradeStrategyRunner(PaperTradeRunner):
    """Spec-driven paper-trade runner.

    Parameters
    ----------
    spec : StrategySpec
        Describes the strategy to attach (name, builder, import paths) plus
        any sibling actors. Pulled from
        :data:`~nautilus_trading.cli._strategy_specs.STRATEGY_SPECS`.
    params : dict[str, Any]
        Per-run arguments. Must include the base fields
        (``instrument_id``, ``bar_type``, ``trade_size``) plus any
        strategy-specific fields the builder requires. Feeds both the
        strategy builder and every actor builder — a single source of truth
        for run inputs.
    log_level : str, default "INFO"
        Forwarded to the node's :class:`LoggingConfig`.
    """

    spec: StrategySpec
    params: dict[str, Any]
    log_level: str = "INFO"

    def build_config(self) -> TradingNodeConfig:
        """Build the :class:`TradingNodeConfig`. Separated from :meth:`main`
        so unit tests can assert on the static config shape without booting
        a :class:`TradingNode`.

        Build order: actors → strategy. The ``self.params`` dict feeds both
        builders, so per-run state (instrument_id / bar_type / overrides)
        reaches each config without duplication.
        """
        # Actors first — list comprehension preserves spec.actor_specs order
        # so downstream message-bus wiring matches the spec declaration.
        actor_configs: list[ImportableActorConfig] = [
            ImportableActorConfig(
                actor_path=actor_spec.actor_path,
                config_path=actor_spec.config_path,
                config=actor_spec.builder.build(self.params),
            )
            for actor_spec in self.spec.actor_specs
        ]

        strategy_config = self.spec.builder.build(self.params)

        return build_paper_trade_node_config(
            strategy_path=self.spec.strategy_path,
            config_path=self.spec.config_path,
            strategy_config=strategy_config,
            instrument_id=self.params["instrument_id"],
            log_level=self.log_level,
            # Pass None (not []) when there are no actors so the downstream
            # helper's truthiness check takes the no-actors branch — matches
            # the strategy-only shape every pre-kronos runner has used.
            actors=actor_configs if actor_configs else None,
        )

    def main(self) -> None:
        """Build the config and block on a running :class:`TradingNode`.

        Delegates to :func:`run_paper_trade`, which installs the SIGINT /
        SIGTERM handlers and validates Binance Testnet credentials before
        booting the node.
        """
        run_paper_trade(self.build_config())
