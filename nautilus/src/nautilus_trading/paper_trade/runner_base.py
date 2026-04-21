"""PaperTradeRunner ABC — parallel to BacktestRunner."""

from __future__ import annotations

from abc import ABC, abstractmethod


class PaperTradeRunner(ABC):
    """Base class for Binance Spot Testnet paper-trade runners.

    Each concrete subclass composes its own TradingNode, subscribes to data,
    attaches one strategy (plus optional actor), and runs to completion.
    No default main() body — see sub-project A PR #16 for the rationale.
    """

    @abstractmethod
    def main(self) -> None:
        """Compose TradingNode, subscribe data, add strategy, run."""
