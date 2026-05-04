"""Sub-project C — entry-point-based strategy discovery."""

from __future__ import annotations

import importlib
import importlib.metadata

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


def test_strategy_specs_dict_is_populated_from_entry_points() -> None:
    """STRATEGY_SPECS is populated from `nautilus_trading.strategies` entry-points."""
    from nautilus_trading.cli._strategy_specs import STRATEGY_SPECS

    eps = {ep.name for ep in importlib.metadata.entry_points(group="nautilus_trading.strategies")}
    # Guard: a passing test on empty == empty would silently no-op when the
    # pyproject entry-points block is missing or `uv sync` was skipped.
    assert eps, (
        "no strategies registered — pyproject.toml entry-points block missing "
        "or `uv sync` skipped after editing pyproject.toml?"
    )
    assert set(STRATEGY_SPECS.keys()) == eps, (
        f"STRATEGY_SPECS keys ({sorted(STRATEGY_SPECS)}) must match registered entry-points ({sorted(eps)})"
    )

    for name, spec in STRATEGY_SPECS.items():
        assert spec.name == name, f"STRATEGY_SPECS['{name}'].name must equal key, got '{spec.name}'"


def test_discover_strategy_specs_raises_on_duplicate_names() -> None:
    """_discover_strategy_specs() raises RuntimeError when two entry-points share a name."""
    from dataclasses import replace
    from unittest.mock import MagicMock, patch

    from strategies.crypto.grid_bot import STRATEGY_SPEC as GRID_BOT_SPEC

    from nautilus_trading.cli._strategy_specs import _discover_strategy_specs

    duplicate_spec = replace(GRID_BOT_SPEC, name="duplicate_name")

    fake_ep_1 = MagicMock()
    fake_ep_1.name = "duplicate_name"
    fake_ep_1.load.return_value = duplicate_spec
    fake_ep_1.dist = MagicMock()
    fake_ep_1.dist.name = "package-a"

    fake_ep_2 = MagicMock()
    fake_ep_2.name = "duplicate_name"
    fake_ep_2.load.return_value = duplicate_spec
    fake_ep_2.dist = MagicMock()
    fake_ep_2.dist.name = "package-b"

    with patch(
        "nautilus_trading.cli._strategy_specs.importlib.metadata.entry_points",
        return_value=[fake_ep_1, fake_ep_2],
    ):
        with pytest.raises(RuntimeError, match="package-a.*package-b|package-b.*package-a"):
            _discover_strategy_specs()
