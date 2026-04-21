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
    # `strategies.*` is importable because tests/conftest.py adds the repo root to sys.path.
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


def test_paper_trade_grid_bot_dispatches_to_runner(monkeypatch):
    """Invoking `nt paper-trade --strategy grid_bot ...` builds a GridBot runner
    and calls .main(). Grid options must dispatch conditionally — no ema args.
    """
    calls = []

    def _recording_main(self):
        calls.append(
            (
                "grid_bot",
                self.instrument_id,
                self.upper_price,
                self.lower_price,
                self.grid_levels,
            )
        )

    from strategies.crypto.grid_bot_paper import GridBotPaperTradeRunner

    monkeypatch.setattr(GridBotPaperTradeRunner, "main", _recording_main)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "paper-trade",
            "--strategy",
            "grid_bot",
            "--instrument-id",
            "BTCUSDT.BINANCE",
            "--bar-type",
            "BTCUSDT.BINANCE-1-MINUTE-LAST-EXTERNAL",
            "--trade-size",
            "0.001",
            "--upper-price",
            "72000",
            "--lower-price",
            "60000",
            "--grid-levels",
            "8",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert calls == [("grid_bot", "BTCUSDT.BINANCE", "72000", "60000", 8)]


def test_paper_trade_dca_bot_dispatches_to_runner(monkeypatch):
    """Invoking `nt paper-trade --strategy dca_bot ...` builds a DCABot runner
    and calls .main(). DCA options dispatch conditionally — no ema/grid args.
    """
    calls = []

    def _recording_main(self):
        calls.append(
            (
                "dca_bot",
                self.instrument_id,
                self.buy_interval_bars,
                self.buy_amount,
            )
        )

    from strategies.crypto.dca_bot_paper import DCABotPaperTradeRunner

    monkeypatch.setattr(DCABotPaperTradeRunner, "main", _recording_main)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "paper-trade",
            "--strategy",
            "dca_bot",
            "--instrument-id",
            "BTCUSDT.BINANCE",
            "--bar-type",
            "BTCUSDT.BINANCE-1-MINUTE-LAST-EXTERNAL",
            "--trade-size",
            "0.001",
            "--buy-interval-bars",
            "60",
            "--buy-amount",
            "10",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert calls == [("dca_bot", "BTCUSDT.BINANCE", 60, "10")]


def test_paper_trade_timesfm_swing_dispatches_to_runner(monkeypatch):
    """Invoking `nt paper-trade --strategy timesfm_swing ...` builds a TimesFMSwing
    runner and calls .main(). TimesFM reuses --fast-ema/--slow-ema (no new options).
    """
    calls = []

    def _recording_main(self):
        calls.append(
            (
                "timesfm_swing",
                self.instrument_id,
                self.fast_ema,
                self.slow_ema,
            )
        )

    from strategies.crypto.timesfm_swing_paper import TimesFMSwingPaperTradeRunner

    monkeypatch.setattr(TimesFMSwingPaperTradeRunner, "main", _recording_main)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "paper-trade",
            "--strategy",
            "timesfm_swing",
            "--instrument-id",
            "BTCUSDT.BINANCE",
            "--bar-type",
            "BTCUSDT.BINANCE-1-MINUTE-LAST-EXTERNAL",
            "--trade-size",
            "0.001",
            "--fast-ema",
            "5",
            "--slow-ema",
            "30",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert calls == [("timesfm_swing", "BTCUSDT.BINANCE", 5, 30)]
