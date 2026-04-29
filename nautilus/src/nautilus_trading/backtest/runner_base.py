"""BacktestRunner abstract base — common lifecycle for backtest runners.

Sub-project B.5 PR 3 simplified ``add_data`` from
``add_data(self, engine, config)`` to ``add_data(self, engine)``. The
``config`` parameter was a holdover from ``KronosBacktestRunner``
(now retired), which used it to pass an ``instrument`` between
``build_config`` and ``add_data``. The current consumers
(``BacktestStrategyRunner``, ``EMABacktestRunner``) read everything
they need from ``self`` (run_config, data_source, etc.), so the
parameter was unused — removing it makes the interface honest.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BacktestRunner(ABC):
    """Common lifecycle for Nautilus backtest runners.

    Subclasses own the engine/venue/data wiring. Callers invoke::

        runner = ConcreteRunner(...)
        runner.main()  # build_config → add_data → run → print_results

    Or, for finer control (notebooks, custom orchestration)::

        runner = ConcreteRunner(...)
        config = runner.build_config()
        engine = ...  # subclass creates & configures
        runner.add_data(engine)
        results = runner.run(engine)
        runner.print_results(results)
    """

    @abstractmethod
    def build_config(self) -> Any:
        """Return the BacktestRunConfig (or equivalent) for this runner."""

    @abstractmethod
    def add_data(self, engine: Any) -> None:
        """Populate ``engine`` with instrument + bars.

        Subclasses read whatever they need (run_config, data source,
        catalog) from ``self`` — no ``config`` parameter is passed in.
        """

    @abstractmethod
    def run(self, engine: Any) -> Any:
        """Execute the backtest on ``engine`` and return results."""

    @abstractmethod
    def print_results(self, results: Any) -> None:
        """Pretty-print results."""

    @abstractmethod
    def main(self) -> None:
        """Run the backtest end-to-end.

        Subclasses own engine construction, venue/Money wrapping, data
        loading, execution, and result printing. No default composition
        — Option D (see PR #16 Copilot thread). Concrete subclasses
        (``EMABacktestRunner``, ``BacktestStrategyRunner``) provide
        their own ``main()``.
        """
