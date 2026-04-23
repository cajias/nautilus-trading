"""Each committed config in configs/paper/ dispatches to its runner.

Avoids Testnet boot by monkeypatching every runner's `.main()` to a
recorder. This locks the YAML schema: if any field name drifts, exactly
one parametrized case fails.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from typer.testing import CliRunner

from nautilus_trading.cli import app

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIGS_DIR = REPO_ROOT / "configs" / "paper"


CONFIG_CASES = [
    ("ema_cross.yaml", "strategies.crypto.ema_cross_paper", "EMACrossPaperTradeRunner"),
    ("grid_bot.yaml", "strategies.crypto.grid_bot_paper", "GridBotPaperTradeRunner"),
    ("dca_bot.yaml", "strategies.crypto.dca_bot_paper", "DCABotPaperTradeRunner"),
    ("timesfm_swing.yaml", "strategies.crypto.timesfm_swing_paper", "TimesFMSwingPaperTradeRunner"),
    (
        "hybrid_sma_r10.yaml",
        "strategies.crypto.hybrid_sma_r10_paper",
        "HybridSMAR10PaperTradeRunner",
    ),
    ("timesfm_grid.yaml", "strategies.crypto.timesfm_grid_paper", "TimesFMGridPaperTradeRunner"),
    ("rvs_swing.yaml", "strategies.crypto.rvs_swing_paper", "RVSSwingPaperTradeRunner"),
    ("shock_guard.yaml", "strategies.crypto.shock_guard_paper", "ShockGuardPaperTradeRunner"),
    ("kronos.yaml", "strategies.crypto.kronos.paper_runner", "KronosPaperTradeRunner"),
]


@pytest.mark.parametrize("filename,module,classname", CONFIG_CASES)
def test_committed_config_dispatches_to_runner(filename, module, classname, monkeypatch):
    """configs/paper/<name>.yaml instantiates <classname> and calls .main()."""
    mod = importlib.import_module(module)
    runner_cls = getattr(mod, classname)

    calls = []
    monkeypatch.setattr(runner_cls, "main", lambda self: calls.append(self))

    path = CONFIGS_DIR / filename
    assert path.exists(), f"Missing committed config: {path}"

    cli_runner = CliRunner()
    result = cli_runner.invoke(app, ["paper-trade", "--config", str(path)])
    assert result.exit_code == 0, f"{filename}: {result.stdout}"
    assert len(calls) == 1, f"{filename}: expected one .main() call, got {len(calls)}"
