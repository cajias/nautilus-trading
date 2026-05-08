"""StrategySpec registry — internal discovery glue.

The public ``StrategySpec`` / ``ActorSpec`` / Protocol surface lives in
:mod:`nautilus_trading.specs`. Plugin authors (in-repo strategies under
``strategies/`` and any third-party packages registering on the
``nautilus_trading.strategies`` entry-point group) should import from
``nautilus_trading.specs``. This module imports those names back so any
existing call site that still does
``from nautilus_trading.cli._strategy_specs import StrategySpec`` keeps
working as a backwards-compat shim, but the canonical home is
:mod:`nautilus_trading.specs`.

What stays here:

- Concrete ``*ConfigBuilder`` classes used by in-repo strategies (each
  encodes the args-validation contract of one strategy's config).
- ``_discover_strategy_specs`` — the entry-point-walking discovery function.
- ``get_strategy_specs`` / ``get_strategy_builders`` — :func:`functools.cache`
  -decorated lazy accessors. The cache is process-lifetime; tests that need
  a fresh registry should call ``clear_strategy_caches()`` (clears both
  accessors at once, since clearing only one leaves the other stale).
- ``STRATEGY_SPECS`` / ``STRATEGY_BUILDERS`` — module-level names served
  lazily through PEP 562 ``__getattr__`` so ``from ... import STRATEGY_SPECS``
  triggers discovery only on first reference, not at module import.

Design note: keeping ``importlib.metadata`` and ``_discover_strategy_specs``
in *this* module is load-bearing — :mod:`tests.cli.test_strategy_discovery`
patches ``nautilus_trading.cli._strategy_specs.importlib.metadata.entry_points``
and calls the underlying function directly. Moving either out would silently
break those tests.
"""

from __future__ import annotations

import functools
import importlib.metadata
import logging
from typing import Any

# Public dataclasses + Protocol are now re-exported from
# :mod:`nautilus_trading.specs`. Keep these imports so existing
# ``from nautilus_trading.cli._strategy_specs import StrategySpec, ...``
# call sites (in-repo + any third-party plugin pinned to the old path)
# continue to resolve. New code should import from ``nautilus_trading.specs``.
from nautilus_trading.specs import (
    ActorConfigBuilder,
    ActorSpec,
    StrategyConfigBuilder,
    StrategySpec,
)

# NOTE: ``STRATEGY_SPECS`` / ``STRATEGY_BUILDERS`` are intentionally NOT in
# ``__all__``. They are served lazily through PEP 562 ``__getattr__`` and
# ruff's F822 check ("undefined name in __all__") flags any name that isn't
# a real module-level binding. The accessors ``get_strategy_specs`` /
# ``get_strategy_builders`` are the canonical export.
__all__ = [
    "ActorConfigBuilder",
    "ActorSpec",
    "StrategyConfigBuilder",
    "StrategySpec",
    # Concrete builder classes — internal to discovery glue, exported for tests.
    "DCABotConfigBuilder",
    "EMAConfigBuilder",
    "GridBotConfigBuilder",
    "HybridSMAConfigBuilder",
    "KronosActorConfigBuilder",
    "KronosConfigBuilder",
    "RVSSwingConfigBuilder",
    "ShockGuardConfigBuilder",
    "TimesFMConfigBuilder",
    "TimesFMGridConfigBuilder",
    # Registry accessors.
    "_discover_strategy_specs",
    "clear_strategy_caches",
    "get_strategy_builders",
    "get_strategy_specs",
]

logger = logging.getLogger(__name__)


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
        if args.get("upper_price") is None or args.get("lower_price") is None:
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
# Registry — discovered from the ``nautilus_trading.strategies`` entry-point group
#
# ``StrategySpec`` / ``ActorSpec`` / Protocols live in
# :mod:`nautilus_trading.specs` and are re-imported at the top of this file.
# ---------------------------------------------------------------------------


