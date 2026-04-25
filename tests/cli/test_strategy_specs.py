"""Tests for the cli._strategy_specs registry.

These tests lock in the shape of the StrategySpec registry that supersedes
STRATEGY_BUILDERS. They are written before implementation (TDD RED phase)
and assert the registry surface the generic PaperTradeStrategyRunner in
Task B will consume.
"""

from __future__ import annotations

import pytest

EXPECTED_STRATEGY_PATHS: dict[str, tuple[str, str]] = {
    "grid_bot": (
        "strategies.crypto.grid_bot:GridBotStrategy",
        "strategies.crypto.grid_bot:GridBotConfig",
    ),
    "dca_bot": (
        "strategies.crypto.dca_bot:DCABotStrategy",
        "strategies.crypto.dca_bot:DCABotConfig",
    ),
    "ema_cross": (
        "strategies.forex.ema_cross:EMACrossStrategy",
        "strategies.forex.ema_cross:EMACrossConfig",
    ),
    "timesfm_swing": (
        "strategies.crypto.timesfm_swing:TimesFMSwingStrategy",
        "strategies.crypto.timesfm_swing:TimesFMSwingConfig",
    ),
    "hybrid_sma_r10": (
        "strategies.crypto.hybrid_sma_r10:HybridSMAR10Strategy",
        "strategies.crypto.hybrid_sma_r10:HybridSMAR10Config",
    ),
    "timesfm_grid": (
        "strategies.crypto.timesfm_grid:TimesFMGridStrategy",
        "strategies.crypto.timesfm_grid:TimesFMGridConfig",
    ),
    "rvs_swing": (
        "strategies.crypto.rvs_swing:RVSSwingStrategy",
        "strategies.crypto.rvs_swing:RVSSwingConfig",
    ),
    "shock_guard": (
        "strategies.crypto.shock_guard:ShockGuardStrategy",
        "strategies.crypto.shock_guard:ShockGuardConfig",
    ),
    "kronos": (
        "strategies.crypto.kronos.strategy:KronosStrategy",
        "strategies.crypto.kronos.strategy:KronosStrategyConfig",
    ),
}


def test_strategy_specs_contains_exactly_nine_strategies():
    from nautilus_trading.cli._strategy_specs import STRATEGY_SPECS

    assert set(STRATEGY_SPECS) == set(EXPECTED_STRATEGY_PATHS)


def test_strategy_specs_keys_match_spec_names():
    from nautilus_trading.cli._strategy_specs import STRATEGY_SPECS

    for name, spec in STRATEGY_SPECS.items():
        assert spec.name == name, f"{name}: spec.name={spec.name!r}"


def test_each_spec_has_non_empty_paths_and_callable_builder():
    from nautilus_trading.cli._strategy_specs import STRATEGY_SPECS

    for name, spec in STRATEGY_SPECS.items():
        assert spec.strategy_path, f"{name} missing strategy_path"
        assert spec.config_path, f"{name} missing config_path"
        assert ":" in spec.strategy_path, f"{name} strategy_path missing ':'"
        assert ":" in spec.config_path, f"{name} config_path missing ':'"
        assert callable(spec.builder.build), f"{name} builder.build not callable"


def test_strategy_paths_match_existing_paper_shim_values():
    """Each spec's (strategy_path, config_path) must match the strings the
    8 *_paper.py shims and kronos/paper_runner.py already use — this is what
    makes Task C's shim deletion a no-op at the import-path layer.
    """
    from nautilus_trading.cli._strategy_specs import STRATEGY_SPECS

    for name, (strat_path, cfg_path) in EXPECTED_STRATEGY_PATHS.items():
        spec = STRATEGY_SPECS[name]
        assert spec.strategy_path == strat_path, f"{name} strategy_path mismatch"
        assert spec.config_path == cfg_path, f"{name} config_path mismatch"


def test_lookup_by_full_strategy_path_resolves_every_spec():
    """``backtest/runner.py`` resolves the strategy-config builder by full
    ``strategy_path`` against ``STRATEGY_SPECS``. This regression guards the
    lookup against being reduced back to module-basename derivation
    (``rsplit('.', 1)[-1].split(':')[0]``), which produced ``'strategy'`` for
    kronos's nested path ``strategies.crypto.kronos.strategy:KronosStrategy``
    and silently missed the registered ``'kronos'`` key. Pre-fix, calling the
    backtest CLI with the kronos path returned the bare base config instead
    of dispatching to ``KronosConfigBuilder``.
    """
    from nautilus_trading.cli._strategy_specs import STRATEGY_SPECS

    for name, spec in STRATEGY_SPECS.items():
        resolved = next(
            (s for s in STRATEGY_SPECS.values() if s.strategy_path == spec.strategy_path),
            None,
        )
        assert resolved is not None, f"by-path lookup missed {name} ({spec.strategy_path})"
        assert resolved.name == name, f"path lookup for {name} resolved to {resolved.name}"


