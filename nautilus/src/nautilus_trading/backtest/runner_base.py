"""BacktestRunner abstract base — unifies the EMA and Kronos runner code paths."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BacktestRunner(ABC):
    """Common lifecycle for Nautilus backtest runners.

    Subclasses own the engine/venue/data wiring. Callers invoke:
        runner = ConcreteRunner(...)
        config = runner.build_config()
        engine = ...  # subclass creates & configures
        runner.add_data(engine, config)
        results = runner.run(engine)
        runner.print_results(results)
    """

    @abstractmethod
    def build_config(self) -> Any:
        """Return the BacktestRunConfig (or equivalent) for this runner."""

    @abstractmethod
    def add_data(self, engine: Any, config: Any) -> None:
        """Populate ``engine`` with instrument + bars from ``config``."""

    @abstractmethod
    def run(self, engine: Any) -> Any:
        """Execute the backtest on ``engine`` and return results."""

    @abstractmethod
    def print_results(self, results: Any) -> None:
        """Pretty-print results."""
