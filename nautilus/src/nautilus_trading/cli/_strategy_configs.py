"""Protocol-based strategy config builders.

Each builder maps CLI args → the strategy_config dict passed to
ImportableStrategyConfig. New strategies add a class and an entry in
STRATEGY_BUILDERS; no CLI editing needed.
"""

from __future__ import annotations

from typing import Any, Protocol


class StrategyConfigBuilder(Protocol):
    """Builds a strategy_config dict from parsed CLI args."""

    def build(self, args: dict[str, Any]) -> dict[str, Any]:
        ...


class GridBotConfigBuilder:
    def build(self, args: dict[str, Any]) -> dict[str, Any]:
        if not args.get("upper_price") or not args.get("lower_price"):
            raise ValueError("grid_bot requires upper_price and lower_price")
        return {
            "instrument_id": args["instrument_id"],
            "bar_type": args["bar_type"],
            "trade_size": args["trade_size"],
            "upper_price": args["upper_price"],
            "lower_price": args["lower_price"],
            "grid_levels": args["grid_levels"],
        }


# Populated by subsequent tasks.
STRATEGY_BUILDERS: dict[str, StrategyConfigBuilder] = {
    "grid_bot": GridBotConfigBuilder(),
}
