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
    """STRATEGY_BUILDERS must derive from STRATEGY_SPECS for backward compat
    (the 8 *_paper.py shims and cli/paper_trade.py still import it)."""
    from nautilus_trading.cli._strategy_specs import STRATEGY_BUILDERS, STRATEGY_SPECS

    assert set(STRATEGY_BUILDERS) == set(STRATEGY_SPECS)
    for name, spec in STRATEGY_SPECS.items():
        assert STRATEGY_BUILDERS[name] is spec.builder


def test_legacy_strategy_configs_module_reexports_registry():
    """cli/_strategy_configs.py must still import cleanly — downstream code
    (paper_trade.py, 8 shim runners, test_strategy_configs.py) depends on it
    until Task C deletes the shim."""
    from nautilus_trading.cli import _strategy_configs as legacy
    from nautilus_trading.cli import _strategy_specs as specs

    assert legacy.STRATEGY_BUILDERS == specs.STRATEGY_BUILDERS
    # The 8 existing builder classes must remain importable through the shim.
    for name in (
        "GridBotConfigBuilder",
        "DCABotConfigBuilder",
        "EMAConfigBuilder",
        "TimesFMConfigBuilder",
        "HybridSMAConfigBuilder",
        "TimesFMGridConfigBuilder",
        "RVSSwingConfigBuilder",
        "ShockGuardConfigBuilder",
        "StrategyConfigBuilder",
    ):
        assert hasattr(legacy, name), f"legacy shim missing {name}"


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


def test_kronos_actor_config_builder_output_is_valid_kronos_actor_config_kwargs():
    """The actor-config dict must contain every required field of
    KronosActorConfig so that ImportableActorConfig can instantiate it."""
    from nautilus_trading.cli._strategy_specs import KronosActorConfigBuilder

    out = KronosActorConfigBuilder().build(
        {
            "instrument_id": "BTCUSDT.BINANCE",
            "bar_type": "BTCUSDT.BINANCE-1-HOUR-LAST-EXTERNAL",
        }
    )
    # All fields that lack defaults on KronosActorConfig must be present.
    assert "instrument_id" in out
    assert "bar_type" in out
    # Optional fields may be present but must not be None-if-set.
    for k in ("model_size", "n_samples", "forecast_horizon", "inference_interval_bars"):
        assert k in out
        assert out[k] is not None
