"""Template submission for the R11+ competition contract.

Trivial always-flat reference strategy. It subscribes to bars and logs them,
but never places an order. Its purpose is to demonstrate the minimum shape
of a valid submission:

    - Module-level ``MANIFEST`` with all six required keys
    - ``StrategyConfig`` subclass with ``frozen=True``
    - ``Strategy`` subclass with the standard lifecycle methods

Copy this directory as the starting point for a real submission, then:

    1. Rename the classes (``TemplateStrategy`` / ``TemplateConfig``) to
       match your strategy.
    2. Update ``MANIFEST`` with the new class names and any default config
       kwargs you want the evaluator to use.
    3. Implement your signal logic in ``on_bar``.
    4. Replace ``tests/test_strategy.py`` with real behavioural tests.
    5. Fill in ``research/notes.md`` with your rationale and backtest
       evidence.

See ``competition/COMPETITION.md`` for the full contract and
``strategies/crypto/hybrid_sma_r10.py`` for a non-trivial reference port.
"""

from __future__ import annotations

from typing import Any

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.trading.strategy import Strategy


class TemplateConfig(StrategyConfig, frozen=True):
    """Configuration for the template strategy.

    Only declares the two fields every submission needs: the instrument to
    trade and the bar type to subscribe to. Real submissions extend this
    with their own parameters (SMA periods, stop-loss percentages, etc.).
    """

    instrument_id: InstrumentId
    bar_type: BarType


class TemplateStrategy(Strategy):
    """Always-flat reference strategy.

    Subscribes to the configured bar type, logs each bar it receives, and
    never places an order. Useful as a validator fixture and as a minimal
    runnable starting point for agents building their first R11+ submission.
    """

    def __init__(self, config: TemplateConfig) -> None:
        super().__init__(config)
        self.instrument: Instrument | None = None

    def on_start(self) -> None:
        self.instrument = self.cache.instrument(self.config.instrument_id)
        if self.instrument is None:
            self.log.error(
                f"Could not find instrument for {self.config.instrument_id}",
            )
            self.stop()
            return

        self.subscribe_bars(self.config.bar_type)
        self.log.info("Template strategy started")

    def on_bar(self, bar: Bar) -> None:
        # No trading — just observe and log. Real strategies replace this
        # with their signal logic and ``self.submit_order(...)`` calls.
        self.log.info(f"Bar: {bar}")

    def on_stop(self) -> None:
        self.log.info("Template strategy stopped")


# Module-level manifest consumed by the competition validator and evaluator.
# All six keys are required. See competition/COMPETITION.md for the spec.
MANIFEST: dict[str, Any] = {
    "strategy_class_name": "TemplateStrategy",
    "config_class_name": "TemplateConfig",
    "instrument_id": "BNBUSDT.BINANCE",
    "bar_type": "BNBUSDT.BINANCE-1-HOUR-LAST-EXTERNAL",
    "default_config": {},
    "description": (
        "Trivial always-flat template showing the minimum submission shape"
    ),
}
