"""Each committed config in configs/paper/ dispatches to a runner.

Avoids Testnet boot by monkeypatching :class:`PaperTradeStrategyRunner`'s
``.main()`` to a recorder. This locks the YAML schema: if any field name
drifts or a strategy name disappears from :data:`STRATEGY_SPECS`, exactly
one parametrized case fails.

Sub-project B.5: the CLI now routes every strategy through the generic
:class:`PaperTradeStrategyRunner` — no per-shim lookup — so the recorder
attaches once and every YAML parametrization exercises the same class.
The per-strategy identity lives in ``runner.spec.name`` on the recorded
call, which is asserted against the expected spec key.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from nautilus_trading.cli import app

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIGS_DIR = REPO_ROOT / "configs" / "paper"


# (yaml filename, expected STRATEGY_SPECS key)
CONFIG_CASES = [
    ("ema_cross.yaml", "ema_cross"),
    ("grid_bot.yaml", "grid_bot"),
    ("dca_bot.yaml", "dca_bot"),
    ("timesfm_swing.yaml", "timesfm_swing"),
    ("hybrid_sma_r10.yaml", "hybrid_sma_r10"),
    ("timesfm_grid.yaml", "timesfm_grid"),
    ("rvs_swing.yaml", "rvs_swing"),
    ("shock_guard.yaml", "shock_guard"),
    ("kronos.yaml", "kronos"),
]


@pytest.mark.parametrize(("filename", "spec_name"), CONFIG_CASES)
def test_committed_config_dispatches_to_runner(filename, spec_name, monkeypatch):
    """configs/paper/<name>.yaml instantiates PaperTradeStrategyRunner with
    ``spec.name == <name>`` and calls ``.main()`` exactly once."""
    from nautilus_trading.paper_trade.strategy_runner import PaperTradeStrategyRunner

    calls: list[PaperTradeStrategyRunner] = []
    monkeypatch.setattr(PaperTradeStrategyRunner, "main", lambda self: calls.append(self))

    path = CONFIGS_DIR / filename
    assert path.exists(), f"Missing committed config: {path}"

    cli_runner = CliRunner()
    result = cli_runner.invoke(app, ["paper-trade", "--config", str(path)])
    assert result.exit_code == 0, f"{filename}: {result.stdout}"
    assert len(calls) == 1, f"{filename}: expected one .main() call, got {len(calls)}"
    assert calls[0].spec.name == spec_name
