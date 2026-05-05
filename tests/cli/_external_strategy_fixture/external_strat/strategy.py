"""Minimal Strategy + Config classes for the synthetic external fixture.

These aren't intended to actually trade — they only satisfy import resolution
in the strategy_path / config_path strings on ``STRATEGY_SPEC``. The smoke
test never actually instantiates them; it only verifies that discovery +
listing works for an externally-installed package.
"""

from __future__ import annotations

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import BarType
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.trading.strategy import Strategy


class ExternalStratConfig(StrategyConfig, frozen=True):
    """Minimal frozen config — mirrors the in-repo strategy config shape."""

    instrument_id: InstrumentId
    bar_type: BarType


class ExternalStratStrategy(Strategy):
    """Minimal Strategy subclass — does nothing, exists only to be importable."""

    def __init__(self, config: ExternalStratConfig) -> None:
        super().__init__(config)
