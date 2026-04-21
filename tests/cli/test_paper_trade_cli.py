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


def test_paper_trade_grid_bot_missing_required_args_is_usage_error(monkeypatch):
    """Omitting --upper-price/--lower-price for grid_bot yields a Typer usage
    error (BadParameter), not a raw ValueError traceback. Guards the builder
    boundary: GridBotConfigBuilder.build raises ValueError when required args
    are missing; the CLI must remap that to a user-friendly usage error.
    """
    from strategies.crypto.grid_bot_paper import GridBotPaperTradeRunner

    def _should_not_run(self):
        raise AssertionError("main() must not run when required args are missing")

    monkeypatch.setattr(GridBotPaperTradeRunner, "main", _should_not_run)

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
            # deliberately omitting --upper-price / --lower-price / --grid-levels
        ],
    )
    assert result.exit_code != 0
    assert "upper-price" in result.output or "upper_price" in result.output


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


def test_paper_trade_hybrid_sma_r10_dispatches_to_runner(monkeypatch):
    """Invoking `nt paper-trade --strategy hybrid_sma_r10 ...` builds a HybridSMA
    runner and calls .main(). Hybrid SMA uses --sma-fast/--sma-slow/--stop-fast/
    --stop-slow and does NOT propagate --trade-size into the runner (strategy
    sizes from equity).
    """
    calls = []

    def _recording_main(self):
        calls.append(
            (
                "hybrid_sma_r10",
                self.instrument_id,
                self.sma_fast,
                self.sma_slow,
                self.stop_fast,
                self.stop_slow,
            )
        )

    from strategies.crypto.hybrid_sma_r10_paper import HybridSMAR10PaperTradeRunner

    monkeypatch.setattr(HybridSMAR10PaperTradeRunner, "main", _recording_main)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "paper-trade",
            "--strategy",
            "hybrid_sma_r10",
            "--instrument-id",
            "BTCUSDT.BINANCE",
            "--bar-type",
            "BTCUSDT.BINANCE-1-MINUTE-LAST-EXTERNAL",
            "--trade-size",
            "0.001",
            "--sma-fast",
            "10",
            "--sma-slow",
            "30",
            "--stop-fast",
            "0.05",
            "--stop-slow",
            "0.10",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert calls == [("hybrid_sma_r10", "BTCUSDT.BINANCE", 10, 30, "0.05", "0.10")]


def test_paper_trade_hybrid_sma_r10_missing_required_args_is_usage_error(monkeypatch):
    """Omitting --sma-fast/--sma-slow/--stop-fast/--stop-slow for hybrid_sma_r10
    yields a Typer usage error (BadParameter), not a raw ValueError traceback.
    Guards the builder boundary: HybridSMAConfigBuilder.build raises ValueError
    when required args are missing; the CLI must remap that to a user-friendly
    usage error.
    """
    from strategies.crypto.hybrid_sma_r10_paper import HybridSMAR10PaperTradeRunner

    def _should_not_run(self):
        raise AssertionError("main() must not run when required args are missing")

    monkeypatch.setattr(HybridSMAR10PaperTradeRunner, "main", _should_not_run)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "paper-trade",
            "--strategy",
            "hybrid_sma_r10",
            "--instrument-id",
            "BTCUSDT.BINANCE",
            "--bar-type",
            "BTCUSDT.BINANCE-1-MINUTE-LAST-EXTERNAL",
            "--trade-size",
            "0.001",
            # deliberately omit --sma-fast / --sma-slow / --stop-fast / --stop-slow
        ],
    )
    assert result.exit_code != 0
    assert "sma_fast" in result.output or "sma-fast" in result.output


def test_paper_trade_timesfm_grid_dispatches_to_runner(monkeypatch):
    """Invoking `nt paper-trade --strategy timesfm_grid ...` builds a TimesFMGrid
    runner and calls .main(). TimesFMGrid uses only base args — all ML/grid
    parameters have Config defaults, so no new Typer options are needed.
    """
    calls = []

    def _recording_main(self):
        calls.append(
            (
                "timesfm_grid",
                self.instrument_id,
                self.trade_size,
            )
        )

    from strategies.crypto.timesfm_grid_paper import TimesFMGridPaperTradeRunner

    monkeypatch.setattr(TimesFMGridPaperTradeRunner, "main", _recording_main)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "paper-trade",
            "--strategy",
            "timesfm_grid",
            "--instrument-id",
            "BTCUSDT.BINANCE",
            "--bar-type",
            "BTCUSDT.BINANCE-1-MINUTE-LAST-EXTERNAL",
            "--trade-size",
            "0.001",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert calls == [("timesfm_grid", "BTCUSDT.BINANCE", "0.001")]


def test_paper_trade_rvs_swing_dispatches_to_runner(monkeypatch):
    """Invoking `nt paper-trade --strategy rvs_swing ...` builds an RVSSwing
    runner and calls .main(). RVSSwing uses only base args — all anomaly/stop/
    EMA parameters have Config defaults, so no new Typer options are needed.
    """
    calls = []

    def _recording_main(self):
        calls.append(
            (
                "rvs_swing",
                self.instrument_id,
                self.trade_size,
            )
        )

    from strategies.crypto.rvs_swing_paper import RVSSwingPaperTradeRunner

    monkeypatch.setattr(RVSSwingPaperTradeRunner, "main", _recording_main)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "paper-trade",
            "--strategy",
            "rvs_swing",
            "--instrument-id",
            "BTCUSDT.BINANCE",
            "--bar-type",
            "BTCUSDT.BINANCE-1-MINUTE-LAST-EXTERNAL",
            "--trade-size",
            "0.001",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert calls == [("rvs_swing", "BTCUSDT.BINANCE", "0.001")]


def test_paper_trade_shock_guard_dispatches_to_runner(monkeypatch):
    """Invoking `nt paper-trade --strategy shock_guard ...` builds a ShockGuard
    runner and calls .main(). ShockGuard uses only base args — all allocation/
    shock parameters have Config defaults, so no new Typer options are needed.
    """
    calls = []

    def _recording_main(self):
        calls.append(
            (
                "shock_guard",
                self.instrument_id,
                self.trade_size,
            )
        )

    from strategies.crypto.shock_guard_paper import ShockGuardPaperTradeRunner

    monkeypatch.setattr(ShockGuardPaperTradeRunner, "main", _recording_main)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "paper-trade",
            "--strategy",
            "shock_guard",
            "--instrument-id",
            "BTCUSDT.BINANCE",
            "--bar-type",
            "BTCUSDT.BINANCE-1-MINUTE-LAST-EXTERNAL",
            "--trade-size",
            "0.001",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert calls == [("shock_guard", "BTCUSDT.BINANCE", "0.001")]
