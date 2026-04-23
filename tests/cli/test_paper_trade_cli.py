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


def test_paper_trade_help_shows_config_option():
    """paper-trade --help mentions --config (single YAML-config option)."""
    runner = CliRunner()
    result = runner.invoke(app, ["paper-trade", "--help"])
    assert result.exit_code == 0
    assert "--config" in result.stdout


def test_paper_trade_config_file_dispatches_to_runner(tmp_path, monkeypatch):
    """`nt paper-trade --config run.yaml` loads the YAML, instantiates the
    right runner, and calls .main(). Sanity check for the YAML dispatch path;
    per-strategy coverage lives in tests/cli/test_paper_trade_configs.py.
    """
    calls = []

    def _recording_main(self):
        calls.append(("ema_cross", self.instrument_id, self.fast_ema, self.slow_ema))

    from strategies.crypto.ema_cross_paper import EMACrossPaperTradeRunner

    monkeypatch.setattr(EMACrossPaperTradeRunner, "main", _recording_main)

    yaml_path = tmp_path / "run.yaml"
    yaml_path.write_text(
        "strategy: ema_cross\n"
        "instrument_id: BTCUSDT.BINANCE\n"
        "bar_type: BTCUSDT.BINANCE-1-MINUTE-LAST-EXTERNAL\n"
        'trade_size: "0.001"\n'
        "params:\n"
        "  fast_ema: 12\n"
        "  slow_ema: 26\n"
    )

    runner = CliRunner()
    result = runner.invoke(app, ["paper-trade", "--config", str(yaml_path)])
    assert result.exit_code == 0, result.stdout
    assert calls == [("ema_cross", "BTCUSDT.BINANCE", 12, 26)]
