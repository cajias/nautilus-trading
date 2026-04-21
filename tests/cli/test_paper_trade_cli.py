"""`nt paper-trade` registration and argument-parsing smoke."""

from __future__ import annotations

from typer.testing import CliRunner

from nautilus_trading.cli import app


def test_paper_trade_command_is_registered():
    """The `paper-trade` subcommand appears in --help."""
    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "paper-trade" in result.stdout


def test_paper_trade_help_shows_required_args():
    """paper-trade --help mentions --strategy and --instrument-id."""
    runner = CliRunner()
    result = runner.invoke(app, ["paper-trade", "--help"])
    assert result.exit_code == 0
    assert "--strategy" in result.stdout
    assert "--instrument-id" in result.stdout


def test_paper_trade_unknown_strategy_exits_nonzero():
    """Unknown strategy name → usage error."""
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "paper-trade",
            "--strategy",
            "nonexistent_strategy",
            "--instrument-id",
            "BTCUSDT.BINANCE",
            "--bar-type",
            "BTCUSDT.BINANCE-1-MINUTE-LAST-EXTERNAL",
            "--trade-size",
            "0.001",
        ],
    )
    assert result.exit_code != 0
