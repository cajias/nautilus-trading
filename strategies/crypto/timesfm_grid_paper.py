"""TimesFMGridPaperTradeRunner — Binance Spot Testnet driver for TimesFMGridStrategy."""

from __future__ import annotations

from dataclasses import dataclass

from nautilus_trader.config import TradingNodeConfig

from nautilus_trading.cli._strategy_configs import STRATEGY_BUILDERS
from nautilus_trading.paper_trade.node_config import build_paper_trade_node_config, run_paper_trade
from nautilus_trading.paper_trade.runner_base import PaperTradeRunner


@dataclass
class TimesFMGridPaperTradeRunner(PaperTradeRunner):
    """Drive TimesFMGridStrategy against Binance Spot Testnet.

    Composition-only: builds a TradingNodeConfig from STRATEGY_BUILDERS
    and hands it to `run_paper_trade`. `build_config` is separated from
    `main` so unit tests can assert shape without booting a TradingNode
    (and without loading the TimesFM checkpoint — strategy instantiation
    is deferred until the TradingNode boots inside `run_paper_trade`).

    Note: TimesFMGrid accepts only the base fields — all ML/grid/stop
    parameters have defaults on `TimesFMGridConfig` so no additional
    runner fields are declared here.
    """

    instrument_id: str
    bar_type: str
    trade_size: str
    log_level: str = "INFO"

    def build_config(self) -> TradingNodeConfig:
        """Build the TradingNodeConfig. Separated from main() for testability."""
        builder = STRATEGY_BUILDERS["timesfm_grid"]
        builder_args: dict[str, object] = {
            "instrument_id": self.instrument_id,
            "bar_type": self.bar_type,
            "trade_size": self.trade_size,
        }
        strategy_config = builder.build(builder_args)
        return build_paper_trade_node_config(
            strategy_path="strategies.crypto.timesfm_grid:TimesFMGridStrategy",
            config_path="strategies.crypto.timesfm_grid:TimesFMGridConfig",
            strategy_config=strategy_config,
            instrument_id=self.instrument_id,
            log_level=self.log_level,
        )

    def main(self) -> None:
        run_paper_trade(self.build_config())
