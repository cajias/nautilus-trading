"""Tests for the R11+ competition template submission.

These tests exist so that the template directory is a self-contained
validator fixture: ``pytest competition/TEMPLATE/tests/`` must pass on a
clean checkout. Real submissions should replace this file with behavioural
tests for their own strategy (regression, edge cases, state machine
transitions, etc.).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from nautilus_trader.model.data import BarType
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.trading.strategy import Strategy


_TEMPLATE_DIR = Path(__file__).resolve().parents[1]
_STRATEGY_PATH = _TEMPLATE_DIR / "strategy.py"

REQUIRED_MANIFEST_KEYS = {
    "strategy_class_name",
    "config_class_name",
    "instrument_id",
    "bar_type",
    "default_config",
    "description",
}


def _load_template_module():
    """Load competition/TEMPLATE/strategy.py without needing it on sys.path."""
    spec = importlib.util.spec_from_file_location(
        "competition_template_strategy", _STRATEGY_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to build spec for {_STRATEGY_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def template_module():
    return _load_template_module()


def test_manifest_has_required_keys(template_module) -> None:
    """MANIFEST must declare all six keys the evaluator reads."""
    manifest = template_module.MANIFEST
    assert isinstance(manifest, dict), "MANIFEST must be a dict"
    missing = REQUIRED_MANIFEST_KEYS - set(manifest.keys())
    assert not missing, f"MANIFEST is missing required keys: {sorted(missing)}"


def test_strategy_class_exists(template_module) -> None:
    """The class named in MANIFEST must exist and subclass Strategy."""
    class_name = template_module.MANIFEST["strategy_class_name"]
    strategy_cls = getattr(template_module, class_name, None)
    assert strategy_cls is not None, (
        f"Strategy class {class_name!r} not found in strategy.py"
    )
    assert issubclass(strategy_cls, Strategy), (
        f"{class_name} must subclass nautilus_trader.trading.strategy.Strategy"
    )


def test_config_class_is_frozen(template_module) -> None:
    """The config class must be immutable (frozen=True on the msgspec Struct)."""
    config_name = template_module.MANIFEST["config_class_name"]
    config_cls = getattr(template_module, config_name, None)
    assert config_cls is not None, (
        f"Config class {config_name!r} not found in strategy.py"
    )
    struct_config = getattr(config_cls, "__struct_config__", None)
    assert struct_config is not None, (
        f"{config_name} does not look like a msgspec Struct "
        "(no __struct_config__). Did you forget StrategyConfig?"
    )
    assert struct_config.frozen is True, (
        f"{config_name} must be declared with frozen=True"
    )


def test_config_instantiates(template_module) -> None:
    """The config class must instantiate with manifest values."""
    config_name = template_module.MANIFEST["config_class_name"]
    config_cls = getattr(template_module, config_name)

    instrument_id = InstrumentId.from_str(template_module.MANIFEST["instrument_id"])
    bar_type = BarType.from_str(template_module.MANIFEST["bar_type"])

    config = config_cls(
        instrument_id=instrument_id,
        bar_type=bar_type,
        **template_module.MANIFEST["default_config"],
    )
    assert config.instrument_id == instrument_id
    assert config.bar_type == bar_type
