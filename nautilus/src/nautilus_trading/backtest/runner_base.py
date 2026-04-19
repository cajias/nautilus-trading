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

    def main(self) -> None:
        """Default composition: build → create engine → add data → run → print.

        Subclasses may override this when engine creation needs type-aware venue
        wiring (see KronosBacktestRunner for an example). The default is
        sufficient for dict-config subclasses that don't need type wrapping.
        """
        from nautilus_trader.backtest.engine import BacktestEngine

        config = self.build_config()
        engine_cfg = config.get("engine_cfg") if isinstance(config, dict) else None
        engine = BacktestEngine(config=engine_cfg) if engine_cfg else BacktestEngine()
        venue = config.get("venue") if isinstance(config, dict) else None
        if venue is not None:
            engine.add_venue(**venue.__dict__)
        self.add_data(engine, config)
        results = self.run(engine)
        self.print_results(results)
