"""PaperTradeStrategyRunner — generic Binance Spot Testnet runner.

A single concrete runner class parameterized by a
:class:`~nautilus_trading.cli._strategy_specs.StrategySpec` + a params dict.
Replaces the 8 ``*_paper.py`` shims and
``strategies/crypto/kronos/paper_runner.py`` (deleted in the same PR) and
the ``PaperTradeRunner`` ABC they all subclassed (also deleted in this PR —
a sole concrete runner driven by the spec registry has no ABC to inherit).

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
provider, account/environment). Booting the resulting
:class:`TradingNodeConfig` is the CLI's job — see ``cli/paper_trade.py``,
which calls :func:`run_paper_trade` directly with the config emitted by
:meth:`PaperTradeStrategyRunner.build_config`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from nautilus_trader.config import ImportableActorConfig, TradingNodeConfig

from nautilus_trading.cli._strategy_specs import StrategySpec
from nautilus_trading.paper_trade.node_config import build_paper_trade_node_config


@dataclass
class PaperTradeStrategyRunner:
    """Spec-driven paper-trade runner.

    Parameters
    ----------
    spec : StrategySpec
        Describes the strategy to attach (name, builder, import paths) plus
        any sibling actors. Pulled from
        :data:`~nautilus_trading.cli._strategy_specs.STRATEGY_SPECS`.
    params : dict[str, Any]
        Per-run arguments. Must include ``instrument_id`` and ``bar_type``,
        and — when the spec's builder consumes it — ``trade_size`` (most
        strategies require it; ``hybrid_sma_r10`` is the exception, sizing
        from equity). Plus any strategy-specific fields the builder
        requires. Feeds both the strategy builder and every actor builder —
        a single source of truth for run inputs.
    log_level : str, default "INFO"
        Forwarded to the node's :class:`LoggingConfig`.
    """

    spec: StrategySpec
    params: dict[str, Any]
    log_level: str = "INFO"

    def build_config(self) -> TradingNodeConfig:
        """Build the :class:`TradingNodeConfig`.

        Returning a config (rather than booting a :class:`TradingNode`) lets
        unit tests assert on the static config shape and lets the CLI reuse
        the eager-validated config for both error mapping and the actual
        boot — see ``cli/paper_trade.py``, which calls
        :func:`~nautilus_trading.paper_trade.node_config.run_paper_trade`
        with the result.

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
            # Pass actor configs directly. Empty list = strategy-only;
            # non-empty preserves spec order so actors attach before the
            # strategy. ``build_paper_trade_node_config`` normalizes a falsy
            # value into ``[]`` either way.
            actors=actor_configs,
        )
