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

    B.5 migration: the CLI now dispatches through the generic
    ``PaperTradeStrategyRunner`` (not per-strategy shims), so the monkeypatch
    attaches to that class and the recorded tuple reads fields off the merged
    ``params`` dict rather than per-runner dataclass attributes.
    """
    calls = []

    def _recording_main(self):
        calls.append(
            (
                self.spec.name,
                self.params["instrument_id"],
                self.params["fast_ema"],
                self.params["slow_ema"],
            )
        )

    from nautilus_trading.paper_trade.strategy_runner import PaperTradeStrategyRunner

    monkeypatch.setattr(PaperTradeStrategyRunner, "main", _recording_main)

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


def test_paper_trade_config_missing_file_is_usage_error(tmp_path):
    """Nonexistent config file → Typer exit_code != 0 mentioning the path.

    This is served by typer.Option(exists=True) on --config; the test guards
    that the option definition still carries the `exists=True` flag.
    """
    runner = CliRunner()
    bogus = tmp_path / "does-not-exist.yaml"
    result = runner.invoke(app, ["paper-trade", "--config", str(bogus)])
    assert result.exit_code != 0
    assert "does-not-exist" in result.output or "does not exist" in result.output


def test_paper_trade_config_unknown_strategy_is_usage_error(tmp_path):
    """Unknown strategy in YAML → BadParameter listing valid names."""
    yaml_path = tmp_path / "run.yaml"
    yaml_path.write_text(
        'strategy: nonexistent_bot\ninstrument_id: X\nbar_type: Y\ntrade_size: "0.001"\n'
    )
    runner = CliRunner()
    result = runner.invoke(app, ["paper-trade", "--config", str(yaml_path)])
    assert result.exit_code != 0
    assert "nonexistent_bot" in result.output


def test_paper_trade_config_unknown_yaml_field_is_usage_error(tmp_path):
    """Unknown top-level YAML field → BadParameter (not raw ValidationError)."""
    yaml_path = tmp_path / "run.yaml"
    yaml_path.write_text(
        'strategy: ema_cross\ninstrument_id: X\nbar_type: Y\ntrade_size: "0.001"\nbogus_field: 1\n'
    )
    runner = CliRunner()
    result = runner.invoke(app, ["paper-trade", "--config", str(yaml_path)])
    assert result.exit_code != 0
    assert "bogus_field" in result.output
