"""Sub-project C — entry-point-based strategy discovery."""

from __future__ import annotations

import importlib

import pytest

from nautilus_trading.cli._strategy_specs import StrategySpec

# All 9 in-repo strategies and their module paths.
STRATEGY_MODULES: dict[str, str] = {
    "ema_cross": "strategies.forex.ema_cross",
    "grid_bot": "strategies.crypto.grid_bot",
    "dca_bot": "strategies.crypto.dca_bot",
    "timesfm_swing": "strategies.crypto.timesfm_swing",
    "hybrid_sma_r10": "strategies.crypto.hybrid_sma_r10",
    "timesfm_grid": "strategies.crypto.timesfm_grid",
    "rvs_swing": "strategies.crypto.rvs_swing",
    "shock_guard": "strategies.crypto.shock_guard",
    "kronos": "strategies.crypto.kronos",
}


@pytest.mark.parametrize("name,module_path", list(STRATEGY_MODULES.items()))
def test_strategy_module_exports_strategy_spec(name: str, module_path: str) -> None:
    """Each in-repo strategy module exports a top-level STRATEGY_SPEC constant."""
    module = importlib.import_module(module_path)
    spec = getattr(module, "STRATEGY_SPEC", None)
    assert spec is not None, f"{module_path} must export STRATEGY_SPEC"
    assert isinstance(spec, StrategySpec), f"{module_path}.STRATEGY_SPEC must be a StrategySpec"
    assert spec.name == name, (
        f"{module_path}.STRATEGY_SPEC.name must be '{name}', got '{spec.name}'"
    )
