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
