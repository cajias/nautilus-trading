"""KronosPaperTradeRunner — Binance Spot Testnet driver for KronosStrategy.

Unlike the other PaperTradeRunner subclasses shipped in PRs 3-5, the Kronos
strategy depends on a sibling actor: ``KronosActor`` subscribes to live bars,
runs model inference, and publishes ``KronosSignal``s on the message bus which
``KronosStrategy`` subscribes to. Both are wired declaratively here via
``ImportableActorConfig`` + ``ImportableStrategyConfig`` so the node can be
rebuilt in-process (for parity tests) without booting a TradingNode.

Default hyperparameters (``model_size="mini"``, ``n_samples=10``,
``forecast_horizon=24``, ``inference_interval_bars=4``) mirror the quarantined
``paper_trade.py`` script they replace, pinned in the parity-gate snapshot at
``tests/strategies/crypto/kronos/_quarantined_config_snapshot.py``.
"""

from __future__ import annotations

from dataclasses import dataclass

from nautilus_trader.config import ImportableActorConfig, TradingNodeConfig

from nautilus_trading.paper_trade.node_config import build_paper_trade_node_config, run_paper_trade
from nautilus_trading.paper_trade.runner_base import PaperTradeRunner


@dataclass
class KronosPaperTradeRunner(PaperTradeRunner):
    """Drive KronosStrategy + KronosActor against Binance Spot Testnet.

    Composition-only: builds a TradingNodeConfig that attaches both the actor
    (inference/signal producer) and the strategy (signal consumer/order router)
    and hands it to ``run_paper_trade``. ``build_config`` is separated from
    ``main`` so the parity gate can assert shape without booting a TradingNode.
    """

    instrument_id: str
    bar_type: str
    trade_size: str
    model_size: str = "mini"
    n_samples: int = 10
    forecast_horizon: int = 24
    inference_interval_bars: int = 4
    log_level: str = "INFO"

    def build_config(self) -> TradingNodeConfig:
        """Build the TradingNodeConfig. Separated from ``main()`` for testability."""
        actor = ImportableActorConfig(
            actor_path="strategies.crypto.kronos.actor:KronosActor",
            config_path="strategies.crypto.kronos.actor:KronosActorConfig",
            config={
                "instrument_id": self.instrument_id,
                "bar_type": self.bar_type,
                "model_size": self.model_size,
                "forecast_horizon": self.forecast_horizon,
                "inference_interval_bars": self.inference_interval_bars,
                "n_samples": self.n_samples,
            },
        )
        return build_paper_trade_node_config(
            strategy_path="strategies.crypto.kronos.strategy:KronosStrategy",
            config_path="strategies.crypto.kronos.strategy:KronosStrategyConfig",
            strategy_config={
                "instrument_id": self.instrument_id,
                "bar_type": self.bar_type,
                "trade_size": self.trade_size,
            },
            instrument_id=self.instrument_id,
            log_level=self.log_level,
            actors=[actor],
        )

    def main(self) -> None:
        run_paper_trade(self.build_config())
