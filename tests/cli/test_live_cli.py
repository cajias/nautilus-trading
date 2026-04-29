"""``nt live`` registration + dispatch + validation tests.

Real-money execution is out of scope per the 2026-04-21 no-real-money
directive. These tests prove the CLI surface is wired correctly without
ever booting a ``TradingNode`` — the dispatch path always terminates in
:class:`NotImplementedError`, and validation failures map cleanly to
``typer.BadParameter``.
"""

from __future__ import annotations

from typer.testing import CliRunner

from nautilus_trading.cli import app

# ---------------------------------------------------------------------------
# Help / registration
# ---------------------------------------------------------------------------


def test_live_command_is_registered():
    """The `live` subcommand appears in `nt --help`. Surface-symmetry guard:
    proves the full `nt {backtest, paper-trade, live}` family is exposed."""
    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "live" in result.stdout


def test_all_three_runner_subcommands_registered():
    """Sub-project B.5's end-state shape: `nt --help` exposes all three
    runner subcommands. Catches accidental registration regressions in
    `cli/__init__.py` if a future PR shuffles imports."""
    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for cmd in ("backtest", "paper-trade", "live"):
        assert cmd in result.stdout, f"Missing `nt {cmd}` in --help output"


def test_live_help_renders_cleanly():
    """`nt live --help` renders without crashing and mentions --config."""
    runner = CliRunner()
    result = runner.invoke(app, ["live", "--help"])
    assert result.exit_code == 0
    assert "--config" in result.stdout


# ---------------------------------------------------------------------------
# Dispatch — happy path lands at NotImplementedError
# ---------------------------------------------------------------------------


def _valid_live_yaml() -> str:
    """Minimal valid LiveRunConfig YAML (i_understand_real_money: true)."""
    return (
        "strategy: ema_cross\n"
        "instrument_id: BTCUSDT.BINANCE\n"
        "bar_type: BTCUSDT.BINANCE-1-MINUTE-LAST-EXTERNAL\n"
        'trade_size: "0.001"\n'
        "i_understand_real_money: true\n"
        "params:\n"
        "  fast_ema: 12\n"
        "  slow_ema: 26\n"
    )


def test_live_config_dispatches_and_raises_not_implemented(tmp_path):
    """`nt live --config <valid-yaml>` reaches ``LiveStrategyRunner.main()``,
    which raises ``NotImplementedError`` per the 2026-04-21 directive.

    CliRunner with default ``catch_exceptions=True`` captures the
    NotImplementedError on ``result.exception`` rather than letting it
    propagate up the test, so we can assert on its message verbatim.
    """
    yaml_path = tmp_path / "run.yaml"
    yaml_path.write_text(_valid_live_yaml())

    runner = CliRunner()
    result = runner.invoke(app, ["live", "--config", str(yaml_path)])

    assert result.exit_code != 0, "Expected non-zero exit when main() raises"
    assert isinstance(result.exception, NotImplementedError), (
        f"Expected NotImplementedError; got {type(result.exception).__name__}: {result.exception}"
    )
    assert "2026-04-21" in str(result.exception)
    assert "directive" in str(result.exception).lower()


# ---------------------------------------------------------------------------
# Validation failures map to BadParameter (not raw exceptions)
# ---------------------------------------------------------------------------


def test_live_config_missing_file_is_usage_error(tmp_path):
    """Nonexistent config file → Typer exit_code != 0 mentioning the path.

    Served by ``typer.Option(exists=True)`` on ``--config``; this test
    guards that the option definition still carries the ``exists=True``
    flag (parallel to ``test_paper_trade_config_missing_file_is_usage_error``).
    """
    runner = CliRunner()
    bogus = tmp_path / "does-not-exist.yaml"
    result = runner.invoke(app, ["live", "--config", str(bogus)])
    assert result.exit_code != 0
    assert "does-not-exist" in result.output or "does not exist" in result.output


def test_live_config_missing_i_understand_real_money_is_usage_error(tmp_path):
    """YAML missing ``i_understand_real_money`` → BadParameter.

    The friction guard's whole point: a paste-error from a paper-trade
    YAML (which has no such field) cannot route through `nt live`.
    msgspec rejects the schema violation; the CLI funnels it through
    BadParameter so the operator sees a clean message.
    """
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
    result = runner.invoke(app, ["live", "--config", str(yaml_path)])
    assert result.exit_code != 0
    assert "i_understand_real_money" in result.output


def test_live_config_i_understand_real_money_false_is_usage_error(tmp_path):
    """Explicit ``i_understand_real_money: false`` → BadParameter.

    Second half of the friction guard: a user explicitly opting OUT
    cannot then route through the live path.
    """
    yaml_path = tmp_path / "run.yaml"
    yaml_path.write_text(
        "strategy: ema_cross\n"
        "instrument_id: BTCUSDT.BINANCE\n"
        "bar_type: BTCUSDT.BINANCE-1-MINUTE-LAST-EXTERNAL\n"
        'trade_size: "0.001"\n'
        "i_understand_real_money: false\n"
    )
    runner = CliRunner()
    result = runner.invoke(app, ["live", "--config", str(yaml_path)])
    assert result.exit_code != 0


