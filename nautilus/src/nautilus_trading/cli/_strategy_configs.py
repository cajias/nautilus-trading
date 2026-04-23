"""Deprecated — re-exports from :mod:`nautilus_trading.cli._strategy_specs`.

Sub-project A shipped the 8 concrete strategy-config builders here.
Sub-project B.5 unified them (plus import-path strings and actor wiring)
into a single ``StrategySpec`` registry in ``cli/_strategy_specs.py``.

This shim keeps ``from nautilus_trading.cli._strategy_configs import ...``
working for downstream code (``cli/paper_trade.py``, the 8 ``*_paper.py``
runners, and ``tests/test_strategy_configs.py``) until Task C of PR 1
migrates callers and removes the shim.

Prefer importing directly from ``cli._strategy_specs`` in new code.
"""

from __future__ import annotations

from nautilus_trading.cli._strategy_specs import (
    STRATEGY_BUILDERS,
    DCABotConfigBuilder,
    EMAConfigBuilder,
    GridBotConfigBuilder,
    HybridSMAConfigBuilder,
    RVSSwingConfigBuilder,
    ShockGuardConfigBuilder,
    StrategyConfigBuilder,
    TimesFMConfigBuilder,
    TimesFMGridConfigBuilder,
)

__all__ = [
    "DCABotConfigBuilder",
    "EMAConfigBuilder",
    "GridBotConfigBuilder",
    "HybridSMAConfigBuilder",
    "RVSSwingConfigBuilder",
    "STRATEGY_BUILDERS",
    "ShockGuardConfigBuilder",
    "StrategyConfigBuilder",
    "TimesFMConfigBuilder",
    "TimesFMGridConfigBuilder",
]
