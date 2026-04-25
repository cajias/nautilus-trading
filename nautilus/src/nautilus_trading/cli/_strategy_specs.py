"""StrategySpec registry — unified home for strategy + actor wiring.

Each :class:`StrategySpec` captures everything a generic runner needs to
attach a strategy to a node:

- the name (CLI / YAML key)
- the config builder (parsed args → strategy_config dict)
- the strategy + config import paths (for ``ImportableStrategyConfig``)
- zero or more :class:`ActorSpec` s (for strategies that depend on sibling
  actors, e.g. Kronos's inference actor)

The 8 non-kronos specs have ``actor_specs == ()``. The kronos spec carries
a single :class:`ActorSpec` pointing at ``KronosActor`` + ``KronosActorConfig``.

The dict :data:`STRATEGY_BUILDERS` is a derived projection
``{name: spec.builder for name, spec in STRATEGY_SPECS.items()}``, kept
because :mod:`nautilus_trading.backtest.runner` still consumes it for
strategy-name → builder lookup. New code should consume :data:`STRATEGY_SPECS`
directly so it picks up the strategy + config import paths and any actor
wiring at the same time.

Design note: ``StrategySpec`` + ``ActorSpec`` are frozen dataclasses so they
can be hashed and stored in sets. ``actor_specs`` is a ``tuple`` (not a
list) to keep the frozen instance hashable without a custom ``__hash__``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

# ---------------------------------------------------------------------------
# Protocols
# ---------------------------------------------------------------------------


class StrategyConfigBuilder(Protocol):
    """Builds a strategy_config dict from parsed CLI / YAML args."""

    def build(self, args: dict[str, Any]) -> dict[str, Any]: ...


class ActorConfigBuilder(Protocol):
    """Builds an actor_config dict from parsed CLI / YAML args."""

    def build(self, args: dict[str, Any]) -> dict[str, Any]: ...


# ---------------------------------------------------------------------------
# Strategy config builders
# ---------------------------------------------------------------------------


_BASE_FIELDS = ("instrument_id", "bar_type")


def _base(args: dict[str, Any], *, include_trade_size: bool = True) -> dict[str, Any]:
    """Pick the shared base fields (``instrument_id``, ``bar_type``, and
    optionally ``trade_size``) out of ``args`` into a fresh dict.

    Raises
    ------
    ValueError
        If any required field is missing or empty. Using ``ValueError`` (not
        ``KeyError``) gives uniform failure semantics across all 9 builders —
        the CLI can map any ``ValueError`` from ``builder.build(...)`` to a
        ``typer.BadParameter`` without having to special-case ``KeyError`` too.
    """
    required: tuple[str, ...] = _BASE_FIELDS + (("trade_size",) if include_trade_size else ())
    missing = [f for f in required if not args.get(f)]
    if missing:
        raise ValueError(
            f"strategy config requires {', '.join(required)}; missing: {', '.join(missing)}"
        )
    out = {k: args[k] for k in _BASE_FIELDS}
    if include_trade_size:
        out["trade_size"] = args["trade_size"]
    return out


class GridBotConfigBuilder:
    def build(self, args: dict[str, Any]) -> dict[str, Any]:
        if not args.get("upper_price") or not args.get("lower_price"):
            raise ValueError("grid_bot requires --upper-price and --lower-price")
        if args.get("grid_levels") is None:
            raise ValueError("grid_bot requires grid_levels")
        out = _base(args)  # ValueError on missing instrument_id/bar_type/trade_size
        out["upper_price"] = args["upper_price"]
        out["lower_price"] = args["lower_price"]
        out["grid_levels"] = args["grid_levels"]
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
        if not args.get("slow_ema") or not args.get("fast_ema"):
            raise ValueError("ema_cross requires slow_ema and fast_ema")
        out = _base(args)
        out["ema_period"] = args["slow_ema"]
        out["fast_ema_period"] = args["fast_ema"]
        out["slow_ema_period"] = args["slow_ema"]
        return out


class TimesFMConfigBuilder:
    """TimesFM swing: uses ema_period + fallback_fast_ema_period (no fast_ema_period)."""

    def build(self, args: dict[str, Any]) -> dict[str, Any]:
        if not args.get("slow_ema") or not args.get("fast_ema"):
            raise ValueError("timesfm_swing requires slow_ema and fast_ema")
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


class TimesFMGridConfigBuilder:
    """TimesFM quantile grid: base fields only — all ML/grid params have Config defaults."""

    def build(self, args: dict[str, Any]) -> dict[str, Any]:
        return _base(args)


class RVSSwingConfigBuilder:
    """RVS swing: base fields only — anomaly/stop/EMA thresholds all have Config defaults."""

    def build(self, args: dict[str, Any]) -> dict[str, Any]:
        return _base(args)


class ShockGuardConfigBuilder:
    """Shock Guard macro allocator: base fields only — all allocation/shock params default."""

    def build(self, args: dict[str, Any]) -> dict[str, Any]:
        return _base(args)


class KronosConfigBuilder:
    """Kronos strategy config: base fields only.

    ML hyperparameters (model_size, n_samples, forecast_horizon,
    inference_interval_bars) live on the sibling ``KronosActorConfig`` — see
    ``KronosActorConfigBuilder`` below. ``KronosStrategyConfig`` accepts only
    the instrument_id / bar_type / trade_size triple, matching the shape pinned
    in ``strategies/crypto/kronos/paper_runner.py``.
    """

    def build(self, args: dict[str, Any]) -> dict[str, Any]:
        return _base(args)


# ---------------------------------------------------------------------------
# Actor config builders
# ---------------------------------------------------------------------------


class KronosActorConfigBuilder:
    """Build a ``KronosActorConfig`` kwargs dict from parsed args.

    Defaults mirror the quarantined ``paper_trade.py`` snapshot values pinned
    in ``strategies/crypto/kronos/paper_runner.py`` (model_size="mini",
    n_samples=10, forecast_horizon=24, inference_interval_bars=4). Callers
    can override any field by supplying it in ``args``.
    """

    _DEFAULTS: dict[str, Any] = {
        "model_size": "mini",
        "n_samples": 10,
        "forecast_horizon": 24,
        "inference_interval_bars": 4,
    }

    def build(self, args: dict[str, Any]) -> dict[str, Any]:
        # _base() validates instrument_id + bar_type and raises ValueError on
        # missing — same uniform failure mode as every strategy-config builder.
        out = _base(args, include_trade_size=False)
        for key, default in self._DEFAULTS.items():
            out[key] = args.get(key, default)
        return out


# ---------------------------------------------------------------------------
# Spec dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ActorSpec:
    """Wire-description for an actor attached to a strategy.

    Attributes
    ----------
    actor_path
        Import path for the Actor class, e.g.
        ``"strategies.crypto.kronos.actor:KronosActor"``. Consumed by
        ``ImportableActorConfig``.
    config_path
        Import path for the Actor's config class, e.g.
        ``"strategies.crypto.kronos.actor:KronosActorConfig"``.
    builder
        Maps parsed CLI / YAML args → the actor_config dict passed to
        ``ImportableActorConfig.config``.
    """

    actor_path: str
    config_path: str
    builder: ActorConfigBuilder


@dataclass(frozen=True)
class StrategySpec:
    """Wire-description for a strategy the generic runners can attach.

    Attributes
    ----------
    name
        CLI / YAML key (``"grid_bot"``, ``"kronos"``, ...).
    builder
        Maps parsed args → strategy_config dict for ``ImportableStrategyConfig``.
    strategy_path
        Import path for the Strategy class.
    config_path
        Import path for the Strategy's config class.
    actor_specs
        Zero or more ``ActorSpec``s to attach before the strategy. Empty tuple
        for all non-kronos strategies shipped in sub-project A; kronos carries
        a single entry for ``KronosActor``. A tuple (not a list) keeps the
        frozen instance hashable without a custom ``__hash__``.
    """

    name: str
    builder: StrategyConfigBuilder
    strategy_path: str
    config_path: str
    actor_specs: tuple[ActorSpec, ...] = ()


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


_KRONOS_ACTOR_SPEC = ActorSpec(
    actor_path="strategies.crypto.kronos.actor:KronosActor",
    config_path="strategies.crypto.kronos.actor:KronosActorConfig",
    builder=KronosActorConfigBuilder(),
)


STRATEGY_SPECS: dict[str, StrategySpec] = {
    "grid_bot": StrategySpec(
        name="grid_bot",
        builder=GridBotConfigBuilder(),
        strategy_path="strategies.crypto.grid_bot:GridBotStrategy",
        config_path="strategies.crypto.grid_bot:GridBotConfig",
    ),
    "dca_bot": StrategySpec(
        name="dca_bot",
        builder=DCABotConfigBuilder(),
        strategy_path="strategies.crypto.dca_bot:DCABotStrategy",
        config_path="strategies.crypto.dca_bot:DCABotConfig",
    ),
    "ema_cross": StrategySpec(
        name="ema_cross",
        builder=EMAConfigBuilder(),
        strategy_path="strategies.forex.ema_cross:EMACrossStrategy",
        config_path="strategies.forex.ema_cross:EMACrossConfig",
    ),
    "timesfm_swing": StrategySpec(
        name="timesfm_swing",
        builder=TimesFMConfigBuilder(),
        strategy_path="strategies.crypto.timesfm_swing:TimesFMSwingStrategy",
        config_path="strategies.crypto.timesfm_swing:TimesFMSwingConfig",
    ),
    "hybrid_sma_r10": StrategySpec(
        name="hybrid_sma_r10",
        builder=HybridSMAConfigBuilder(),
        strategy_path="strategies.crypto.hybrid_sma_r10:HybridSMAR10Strategy",
        config_path="strategies.crypto.hybrid_sma_r10:HybridSMAR10Config",
    ),
    "timesfm_grid": StrategySpec(
        name="timesfm_grid",
        builder=TimesFMGridConfigBuilder(),
        strategy_path="strategies.crypto.timesfm_grid:TimesFMGridStrategy",
        config_path="strategies.crypto.timesfm_grid:TimesFMGridConfig",
    ),
    "rvs_swing": StrategySpec(
        name="rvs_swing",
        builder=RVSSwingConfigBuilder(),
        strategy_path="strategies.crypto.rvs_swing:RVSSwingStrategy",
        config_path="strategies.crypto.rvs_swing:RVSSwingConfig",
    ),
    "shock_guard": StrategySpec(
        name="shock_guard",
        builder=ShockGuardConfigBuilder(),
        strategy_path="strategies.crypto.shock_guard:ShockGuardStrategy",
        config_path="strategies.crypto.shock_guard:ShockGuardConfig",
    ),
    "kronos": StrategySpec(
        name="kronos",
        builder=KronosConfigBuilder(),
        strategy_path="strategies.crypto.kronos.strategy:KronosStrategy",
        config_path="strategies.crypto.kronos.strategy:KronosStrategyConfig",
        actor_specs=(_KRONOS_ACTOR_SPEC,),
    ),
}


# Derived name → builder projection. ``backtest/runner.py`` still consumes
# this for strategy-name → builder lookup; new code should consume
# ``STRATEGY_SPECS`` directly so it picks up the import paths + actor wiring
# at the same time.
STRATEGY_BUILDERS: dict[str, StrategyConfigBuilder] = {
    name: spec.builder for name, spec in STRATEGY_SPECS.items()
}