def _discover_strategy_specs() -> dict[str, StrategySpec]:
    """Discover strategies registered via the ``nautilus_trading.strategies`` entry-point group.

    Each entry-point resolves to a :class:`StrategySpec` constant or a zero-arg factory
    returning one. This is the underlying discovery function. Callers should normally
    use :func:`get_strategy_specs` (cached) or the lazy ``STRATEGY_SPECS`` module
    attribute (resolved via PEP 562 ``__getattr__``). This raw function is exposed so
    tests can patch ``entry_points`` and call it without triggering the cache.

    Resilience: a single broken plugin must not crash the CLI. Three guards apply
    per entry-point — ``ep.load()`` failure, factory-call failure, and wrong type
    are each logged-and-skipped. Duplicate names and name mismatches still raise
    ``RuntimeError`` because those are unambiguous plugin-author errors that
    silent skipping would mask.

    Raises
    ------
    RuntimeError
        If two installed packages register the same strategy name, or if an
        entry-point's registered name does not match its ``STRATEGY_SPEC.name``.
        Both error messages name the source package(s) so the user knows what
        to uninstall, rename, or correct.
    """
    # Repo-root strategies (``strategies.forex.*``, ``strategies.crypto.*``) live
    # outside the ``nautilus_trading`` package and rely on a sys.path bootstrap.
    # Discovery fires at module import — before any CLI dispatcher has had a
    # chance to call this — so do the bootstrap here. The helper is idempotent.
    from nautilus_trading.cli._common import _ensure_project_root_on_path

    _ensure_project_root_on_path()

    specs: dict[str, StrategySpec] = {}
    sources: dict[str, str] = {}  # spec.name -> source distribution name
    for ep in importlib.metadata.entry_points(group="nautilus_trading.strategies"):
        # ``EntryPoint.dist`` is typed Optional but populated for any ep yielded by
        # ``entry_points(group=...)``; the fallback gives a human-readable label
        # if a future Python release relaxes the contract.
        dist_name = ep.dist.name if ep.dist is not None else "<unknown distribution>"
        try:
            spec_obj: object = ep.load()
        except Exception as exc:  # noqa: BLE001 — broken plugin must not crash discovery
            logger.warning(
                "Skipping strategy entry-point %r from %r: load failed (%s)",
                ep.name,
                dist_name,
                exc,
            )
            continue
        if callable(spec_obj):
            try:
                spec_obj = spec_obj()
            except Exception as exc:  # noqa: BLE001 — same rationale as above
                logger.warning(
                    "Skipping strategy entry-point %r from %r: factory call failed (%s)",
                    ep.name,
                    dist_name,
                    exc,
                )
                continue
        if not isinstance(spec_obj, StrategySpec):
            logger.warning(
                "Skipping strategy entry-point %r from %r: expected StrategySpec, got %s",
                ep.name,
                dist_name,
                type(spec_obj).__name__,
            )
            continue
        spec = spec_obj
        # Contract: the entry-point key (used by YAML's ``strategy:`` field) must
        # match ``STRATEGY_SPEC.name`` (used by dispatch + the duplicate-detect
        # check below). A mismatch would let a third party silently expose a
        # name differently from what they registered.
        if spec.name != ep.name:
            raise RuntimeError(
                f"Entry-point name mismatch: '{dist_name}' registered the strategy "
                f"as '{ep.name}' but its STRATEGY_SPEC.name is '{spec.name}'. "
                f"The two must match."
            )
        if spec.name in specs:
            raise RuntimeError(
                f"Duplicate strategy registration: '{spec.name}' "
                f"declared by both '{sources[spec.name]}' and '{dist_name}'. "
                f"Uninstall or rename one to resolve."
            )
        specs[spec.name] = spec
        sources[spec.name] = dist_name
    return specs


@functools.cache
def get_strategy_specs() -> dict[str, StrategySpec]:
    """Discover and cache the strategy registry on first call.

    The cache is process-lifetime; tests that need a fresh registry
    can call ``get_strategy_specs.cache_clear()``. Discovery walks the
    ``nautilus_trading.strategies`` entry-point group and bootstraps
    ``sys.path`` for repo-root strategies.

    The cached dict is shared across callers — never mutate it in place;
    if you need a copy, use ``dict(get_strategy_specs())``.
    """
    return _discover_strategy_specs()


@functools.cache
def get_strategy_builders() -> dict[str, StrategyConfigBuilder]:
    """Lazy ``name -> builder`` projection of :func:`get_strategy_specs`.

    Kept as a separate cache so callers consuming only the builder map
    don't pay an extra dict comprehension on every access. ``backtest/runner.py``
    is the remaining consumer; new code should prefer :func:`get_strategy_specs`
    so it picks up import paths + actor wiring at the same time.
    """
    return {name: spec.builder for name, spec in get_strategy_specs().items()}


def clear_strategy_caches() -> None:
    """Clear both cached accessors at once for tests that need a fresh registry.

    :func:`get_strategy_specs` and :func:`get_strategy_builders` are cached
    independently, so clearing only one can leave the other stale. Tests that
    re-patch ``importlib.metadata.entry_points`` (or otherwise need to force
    a re-scan) should prefer this helper. The per-accessor ``.cache_clear()``
    methods still work — they're just easy to forget the second one of.
    """
    get_strategy_specs.cache_clear()
    get_strategy_builders.cache_clear()


# PEP 562 module-level ``__getattr__`` makes the legacy module-level
# constants ``STRATEGY_SPECS`` / ``STRATEGY_BUILDERS`` lazy: importing them
# (``from nautilus_trading.cli._strategy_specs import STRATEGY_SPECS``) does
# NOT trigger discovery at import time — only the first reference does,
# via :func:`get_strategy_specs`. New code should call the accessors
# directly; these aliases exist for backwards compat.
def __getattr__(name: str) -> Any:
    if name == "STRATEGY_SPECS":
        return get_strategy_specs()
    if name == "STRATEGY_BUILDERS":
        return get_strategy_builders()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