def test_lookup_by_kronos_full_strategy_path_returns_kronos_spec():
    """Specific regression for the nested-module bug: the kronos full
    strategy_path resolves to the kronos spec, not to a sibling spec or to
    ``None`` (which the pre-fix backtest dispatcher hit, silently degrading
    to the base config dict)."""
    from nautilus_trading.cli._strategy_specs import STRATEGY_SPECS

    target = "strategies.crypto.kronos.strategy:KronosStrategy"
    resolved = next(
        (s for s in STRATEGY_SPECS.values() if s.strategy_path == target),
        None,
    )

    assert resolved is not None
    assert resolved.name == "kronos"
    # And the legacy module-basename derivation that caused the bug:
    legacy_module_name = target.rsplit(".", 1)[-1].split(":")[0]
    assert legacy_module_name == "strategy", (
        "guard: this test exists because the legacy lookup would derive "
        f"{legacy_module_name!r} from the kronos path; if that derivation "
        "ever changes, the bug-class behind this test has changed too"
    )
    assert legacy_module_name not in STRATEGY_SPECS, (
        "guard: legacy module-name lookup against STRATEGY_SPECS would still miss "
        "kronos because the registry keys by spec name, not module basename"
    )


def test_non_kronos_specs_have_empty_actor_specs_tuple():
    from nautilus_trading.cli._strategy_specs import STRATEGY_SPECS

    for name, spec in STRATEGY_SPECS.items():
        if name == "kronos":
            continue
        assert spec.actor_specs == (), f"{name} should have empty actor_specs"


def test_kronos_spec_has_exactly_one_actor_spec():
    from nautilus_trading.cli._strategy_specs import STRATEGY_SPECS

    spec = STRATEGY_SPECS["kronos"]
    assert len(spec.actor_specs) == 1
    actor = spec.actor_specs[0]
    assert actor.actor_path == "strategies.crypto.kronos.actor:KronosActor"
    assert actor.config_path == "strategies.crypto.kronos.actor:KronosActorConfig"
    assert callable(actor.builder.build)


def test_strategy_spec_is_frozen_dataclass():
    """StrategySpec must be frozen so it can be hashed / stored in sets."""
    import dataclasses

    from nautilus_trading.cli._strategy_specs import STRATEGY_SPECS

    spec = STRATEGY_SPECS["grid_bot"]
    with pytest.raises(dataclasses.FrozenInstanceError):
        spec.name = "different"  # type: ignore[misc]


def test_actor_spec_is_frozen_dataclass():
    import dataclasses

    from nautilus_trading.cli._strategy_specs import STRATEGY_SPECS

    actor = STRATEGY_SPECS["kronos"].actor_specs[0]
    with pytest.raises(dataclasses.FrozenInstanceError):
        actor.actor_path = "different"  # type: ignore[misc]


def test_spec_builder_matches_legacy_strategy_builders_shim():
    """STRATEGY_BUILDERS must derive from STRATEGY_SPECS for legacy backtest
    dispatch (``backtest/runner.py`` is the remaining consumer post-B.5)."""
    from nautilus_trading.cli._strategy_specs import STRATEGY_BUILDERS, STRATEGY_SPECS

    assert set(STRATEGY_BUILDERS) == set(STRATEGY_SPECS)
    for name, spec in STRATEGY_SPECS.items():
        assert STRATEGY_BUILDERS[name] is spec.builder


# -- Per-builder parity: spec.builder == direct builder class --------------


def test_grid_bot_spec_builder_parity():
    from nautilus_trading.cli._strategy_specs import STRATEGY_SPECS, GridBotConfigBuilder

    args = {
        "instrument_id": "BTCUSDT.BINANCE",
        "bar_type": "BTCUSDT.BINANCE-1-HOUR-LAST-EXTERNAL",
        "trade_size": "0.001",
        "upper_price": "50000",
        "lower_price": "40000",
        "grid_levels": 8,
    }
    assert STRATEGY_SPECS["grid_bot"].builder.build(args) == GridBotConfigBuilder().build(args)


