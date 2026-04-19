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
            raise ValueError("grid_bot requires --upper-price and --lower-price")
        return {
            "instrument_id": args["instrument_id"],
            "bar_type": args["bar_type"],
            "trade_size": args["trade_size"],
            "upper_price": args["upper_price"],
            "lower_price": args["lower_price"],
            "grid_levels": args["grid_levels"],
        }


_BASE_FIELDS = ("instrument_id", "bar_type")


def _base(args: dict[str, Any], *, include_trade_size: bool = True) -> dict[str, Any]:
    out = {k: args[k] for k in _BASE_FIELDS}
    if include_trade_size:
        out["trade_size"] = args["trade_size"]
    return out


class DCABotConfigBuilder:
    def build(self, args: dict[str, Any]) -> dict[str, Any]:
        if not args.get("buy_interval_bars"):
            raise ValueError("dca_bot requires buy_interval_bars")
        out = _base(args)
        if args.get("buy_amount"):
            out["buy_amount"] = args["buy_amount"]
        out["buy_interval_bars"] = args["buy_interval_bars"]
        return out


class EMAConfigBuilder:
    """EMA cross / swing strategies that need both slow and fast EMA periods."""

    def build(self, args: dict[str, Any]) -> dict[str, Any]:
        out = _base(args)
        out["ema_period"] = args["slow_ema"]
        out["fast_ema_period"] = args["fast_ema"]
        out["slow_ema_period"] = args["slow_ema"]
        return out


class TimesFMConfigBuilder:
    """TimesFM swing: uses ema_period + fallback_fast_ema_period (no fast_ema_period)."""

    def build(self, args: dict[str, Any]) -> dict[str, Any]:
        out = _base(args)
        out["ema_period"] = args["slow_ema"]
        out["fallback_fast_ema_period"] = args["fast_ema"]
        return out


class HybridSMAConfigBuilder:
    """Hybrid SMA ensemble: sizes from equity, so NO trade_size. Decimal fields as strings."""

    def build(self, args: dict[str, Any]) -> dict[str, Any]:
        if not args.get("sma_fast") or not args.get("sma_slow"):
            raise ValueError("hybrid_sma_r10 requires sma_fast and sma_slow")
        if args.get("stop_fast") is None or args.get("stop_slow") is None:
            raise ValueError("hybrid_sma_r10 requires stop_fast and stop_slow")
        out = _base(args, include_trade_size=False)
        out["sma_fast"] = args["sma_fast"]
        out["sma_slow"] = args["sma_slow"]
        out["stop_fast"] = str(args["stop_fast"])
        out["stop_slow"] = str(args["stop_slow"])
        return out


STRATEGY_BUILDERS: dict[str, StrategyConfigBuilder] = {
    "grid_bot": GridBotConfigBuilder(),
    "dca_bot": DCABotConfigBuilder(),
    "ema_cross": EMAConfigBuilder(),
    "timesfm_swing": TimesFMConfigBuilder(),
    "hybrid_sma_r10": HybridSMAConfigBuilder(),
}
