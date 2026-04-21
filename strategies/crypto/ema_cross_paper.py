"""EMACrossPaperTradeRunner — Binance Spot Testnet driver for EMACrossStrategy."""

from __future__ import annotations

from dataclasses import dataclass

from nautilus_trader.config import TradingNodeConfig

from nautilus_trading.cli._strategy_configs import STRATEGY_BUILDERS
from nautilus_trading.paper_trade.node_config import build_paper_trade_node_config, run_paper_trade
from nautilus_trading.paper_trade.runner_base import PaperTradeRunner


@dataclass
class EMACrossPaperTradeRunner(PaperTradeRunner):
    """Drive EMACrossStrategy against Binance Spot Testnet.

    Composition-only: builds a TradingNodeConfig from STRATEGY_BUILDERS
    and hands it to `run_paper_trade`. `build_config` is separated from
    `main` so unit tests can assert shape without booting a TradingNode.
    """

    instrument_id: str
    bar_type: str
    trade_size: str
    fast_ema: int = 10
    slow_ema: int = 20
    log_level: str = "INFO"

    def build_config(self) -> TradingNodeConfig:
        """Build the TradingNodeConfig. Separated from main() for testability."""
        builder = STRATEGY_BUILDERS["ema_cross"]
        builder_args: dict[str, object] = {
            "instrument_id": self.instrument_id,
            "bar_type": self.bar_type,
            "trade_size": self.trade_size,
            "fast_ema": self.fast_ema,
            "slow_ema": self.slow_ema,
        }
        strategy_config = builder.build(builder_args)
        return build_paper_trade_node_config(
            strategy_path="strategies.forex.ema_cross:EMACrossStrategy",
            config_path="strategies.forex.ema_cross:EMACrossConfig",
            strategy_config=strategy_config,
            instrument_id=self.instrument_id,
            log_level=self.log_level,
        )

    def main(self) -> None:
        run_paper_trade(self.build_config())