def test_dca_bot_spec_builder_parity():
    from nautilus_trading.cli._strategy_specs import STRATEGY_SPECS, DCABotConfigBuilder

    args = {
        "instrument_id": "BTCUSDT.BINANCE",
        "bar_type": "BTCUSDT.BINANCE-1-HOUR-LAST-EXTERNAL",
        "trade_size": "0.001",
        "buy_amount": "100",
        "buy_interval_bars": 4,
    }
    assert STRATEGY_SPECS["dca_bot"].builder.build(args) == DCABotConfigBuilder().build(args)


def test_ema_cross_spec_builder_parity():
    from nautilus_trading.cli._strategy_specs import STRATEGY_SPECS, EMAConfigBuilder

    args = {
        "instrument_id": "EUR/USD.SIM",
        "bar_type": "EUR/USD.SIM-1-MINUTE-MID-INTERNAL",
        "trade_size": "1000",
        "fast_ema": 10,
        "slow_ema": 20,
    }
    assert STRATEGY_SPECS["ema_cross"].builder.build(args) == EMAConfigBuilder().build(args)


def test_hybrid_sma_r10_spec_builder_parity():
    from nautilus_trading.cli._strategy_specs import STRATEGY_SPECS, HybridSMAConfigBuilder

    args = {
        "instrument_id": "BTCUSDT.BINANCE",
        "bar_type": "BTCUSDT.BINANCE-1-HOUR-LAST-EXTERNAL",
        "sma_fast": 10,
        "sma_slow": 30,
        "stop_fast": "0.02",
        "stop_slow": "0.05",
    }
    assert STRATEGY_SPECS["hybrid_sma_r10"].builder.build(args) == HybridSMAConfigBuilder().build(
        args
    )


# -- Kronos-specific builders ----------------------------------------------


def test_kronos_strategy_builder_returns_base_dict_only():
    """KronosStrategyConfig accepts only base fields — ML hyperparams live on
    KronosActorConfig, not the strategy config. The kronos paper_runner pins
    this shape in build_config()."""
    from nautilus_trading.cli._strategy_specs import STRATEGY_SPECS

    args = {
        "instrument_id": "BTCUSDT.BINANCE",
        "bar_type": "BTCUSDT.BINANCE-1-HOUR-LAST-EXTERNAL",
        "trade_size": "0.001",
    }
    out = STRATEGY_SPECS["kronos"].builder.build(args)
    assert out == {
        "instrument_id": "BTCUSDT.BINANCE",
        "bar_type": "BTCUSDT.BINANCE-1-HOUR-LAST-EXTERNAL",
        "trade_size": "0.001",
    }


def test_kronos_actor_builder_applies_paper_runner_defaults():
    """Defaults must match the quarantined paper_trade.py snapshot values
    pinned in strategies/crypto/kronos/paper_runner.py (model_size=mini,
    n_samples=10, forecast_horizon=24, inference_interval_bars=4)."""
    from nautilus_trading.cli._strategy_specs import KronosActorConfigBuilder

    out = KronosActorConfigBuilder().build(
        {
            "instrument_id": "BTCUSDT.BINANCE",
            "bar_type": "BTCUSDT.BINANCE-1-HOUR-LAST-EXTERNAL",
        }
    )
    assert out == {
        "instrument_id": "BTCUSDT.BINANCE",
        "bar_type": "BTCUSDT.BINANCE-1-HOUR-LAST-EXTERNAL",
        "model_size": "mini",
        "n_samples": 10,
        "forecast_horizon": 24,
        "inference_interval_bars": 4,
    }


def test_kronos_actor_builder_honors_overrides():
    from nautilus_trading.cli._strategy_specs import KronosActorConfigBuilder

    out = KronosActorConfigBuilder().build(
        {
            "instrument_id": "BTCUSDT.BINANCE",
            "bar_type": "BTCUSDT.BINANCE-1-HOUR-LAST-EXTERNAL",
            "model_size": "small",
            "n_samples": 20,
            "forecast_horizon": 48,
            "inference_interval_bars": 2,
        }
    )
    assert out["model_size"] == "small"
    assert out["n_samples"] == 20
    assert out["forecast_horizon"] == 48
    assert out["inference_interval_bars"] == 2


def test_kronos_actor_spec_builder_is_kronos_actor_config_builder():
    from nautilus_trading.cli._strategy_specs import STRATEGY_SPECS, KronosActorConfigBuilder

    args = {
        "instrument_id": "BTCUSDT.BINANCE",
        "bar_type": "BTCUSDT.BINANCE-1-HOUR-LAST-EXTERNAL",
    }
    actor_spec = STRATEGY_SPECS["kronos"].actor_specs[0]
    assert actor_spec.builder.build(args) == KronosActorConfigBuilder().build(args)


