"""Characterization tests for nautilus_trading.cli._common."""

from __future__ import annotations

import sys
from pathlib import Path

from nautilus_trading.cli._common import (
    _ensure_project_root_on_path,
    _resolve_strategy_paths,
)


def test_ensure_project_root_on_path_is_idempotent(monkeypatch):
    import nautilus_trading.cli._common as _mod

    project_root = str(Path(_mod.__file__).resolve().parents[4])
    monkeypatch.setattr(sys, "path", [p for p in sys.path if p != project_root])

    _ensure_project_root_on_path()
    _ensure_project_root_on_path()

    assert sys.path.count(project_root) == 1


def test_resolve_strategy_paths_known_module():
    strategy, config = _resolve_strategy_paths("strategies.forex.ema_cross")
    assert strategy == "strategies.forex.ema_cross:EMACrossStrategy"
    assert config == "strategies.forex.ema_cross:EMACrossConfig"


def test_resolve_strategy_paths_explicit_import_path():
    strategy, config = _resolve_strategy_paths("strategies.crypto.grid_bot:GridBotStrategy")
    assert strategy == "strategies.crypto.grid_bot:GridBotStrategy"
    assert config == "strategies.crypto.grid_bot:GridBotConfig"


def test_resolve_strategy_paths_pascal_case_fallback():
    strategy, config = _resolve_strategy_paths("strategies.crypto.foo_bar")
    assert strategy == "strategies.crypto.foo_bar:FooBarStrategy"
    assert config == "strategies.crypto.foo_bar:FooBarConfig"


def test_resolve_strategy_paths_timesfm_grid_acronym():
    strategy, config = _resolve_strategy_paths("strategies.crypto.timesfm_grid")
    assert strategy == "strategies.crypto.timesfm_grid:TimesFMGridStrategy"
    assert config == "strategies.crypto.timesfm_grid:TimesFMGridConfig"


def test_resolve_strategy_paths_rvs_swing_acronym():
    strategy, config = _resolve_strategy_paths("strategies.crypto.rvs_swing")
    assert strategy == "strategies.crypto.rvs_swing:RVSSwingStrategy"
    assert config == "strategies.crypto.rvs_swing:RVSSwingConfig"


def test_resolve_strategy_paths_shock_guard_explicit():
    strategy, config = _resolve_strategy_paths("strategies.crypto.shock_guard")
    assert strategy == "strategies.crypto.shock_guard:ShockGuardStrategy"
    assert config == "strategies.crypto.shock_guard:ShockGuardConfig"


def test_resolve_strategy_paths_hybrid_sma_r10_acronym():
    strategy, config = _resolve_strategy_paths("strategies.crypto.hybrid_sma_r10")
    assert strategy == "strategies.crypto.hybrid_sma_r10:HybridSMAR10Strategy"
    assert config == "strategies.crypto.hybrid_sma_r10:HybridSMAR10Config"
