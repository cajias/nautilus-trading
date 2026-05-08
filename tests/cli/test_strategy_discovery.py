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
    """STRATEGY_SPECS is a (non-empty) subset of `nautilus_trading.strategies` eps.

    Subset (not equality) because discovery is lenient: a third-party package
    with a broken entry point (stale ``.pth``, missing module, factory raises,
    wrong return type) is logged-and-skipped rather than crashing the CLI.
    The full equality contract is checked separately by the in-repo strategy
    list in ``tests/cli/test_strategy_specs.py``, which knows the canonical
    set of in-repo strategy names.
    """
    from nautilus_trading.cli._strategy_specs import STRATEGY_SPECS

    eps = {ep.name for ep in importlib.metadata.entry_points(group="nautilus_trading.strategies")}
    # Guard: a passing test on empty == empty would silently no-op when the
    # pyproject entry-points block is missing or `uv sync` was skipped.
    assert eps, (
        "no strategies registered — pyproject.toml entry-points block missing "
        "or `uv sync` skipped after editing pyproject.toml?"
    )
    assert STRATEGY_SPECS, "STRATEGY_SPECS empty — discovery skipped every entry point?"
    assert set(STRATEGY_SPECS.keys()) <= eps, (
        f"STRATEGY_SPECS keys ({sorted(STRATEGY_SPECS)}) must be a subset of registered entry-points ({sorted(eps)})"
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


def test_discover_strategy_specs_raises_on_name_mismatch() -> None:
    """_discover_strategy_specs() raises RuntimeError when ep.name != spec.name.

    The entry-point key (used in YAML ``strategy:`` lookups) must match
    ``STRATEGY_SPEC.name`` (used by dispatch + duplicate detection). A mismatch
    would let an external package expose a name different from what it registered.
    """
    from dataclasses import replace
    from unittest.mock import MagicMock, patch

    from strategies.crypto.grid_bot import STRATEGY_SPEC as GRID_BOT_SPEC

    from nautilus_trading.cli._strategy_specs import _discover_strategy_specs

    mismatched_spec = replace(GRID_BOT_SPEC, name="actual_name_in_spec")

    fake_ep = MagicMock()
    fake_ep.name = "registered_as"  # entry-point key
    fake_ep.load.return_value = mismatched_spec
    fake_ep.dist = MagicMock()
    fake_ep.dist.name = "external-pkg"

    with patch(
        "nautilus_trading.cli._strategy_specs.importlib.metadata.entry_points",
        return_value=[fake_ep],
    ):
        with pytest.raises(
            RuntimeError,
            match="registered_as.*actual_name_in_spec|actual_name_in_spec.*registered_as",
        ):
            _discover_strategy_specs()


# ---------------------------------------------------------------------------
# Lenient discovery — broken plugin must be logged-and-skipped, not raise
# ---------------------------------------------------------------------------


def test_discover_skips_entry_point_with_load_failure(caplog) -> None:
    """A broken entry-point whose ``ep.load()`` raises must be logged-and-skipped.

    Regression guard for the lenient-discovery contract: a third-party plugin
    that is half-installed (stale ``.pth``, missing dependency, syntax error
    in module) must not crash the CLI. Discovery logs WARNING + the ep name
    + the source distribution and continues.
    """
    import logging
    from unittest.mock import MagicMock, patch

    from nautilus_trading.cli._strategy_specs import _discover_strategy_specs

    fake_ep = MagicMock()
    fake_ep.name = "broken_loader"
    fake_ep.load.side_effect = ImportError("boom")
    fake_ep.dist = MagicMock()
    fake_ep.dist.name = "broken-package"

    caplog.set_level(logging.WARNING, logger="nautilus_trading.cli._strategy_specs")
    with patch(
        "nautilus_trading.cli._strategy_specs.importlib.metadata.entry_points",
        return_value=[fake_ep],
    ):
        result = _discover_strategy_specs()

    assert isinstance(result, dict)
    assert "broken_loader" not in result
    matching = [
        rec
        for rec in caplog.records
        if rec.levelno == logging.WARNING and "load failed" in rec.getMessage()
    ]
    assert matching, (
        f"expected a WARNING log containing 'load failed'; got: "
        f"{[(r.levelname, r.getMessage()) for r in caplog.records]}"
    )
    assert any("broken_loader" in rec.getMessage() for rec in matching), (
        f"expected the entry-point name 'broken_loader' in the warning; "
        f"got: {[r.getMessage() for r in matching]}"
    )


def test_discover_skips_entry_point_with_factory_failure(caplog) -> None:
    """A callable entry-point whose factory call raises must be logged-and-skipped.

    Some entry-points point at a zero-arg factory rather than a constant
    ``StrategySpec``. If the factory itself raises (e.g. heavy import in the
    factory body fails), discovery logs WARNING and continues.
    """
    import logging
    from unittest.mock import MagicMock, patch

    from nautilus_trading.cli._strategy_specs import _discover_strategy_specs

    def exploding_factory() -> object:
        raise TypeError("boom")

    fake_ep = MagicMock()
    fake_ep.name = "broken_factory"
    fake_ep.load.return_value = exploding_factory
    fake_ep.dist = MagicMock()
    fake_ep.dist.name = "factory-package"

    caplog.set_level(logging.WARNING, logger="nautilus_trading.cli._strategy_specs")
    with patch(
        "nautilus_trading.cli._strategy_specs.importlib.metadata.entry_points",
        return_value=[fake_ep],
    ):
        result = _discover_strategy_specs()

    assert isinstance(result, dict)
    assert "broken_factory" not in result
    matching = [
        rec
        for rec in caplog.records
        if rec.levelno == logging.WARNING and "factory call failed" in rec.getMessage()
    ]
    assert matching, (
        f"expected a WARNING log containing 'factory call failed'; got: "
        f"{[(r.levelname, r.getMessage()) for r in caplog.records]}"
    )
    assert any("broken_factory" in rec.getMessage() for rec in matching), (
        f"expected the entry-point name 'broken_factory' in the warning; "
        f"got: {[r.getMessage() for r in matching]}"
    )


def test_discover_skips_entry_point_with_wrong_type(caplog) -> None:
    """An entry-point that resolves to a non-``StrategySpec`` value must be logged-and-skipped.

    A plugin author can register an entry-point pointing at the wrong symbol
    (a dict, a Strategy class, an arbitrary object). Discovery logs WARNING
    naming the actual type and continues — never crashes.
    """
    import logging
    from unittest.mock import MagicMock, patch

    from nautilus_trading.cli._strategy_specs import _discover_strategy_specs

    fake_ep = MagicMock()
    fake_ep.name = "wrong_type"
    fake_ep.load.return_value = {"not": "a strategy spec"}
    fake_ep.dist = MagicMock()
    fake_ep.dist.name = "wrong-type-package"

    caplog.set_level(logging.WARNING, logger="nautilus_trading.cli._strategy_specs")
    with patch(
        "nautilus_trading.cli._strategy_specs.importlib.metadata.entry_points",
        return_value=[fake_ep],
    ):
        result = _discover_strategy_specs()

    assert isinstance(result, dict)
    assert "wrong_type" not in result
    matching = [
        rec
        for rec in caplog.records
        if rec.levelno == logging.WARNING and "expected StrategySpec, got dict" in rec.getMessage()
    ]
    assert matching, (
        f"expected a WARNING log containing 'expected StrategySpec, got dict'; got: "
        f"{[(r.levelname, r.getMessage()) for r in caplog.records]}"
    )
    assert any("wrong_type" in rec.getMessage() for rec in matching), (
        f"expected the entry-point name 'wrong_type' in the warning; "
        f"got: {[r.getMessage() for r in matching]}"
    )