def test_live_config_unknown_strategy_is_usage_error(tmp_path):
    """Unknown strategy in YAML → BadParameter listing the bad name."""
    yaml_path = tmp_path / "run.yaml"
    yaml_path.write_text(
        "strategy: nonexistent_bot\n"
        "instrument_id: BTCUSDT.BINANCE\n"
        "bar_type: BTCUSDT.BINANCE-1-MINUTE-LAST-EXTERNAL\n"
        'trade_size: "0.001"\n'
        "i_understand_real_money: true\n"
    )
    runner = CliRunner()
    result = runner.invoke(app, ["live", "--config", str(yaml_path)])
    assert result.exit_code != 0
    assert "nonexistent_bot" in result.output


def test_live_config_unknown_yaml_field_is_usage_error(tmp_path):
    """Unknown top-level YAML field → BadParameter (forbid_unknown_fields)."""
    yaml_path = tmp_path / "run.yaml"
    yaml_path.write_text(
        "strategy: ema_cross\n"
        "instrument_id: BTCUSDT.BINANCE\n"
        "bar_type: BTCUSDT.BINANCE-1-MINUTE-LAST-EXTERNAL\n"
        'trade_size: "0.001"\n'
        "i_understand_real_money: true\n"
        "bogus_field: 1\n"
    )
    runner = CliRunner()
    result = runner.invoke(app, ["live", "--config", str(yaml_path)])
    assert result.exit_code != 0
    assert "bogus_field" in result.output


# ---------------------------------------------------------------------------
# Build-once contract — pre-flight build runs exactly once before main()
# ---------------------------------------------------------------------------


def test_live_dispatch_builds_config_exactly_once(tmp_path, monkeypatch):
    """`nt live` must build the ``TradingNodeConfig`` exactly once.

    Same regression guard PR 1 introduced for paper-trade and PR 2
    extended for backtest (``bug_025``): the CLI does its eager
    build_config() pre-flight, then calls main(). main() must NOT
    re-build before raising — else once a future implementer fills
    main() in, the dual-build anti-pattern creeps back.
    """
    from nautilus_trading.live.strategy_runner import LiveStrategyRunner

    call_count = [0]
    original_build = LiveStrategyRunner.build_config

    def _counting_build(self):
        call_count[0] += 1
        return original_build(self)

    monkeypatch.setattr(LiveStrategyRunner, "build_config", _counting_build)

    yaml_path = tmp_path / "run.yaml"
    yaml_path.write_text(_valid_live_yaml())

    runner = CliRunner()
    result = runner.invoke(app, ["live", "--config", str(yaml_path)])

    # main() raises NotImplementedError — that's the expected terminal state.
    assert isinstance(result.exception, NotImplementedError)
    # But the eager pre-flight build must have run exactly once.
    assert call_count[0] == 1, (
        f"Expected build_config() to be called exactly once; got {call_count[0]}. "
        "The CLI's eager pre-flight is the only build site — main() must not re-build."
    )


def test_live_top_level_fields_override_params_block(tmp_path, monkeypatch):
    """Top-level YAML fields win over duplicates inside ``params:``.

    Same merge-direction contract as ``cli/paper_trade.py``. Captures the
    runner's params dict on construction to verify the merge applies
    before main() raises.
    """
    from nautilus_trading.live.strategy_runner import LiveStrategyRunner

    captured: list[dict] = []
    original_init = LiveStrategyRunner.__init__

    def _capture_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        captured.append(dict(self.params))

    monkeypatch.setattr(LiveStrategyRunner, "__init__", _capture_init)

    yaml_path = tmp_path / "run.yaml"
    yaml_path.write_text(
        "strategy: ema_cross\n"
        "instrument_id: BTCUSDT.BINANCE\n"
        "bar_type: BTCUSDT.BINANCE-1-MINUTE-LAST-EXTERNAL\n"
        'trade_size: "0.001"\n'
        "i_understand_real_money: true\n"
        "params:\n"
        "  instrument_id: ETHUSDT.BINANCE\n"
        "  fast_ema: 12\n"
        "  slow_ema: 26\n"
    )

    runner = CliRunner()
    result = runner.invoke(app, ["live", "--config", str(yaml_path)])
    # main() still raises — that's the contract — but capture happens first.
    assert isinstance(result.exception, NotImplementedError)
    assert len(captured) == 1
    # Top-level wins.
    assert captured[0]["instrument_id"] == "BTCUSDT.BINANCE"
    assert captured[0]["bar_type"] == "BTCUSDT.BINANCE-1-MINUTE-LAST-EXTERNAL"
    assert captured[0]["trade_size"] == "0.001"
    # Strategy-specific params still flow.
    assert captured[0]["fast_ema"] == 12
    assert captured[0]["slow_ema"] == 26
