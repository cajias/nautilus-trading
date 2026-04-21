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


def test_paper_trade_ema_cross_dispatches_to_runner(monkeypatch):
    """Invoking `nt paper-trade --strategy ema_cross ...` builds an EMACross runner
    and calls .main(). We swap .main() for a recorder double so we don't hit Testnet.
    """
    # Ensure project root is on sys.path so `strategies.*` is importable here.
    import sys
    from pathlib import Path

    project_root = str(Path(__file__).resolve().parents[2])
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    calls = []

    def _recording_main(self):
        calls.append(("ema_cross", self.instrument_id, self.fast_ema, self.slow_ema))

    from strategies.crypto.ema_cross_paper import EMACrossPaperTradeRunner

    monkeypatch.setattr(EMACrossPaperTradeRunner, "main", _recording_main)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "paper-trade",
            "--strategy",
            "ema_cross",
            "--instrument-id",
            "BTCUSDT.BINANCE",
            "--bar-type",
            "BTCUSDT.BINANCE-1-MINUTE-LAST-EXTERNAL",
            "--trade-size",
            "0.001",
            "--fast-ema",
            "12",
            "--slow-ema",
            "26",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert calls == [("ema_cross", "BTCUSDT.BINANCE", 12, 26)]