def test_kronos_actor_config_builder_output_constructs_kronos_actor_config():
    """Round-trip: ``KronosActorConfig(**builder.build(args))`` must not raise.

    This guards against KronosActorConfig schema drift. A key-presence check
    stays green while ``ImportableActorConfig`` blows up at runtime; actually
    constructing the config surfaces missing-required-field or type errors
    here, at unit-test time.

    ImportableActorConfig does the string→``InstrumentId``/``BarType`` coercion
    via msgspec at TradingNode boot. When constructing ``KronosActorConfig``
    directly we convert them explicitly — the test is about required-field
    coverage, not the coercion layer.
    """
    from nautilus_trader.model.data import BarType
    from nautilus_trader.model.identifiers import InstrumentId
    from strategies.crypto.kronos.actor import KronosActorConfig

    from nautilus_trading.cli._strategy_specs import KronosActorConfigBuilder

    instrument_str = "BTCUSDT.BINANCE"
    bar_type_str = "BTCUSDT.BINANCE-1-MINUTE-LAST-INTERNAL"

    out = KronosActorConfigBuilder().build(
        {"instrument_id": instrument_str, "bar_type": bar_type_str}
    )

    # Convert string IDs to the types KronosActorConfig expects when constructed
    # directly — ImportableActorConfig does this via msgspec at runtime.
    kwargs = dict(out)
    kwargs["instrument_id"] = InstrumentId.from_str(kwargs["instrument_id"])
    kwargs["bar_type"] = BarType.from_str(kwargs["bar_type"])

    config = KronosActorConfig(**kwargs)  # must not raise

    # Round-trip sanity: every builder output field lands on the config.
    assert str(config.instrument_id) == instrument_str
    assert str(config.bar_type) == bar_type_str
    assert config.model_size == "mini"
    assert config.n_samples == 10
    assert config.forecast_horizon == 24
    assert config.inference_interval_bars == 4


# -- Uniform failure mode: missing base fields raise ValueError, not KeyError -


def test_all_builders_raise_value_error_on_empty_args():
    """Every registered builder must raise ``ValueError`` (not ``KeyError``)
    when handed an empty args dict.

    Uniform ``ValueError`` semantics matter because the CLI / YAML dispatcher
    catches ``ValueError`` from ``builder.build(...)`` and re-raises it as a
    ``typer.BadParameter``. A ``KeyError`` would bypass that mapping and
    surface as an uncaught stack trace.

    Each builder may raise with its own message (e.g. ``grid_bot`` checks
    prices before calling ``_base``; ``dca_bot`` checks ``buy_interval_bars``
    first). We don't pin the message here — only the exception type.
    """
    from nautilus_trading.cli._strategy_specs import STRATEGY_SPECS

    for name, spec in STRATEGY_SPECS.items():
        with pytest.raises(ValueError) as excinfo:
            spec.builder.build({})
        # Sanity: KeyError would not inherit from ValueError, so a false
        # positive here would only arise if a builder raised ValueError for a
        # reason unrelated to missing fields — acceptable, we just want the
        # non-KeyError contract to hold.
        assert not isinstance(excinfo.value, KeyError), (
            f"{name} raised KeyError-compatible ValueError: {excinfo.value!r}"
        )


def test_kronos_builder_raises_value_error_on_missing_base_fields():
    """Regression: the pre-fix ``KronosConfigBuilder`` (and any other builder
    that went straight to ``_base(args)`` without a prior guard) silently
    ``KeyError``-ed on missing base fields. ``_base`` now raises
    ``ValueError`` uniformly. This test pins that for kronos specifically
    so any future regression in ``_base`` fails loudly here.
    """
    from nautilus_trading.cli._strategy_specs import STRATEGY_SPECS

    builder = STRATEGY_SPECS["kronos"].builder

    # instrument_id only — bar_type + trade_size missing
    with pytest.raises(ValueError, match=r"bar_type|trade_size"):
        builder.build({"instrument_id": "BTCUSDT.BINANCE"})

    # bar_type only — instrument_id + trade_size missing
    with pytest.raises(ValueError, match=r"instrument_id|trade_size"):
        builder.build({"bar_type": "BTCUSDT.BINANCE-1-HOUR-LAST-EXTERNAL"})
