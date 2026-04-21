"""GridBotPaperTradeRunner — Binance Spot Testnet driver for GridBotStrategy."""

from __future__ import annotations

from dataclasses import dataclass

from nautilus_trader.config import TradingNodeConfig
from nautilus_trading.cli._strategy_configs import STRATEGY_BUILDERS
from nautilus_trading.paper_trade.node_config import build_paper_trade_node_config, run_paper_trade
from nautilus_trading.paper_trade.runner_base import PaperTradeRunner


@dataclass
class GridBotPaperTradeRunner(PaperTradeRunner):
    """Drive GridBotStrategy against Binance Spot Testnet.

    Composition-only: builds a TradingNodeConfig from STRATEGY_BUILDERS
    and hands it to `run_paper_trade`. `build_config` is separated from
    `main` so unit tests can assert shape without booting a TradingNode.
    """

    instrument_id: str
    bar_type: str
    trade_size: str
    upper_price: str
    lower_price: str
    grid_levels: int
    log_level: str = "INFO"

    def build_config(self) -> TradingNodeConfig:
        """Build the TradingNodeConfig. Separated from main() for testability."""
        builder = STRATEGY_BUILDERS["grid_bot"]
        builder_args: dict[str, object] = {
            "instrument_id": self.instrument_id,
            "bar_type": self.bar_type,
            "trade_size": self.trade_size,
            "upper_price": self.upper_price,
            "lower_price": self.lower_price,
            "grid_levels": self.grid_levels,
        }
        strategy_config = builder.build(builder_args)
        return build_paper_trade_node_config(
            strategy_path="strategies.crypto.grid_bot:GridBotStrategy",
            config_path="strategies.crypto.grid_bot:GridBotConfig",
            strategy_config=strategy_config,
            instrument_id=self.instrument_id,
            log_level=self.log_level,
        )

    def main(self) -> None:
        run_paper_trade(self.build_config())
