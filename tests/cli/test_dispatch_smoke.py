"""CLI dispatch smokes — go beyond ``--help`` and exercise the actual paths.

Per ``feedback_help_smoke_gap.md``: a ``--help`` smoke doesn't catch the
real regressions. The two contracts here are the smallest invocations that
DO exercise dispatch:

(a) Every registered ``StrategySpec.builder`` produces a dict for canonical
    args. Catches a builder that accidentally returns ``None`` or raises
    on otherwise-valid input — the kind of thing ``--help`` ignores entirely.

(b) Every ``StrategySpec.strategy_path`` and ``StrategySpec.config_path``
    actually resolves to an importable class. Catches a renamed module
    whose ``STRATEGY_SPEC.strategy_path`` was not updated — a real risk
    after the Phase C migration that moved strategies under ``strategies/``.
"""

from __future__ import annotations

import importlib
import inspect

import pytest

from nautilus_trading.cli._strategy_specs import STRATEGY_SPECS

# Canonical args used by both tests. Every key a builder might want is supplied;
# builders that don't need a key just ignore it.
CANONICAL_ARGS: dict[str, object] = {
    # _BASE_FIELDS + trade_size
    "instrument_id": "BTCUSDT.BINANCE",
    "bar_type": "BTCUSDT.BINANCE-1-HOUR-LAST-EXTERNAL",
    "trade_size": "0.01",
    # GridBotConfigBuilder
    "upper_price": "50000",
    "lower_price": "40000",
    "grid_levels": 8,
    # DCABotConfigBuilder
    "buy_amount": "100",
    "buy_interval_bars": 4,
    # EMAConfigBuilder + TimesFMConfigBuilder
    "fast_ema": 10,
    "slow_ema": 20,
    # HybridSMAConfigBuilder
    "sma_fast": 10,
    "sma_slow": 30,
    "stop_fast": "0.02",
    "stop_slow": "0.05",
}


@pytest.mark.parametrize(
    "name,spec",
    list(STRATEGY_SPECS.items()),
    ids=list(STRATEGY_SPECS.keys()),
)
def test_every_registered_strategy_builder_produces_dict_for_canonical_args(
    name: str, spec: object
) -> None:
    """Every ``StrategySpec.builder.build(canonical_args)`` returns a dict
    containing the base fields.

    Catches regressions where a builder accidentally returns ``None``, returns
    a non-dict, raises on canonical input, or drops a base field. ``--help``
    never invokes ``builder.build``, so this is the cheapest smoke that
    actually exercises the dispatch path the CLI takes after argument parsing.
    """
    out = spec.builder.build(dict(CANONICAL_ARGS))  # type: ignore[attr-defined]
    assert isinstance(out, dict), f"{name} builder returned {type(out).__name__}, expected dict"
    assert out.get("instrument_id") == CANONICAL_ARGS["instrument_id"], (
        f"{name} builder dropped/altered instrument_id: {out!r}"
    )
    assert out.get("bar_type") == CANONICAL_ARGS["bar_type"], (
        f"{name} builder dropped/altered bar_type: {out!r}"
    )
    # trade_size is base-and-required for every builder EXCEPT hybrid_sma_r10,
    # which sizes from equity (HybridSMAConfigBuilder uses include_trade_size=False).
    if name != "hybrid_sma_r10":
        assert out.get("trade_size") == CANONICAL_ARGS["trade_size"], (
            f"{name} builder dropped/altered trade_size: {out!r}"
        )


@pytest.mark.parametrize(
    "name,spec",
    list(STRATEGY_SPECS.items()),
    ids=list(STRATEGY_SPECS.keys()),
)
def test_strategy_path_resolves_to_importable_class(name: str, spec: object) -> None:
    """Every ``StrategySpec.strategy_path`` and ``config_path`` resolves to a class.

    Catches regressions where a strategy module is renamed but
    ``STRATEGY_SPEC.strategy_path`` wasn't updated — a real risk after the
    Phase C migration that moved strategies under ``strategies/``. Walks the
    same ``"module.path:ClassName"`` shape ``ImportableStrategyConfig``
    consumes at TradingNode boot.

    Heavy ML deps (timesfm, kronos local install) may not be present in every
    environment; ``importorskip`` short-circuits the parametrization in that
    case so the dispatch smoke stays useful for the lighter strategies.
    """
    # Heavy-deps short-circuit. timesfm_swing/timesfm_grid import the timesfm
    # package at module-import time; kronos imports the local kronos repo.
    if name in {"timesfm_swing", "timesfm_grid"}:
        pytest.importorskip("timesfm", reason=f"{name} requires the timesfm package")
    if name == "kronos":
        pytest.importorskip("kronos", reason="kronos requires the local kronos install")

    for label, path in (
        ("strategy_path", spec.strategy_path),  # type: ignore[attr-defined]
        ("config_path", spec.config_path),  # type: ignore[attr-defined]
    ):
        assert ":" in path, f"{name}.{label} missing ':' separator: {path!r}"
        module_path, _, class_name = path.partition(":")
        module = importlib.import_module(module_path)
        cls = getattr(module, class_name, None)
        assert cls is not None, (
            f"{name}.{label}={path!r} — module {module_path!r} has no attribute {class_name!r}"
        )
        assert inspect.isclass(cls), (
            f"{name}.{label}={path!r} — {class_name!r} is {type(cls).__name__}, expected a class"
        )

    # Same drill for any attached ActorSpecs (kronos has one).
    for i, actor in enumerate(spec.actor_specs):  # type: ignore[attr-defined]
        for label, path in (
            (f"actor_specs[{i}].actor_path", actor.actor_path),
            (f"actor_specs[{i}].config_path", actor.config_path),
        ):
            assert ":" in path, f"{name}.{label} missing ':' separator: {path!r}"
            module_path, _, class_name = path.partition(":")
            module = importlib.import_module(module_path)
            cls = getattr(module, class_name, None)
            assert cls is not None, (
                f"{name}.{label}={path!r} — module {module_path!r} has no attribute {class_name!r}"
            )
            assert inspect.isclass(cls), (
                f"{name}.{label}={path!r} — {class_name!r} is "
                f"{type(cls).__name__}, expected a class"
            )
