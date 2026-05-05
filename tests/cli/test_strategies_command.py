"""Tests for the `nt strategies` listing CLI.

Sub-project C / PR 2: post-entry-point-discovery, ``nt strategies`` is the
diagnostic surface for "what strategies does this venv know about, and which
package did each come from". The 3 contract tests below pin that surface:

1. All 9 in-repo strategies appear in the listing.
2. The source package label (``nautilus-trading``) is shown alongside each entry.
3. The strategy-class import path is shown for each entry.
"""

from __future__ import annotations

from typer.testing import CliRunner

from nautilus_trading.cli import app

runner = CliRunner()


def test_strategies_command_lists_all_in_repo_strategies() -> None:
    """`nt strategies` lists each of the 9 in-repo strategies."""
    result = runner.invoke(app, ["strategies"])
    assert result.exit_code == 0, result.output

    expected = [
        "ema_cross",
        "grid_bot",
        "dca_bot",
        "timesfm_swing",
        "hybrid_sma_r10",
        "timesfm_grid",
        "rvs_swing",
        "shock_guard",
        "kronos",
    ]
    for name in expected:
        assert name in result.output, f"`nt strategies` output missing '{name}'"


def test_strategies_command_includes_source_package_for_each_entry() -> None:
    """Each listed strategy includes its source package name."""
    result = runner.invoke(app, ["strategies"])
    assert result.exit_code == 0, result.output
    # The source package for in-repo strategies is `nautilus-trading`.
    assert "nautilus-trading" in result.output


def test_strategies_command_includes_strategy_path_for_each_entry() -> None:
    """Each listed strategy includes its ``StrategySpec.strategy_path``."""
    result = runner.invoke(app, ["strategies"])
    assert result.exit_code == 0, result.output
    assert "strategies.forex.ema_cross:EMACrossStrategy" in result.output
    assert "strategies.crypto.kronos.strategy:KronosStrategy" in result.output
