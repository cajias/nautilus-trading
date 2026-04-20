"""Shared helpers for nautilus_trading.cli.*. No strategy-specific logic lives here."""

from __future__ import annotations

import sys
from pathlib import Path

# Maps strategy module names to their class names
_STRATEGY_CLASSES: dict[str, tuple[str, str]] = {
    "ema_cross": ("EMACrossStrategy", "EMACrossConfig"),
    "grid_bot": ("GridBotStrategy", "GridBotConfig"),
    "dca_bot": ("DCABotStrategy", "DCABotConfig"),
    "timesfm_swing": ("TimesFMSwingStrategy", "TimesFMSwingConfig"),
    "hybrid_sma_r10": ("HybridSMAR10Strategy", "HybridSMAR10Config"),
    "timesfm_grid": ("TimesFMGridStrategy", "TimesFMGridConfig"),
    "rvs_swing": ("RVSSwingStrategy", "RVSSwingConfig"),
    "shock_guard": ("ShockGuardStrategy", "ShockGuardConfig"),
}


def _ensure_project_root_on_path() -> None:
    """Add the project root (parent of the ``nautilus/`` package dir) to sys.path.

    This allows strategy modules like ``strategies.forex.ema_cross`` that live at the
    project root to be imported via ``ImportableStrategyConfig``.
    """
    # Walk up from this file:
    #   nautilus/src/nautilus_trading/cli/_common.py -> project root is 4 levels up
    project_root = str(Path(__file__).resolve().parents[4])
    if project_root not in sys.path:
        sys.path.insert(0, project_root)


def _resolve_strategy_paths(module_path: str) -> tuple[str, str]:
    """Resolve a module path like 'strategies.crypto.grid_bot' to full import paths.

    If the path already contains ':', it's treated as an explicit import path.
    Otherwise, the strategy/config class names are inferred from the module name.
    """
    if ":" in module_path:
        module, cls = module_path.rsplit(":", 1)
        config_cls = cls.replace("Strategy", "Config")
        return module_path, f"{module}:{config_cls}"

    module_name = module_path.rsplit(".", 1)[-1]
    if module_name in _STRATEGY_CLASSES:
        strategy_cls, config_cls = _STRATEGY_CLASSES[module_name]
    else:
        # Fallback: PascalCase the module name
        parts = module_name.split("_")
        base = "".join(p.capitalize() for p in parts)
        strategy_cls = f"{base}Strategy"
        config_cls = f"{base}Config"

    return f"{module_path}:{strategy_cls}", f"{module_path}:{config_cls}"
