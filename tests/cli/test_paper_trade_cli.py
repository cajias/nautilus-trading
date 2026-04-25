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
    right runner, and dispatches the validated config to ``run_paper_trade``.
    Sanity check for the YAML dispatch path; per-strategy coverage lives in
    tests/cli/test_paper_trade_configs.py.

    B.5 PR 2 migration: the CLI now builds the ``TradingNodeConfig`` once
    (via ``runner.build_config()``) and passes it directly to
    ``run_paper_trade(config)`` — the runner no longer carries a ``.main()``
    boot wrapper. Tests capture the runner instance via ``__init__`` and
    no-op the boot at the source module so the lazy ``from`` import in
    ``cli/paper_trade.py`` picks up the patched function.
    """
    from nautilus_trading.paper_trade.strategy_runner import PaperTradeStrategyRunner

    runners: list[PaperTradeStrategyRunner] = []
    original_init = PaperTradeStrategyRunner.__init__

    def _capturing_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        runners.append(self)

    monkeypatch.setattr(PaperTradeStrategyRunner, "__init__", _capturing_init)
    monkeypatch.setattr(
        "nautilus_trading.paper_trade.node_config.run_paper_trade",
        lambda config: None,
    )

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

    cli_runner = CliRunner()
    result = cli_runner.invoke(app, ["paper-trade", "--config", str(yaml_path)])
    assert result.exit_code == 0, result.stdout
    assert len(runners) == 1
    captured = runners[0]
    assert captured.spec.name == "ema_cross"
    assert captured.params["instrument_id"] == "BTCUSDT.BINANCE"
    assert captured.params["fast_ema"] == 12
    assert captured.params["slow_ema"] == 26


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


def test_paper_trade_dispatch_builds_config_exactly_once(tmp_path, monkeypatch):
    """``nt paper-trade --config <yaml>`` must build the ``TradingNodeConfig``
    exactly once per successful invocation.

    Regression guard against the dual-build anti-pattern flagged by
    /ultrareview on PR #41 (``bug_025``): the CLI used to call
    ``runner.build_config()`` eagerly for friendly-error mapping AND
    ``runner.main()`` (which re-built internally), doing the same msgspec
    + ImportableActorConfig + builder work twice. Locking it to one call
    prevents the same anti-pattern from creeping back into the parallel
    backtest CLI in subsequent PRs.
    """
    from nautilus_trading.paper_trade.strategy_runner import PaperTradeStrategyRunner

    call_count = [0]
    original_build = PaperTradeStrategyRunner.build_config

    def _counting_build(self):
        call_count[0] += 1
        return original_build(self)

    monkeypatch.setattr(PaperTradeStrategyRunner, "build_config", _counting_build)
    # No-op the boot so we don't actually start a TradingNode. Patch every
    # binding site of ``run_paper_trade`` because the current code imports it
    # at module level in ``strategy_runner.py`` (top-level ``from`` binds it
    # into that module's namespace), and the post-refactor code will import
    # it lazily inside ``cli/paper_trade.py``. ``raising=False`` lets the same
    # test run cleanly across both shapes.
    monkeypatch.setattr(
        "nautilus_trading.paper_trade.node_config.run_paper_trade",
        lambda config: None,
    )
    monkeypatch.setattr(
        "nautilus_trading.paper_trade.strategy_runner.run_paper_trade",
        lambda config: None,
        raising=False,
    )

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

    cli_runner = CliRunner()
    result = cli_runner.invoke(app, ["paper-trade", "--config", str(yaml_path)])
    assert result.exit_code == 0, result.stdout
    assert call_count[0] == 1, (
        f"Expected build_config() to be called exactly once; got {call_count[0]}. "
        "The CLI must reuse the eager-validated config rather than rebuilding it."
    )


def test_paper_trade_top_level_fields_override_params_block(tmp_path, monkeypatch):
    """Top-level YAML fields (``instrument_id`` / ``bar_type`` / ``trade_size``)
    are the canonical source of truth. If a user mistakenly drops one of
    those keys inside the per-strategy ``params:`` block, the top-level
    field must win at the merge step in ``cli/paper_trade.py``.

    Regression guard: PR-41 review-round caught a merge-direction bug where
    ``**run_config.params`` came AFTER the top-level fields and could
    silently shadow them.
    """
    from nautilus_trading.paper_trade.strategy_runner import PaperTradeStrategyRunner

    captured: list[dict] = []
    original_init = PaperTradeStrategyRunner.__init__

    def _capture_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        captured.append(dict(self.params))

    monkeypatch.setattr(PaperTradeStrategyRunner, "__init__", _capture_init)
    monkeypatch.setattr(
        "nautilus_trading.paper_trade.node_config.run_paper_trade",
        lambda config: None,
    )

    # Top-level says BTCUSDT.BINANCE; params block tries (incorrectly) to
    # override it with a different instrument_id.
    yaml_path = tmp_path / "run.yaml"
    yaml_path.write_text(
        "strategy: ema_cross\n"
        "instrument_id: BTCUSDT.BINANCE\n"
        "bar_type: BTCUSDT.BINANCE-1-MINUTE-LAST-EXTERNAL\n"
        'trade_size: "0.001"\n'
        "params:\n"
        "  instrument_id: ETHUSDT.BINANCE\n"
        "  fast_ema: 12\n"
        "  slow_ema: 26\n"
    )

    cli_runner = CliRunner()
    result = cli_runner.invoke(app, ["paper-trade", "--config", str(yaml_path)])
    assert result.exit_code == 0, result.stdout
    assert len(captured) == 1
    # Top-level wins; the params-block override is harmlessly overwritten.
    assert captured[0]["instrument_id"] == "BTCUSDT.BINANCE"
    assert captured[0]["bar_type"] == "BTCUSDT.BINANCE-1-MINUTE-LAST-EXTERNAL"
    assert captured[0]["trade_size"] == "0.001"
    # Strategy-specific params still flow through.
    assert captured[0]["fast_ema"] == 12
    assert captured[0]["slow_ema"] == 26
