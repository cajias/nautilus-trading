"""DCABotPaperTradeRunner — Binance Spot Testnet driver for DCABotStrategy."""

from __future__ import annotations

from dataclasses import dataclass

from nautilus_trader.config import TradingNodeConfig

from nautilus_trading.cli._strategy_configs import STRATEGY_BUILDERS
from nautilus_trading.paper_trade.node_config import build_paper_trade_node_config, run_paper_trade
from nautilus_trading.paper_trade.runner_base import PaperTradeRunner


@dataclass
class DCABotPaperTradeRunner(PaperTradeRunner):
    """Drive DCABotStrategy against Binance Spot Testnet.

    Composition-only: builds a TradingNodeConfig from STRATEGY_BUILDERS
    and hands it to `run_paper_trade`. `build_config` is separated from
    `main` so unit tests can assert shape without booting a TradingNode.
    """

    instrument_id: str
    bar_type: str
    trade_size: str
    buy_interval_bars: int
    buy_amount: str | None = None
    log_level: str = "INFO"

    def build_config(self) -> TradingNodeConfig:
        """Build the TradingNodeConfig. Separated from main() for testability."""
        builder = STRATEGY_BUILDERS["dca_bot"]
        builder_args: dict[str, object] = {
            "instrument_id": self.instrument_id,
            "bar_type": self.bar_type,
            "trade_size": self.trade_size,
            "buy_interval_bars": self.buy_interval_bars,
        }
        if self.buy_amount is not None:
            builder_args["buy_amount"] = self.buy_amount
        strategy_config = builder.build(builder_args)
        return build_paper_trade_node_config(
            strategy_path="strategies.crypto.dca_bot:DCABotStrategy",
            config_path="strategies.crypto.dca_bot:DCABotConfig",
            strategy_config=strategy_config,
            instrument_id=self.instrument_id,
            log_level=self.log_level,
        )

    def main(self) -> None:
        run_paper_trade(self.build_config())
