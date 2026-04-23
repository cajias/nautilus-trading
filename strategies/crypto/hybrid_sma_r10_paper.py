"""HybridSMAR10PaperTradeRunner — Binance Spot Testnet driver for HybridSMAR10Strategy.

Unlike the simple directional runners (grid_bot / dca_bot / timesfm_swing /
ema_cross), HybridSMA sizes each sub-strategy vote from *equity* rather than
a fixed per-trade quantity. As a result this runner intentionally does NOT
declare a `trade_size` field, and the underlying HybridSMAConfigBuilder omits
it from the strategy config dict.
"""

from __future__ import annotations

from dataclasses import dataclass

from nautilus_trader.config import TradingNodeConfig

from nautilus_trading.cli._strategy_configs import STRATEGY_BUILDERS
from nautilus_trading.paper_trade.node_config import build_paper_trade_node_config, run_paper_trade
from nautilus_trading.paper_trade.runner_base import PaperTradeRunner


@dataclass
class HybridSMAR10PaperTradeRunner(PaperTradeRunner):
    """Drive HybridSMAR10Strategy against Binance Spot Testnet.

    Composition-only: builds a TradingNodeConfig from STRATEGY_BUILDERS
    and hands it to `run_paper_trade`. `build_config` is separated from
    `main` so unit tests can assert shape without booting a TradingNode.

    Note: `stop_fast` / `stop_slow` are kept as strings to match the
    builder's `str(...)` coercion and avoid Decimal/float round-trip drift.
    """

    instrument_id: str
    bar_type: str
    sma_fast: int
    sma_slow: int
    stop_fast: str
    stop_slow: str
    log_level: str = "INFO"

    # trade_size intentionally absent — HybridSMA sizes from equity, not per-trade.

    def build_config(self) -> TradingNodeConfig:
        """Build the TradingNodeConfig. Separated from main() for testability."""
        builder = STRATEGY_BUILDERS["hybrid_sma_r10"]
        builder_args: dict[str, object] = {
            "instrument_id": self.instrument_id,
            "bar_type": self.bar_type,
            "sma_fast": self.sma_fast,
            "sma_slow": self.sma_slow,
            "stop_fast": self.stop_fast,
            "stop_slow": self.stop_slow,
        }
        strategy_config = builder.build(builder_args)
        return build_paper_trade_node_config(
            strategy_path="strategies.crypto.hybrid_sma_r10:HybridSMAR10Strategy",
            config_path="strategies.crypto.hybrid_sma_r10:HybridSMAR10Config",
            strategy_config=strategy_config,
            instrument_id=self.instrument_id,
            log_level=self.log_level,
        )

    def main(self) -> None:
        run_paper_trade(self.build_config())
