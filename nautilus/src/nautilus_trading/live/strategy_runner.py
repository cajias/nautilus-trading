"""LiveStrategyRunner — Binance PROD scaffold (NOT IMPLEMENTED).

Real-money execution is out of scope per the 2026-04-21 no-real-money
directive. This runner exists so the third member of the
``{Backtest, PaperTrade, Live}StrategyRunner`` family has a structural
template parallel to its siblings, and so the
``nt {backtest, paper-trade, live}`` CLI surface is complete.

Contract
--------
* :meth:`build_config` returns a valid :class:`TradingNodeConfig` pointed at
  Binance PROD. This is allowed — a future real-money implementer can fill
  in :meth:`main` without reshaping any caller. Failure happens at boot,
  not at config-validation time.
* :meth:`main` raises :class:`NotImplementedError` with an explicit message
  referencing the 2026-04-21 directive. This is the contract gate.

Signal-flow ordering matches the paper-trade runner: actor configs are
built before the strategy config and land in ``config.actors``, which
NautilusTrader starts before anything in ``config.strategies`` at node
boot. Preserves the Kronos contract documented in
``strategies/crypto/kronos/strategy.py`` (actor publishes signals first,
strategy consumes them) for the day this scaffold gets implemented.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from nautilus_trader.config import ImportableActorConfig, TradingNodeConfig

from nautilus_trading.cli._strategy_specs import StrategySpec
from nautilus_trading.live.node_config import build_live_node_config


@dataclass
class LiveStrategyRunner:
    """Spec-driven Binance PROD runner — scaffold only.

    Parameters
    ----------
    spec : StrategySpec
        Describes the strategy to attach (name, builder, import paths) plus
        any sibling actors. Pulled from
        :data:`~nautilus_trading.cli._strategy_specs.STRATEGY_SPECS`.
    params : dict[str, Any]
        Per-run arguments forwarded to every builder. Same shape as the
        paper-trade and backtest runners — single source of truth.
    log_level : str, default "INFO"
        Forwarded to the node's :class:`LoggingConfig`.
    """

    spec: StrategySpec
    params: dict[str, Any]
    log_level: str = "INFO"

    def build_config(self) -> TradingNodeConfig:
        """Build the :class:`TradingNodeConfig` for the PROD scaffold.

        Allowed to succeed even though :meth:`main` refuses to boot — this
        keeps the shape symmetric with ``PaperTradeStrategyRunner`` and
        ``BacktestStrategyRunner`` so a future real-money implementer
        doesn't have to reshape any caller. Build order is actors →
        strategy, mirroring the paper-trade runner.
        """
        actor_configs: list[ImportableActorConfig] = [
            ImportableActorConfig(
                actor_path=actor_spec.actor_path,
                config_path=actor_spec.config_path,
                config=actor_spec.builder.build(self.params),
            )
            for actor_spec in self.spec.actor_specs
        ]

        strategy_config = self.spec.builder.build(self.params)

        return build_live_node_config(
            strategy_path=self.spec.strategy_path,
            config_path=self.spec.config_path,
            strategy_config=strategy_config,
            instrument_id=self.params["instrument_id"],
            log_level=self.log_level,
            actors=actor_configs,
        )

    def main(self) -> None:
        """Refuse to boot a real-money TradingNode.

        Raises
        ------
        NotImplementedError
            Always. Real-money trading is out of scope per the 2026-04-21
            no-real-money directive. When real-money work is in scope,
            implement booting via :class:`TradingNode` with Binance PROD
            credentials and an ``i_understand_real_money`` runtime guard.
        """
        raise NotImplementedError(
            "Real-money trading not in scope per the 2026-04-21 directive. "
            "LiveStrategyRunner exists as an architectural scaffold only. "
            "When real-money work is in scope, implement booting via TradingNode "
            "with Binance PROD credentials and the i_understand_real_money guard."
        )
