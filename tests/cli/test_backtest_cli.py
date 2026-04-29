"""``nt backtest`` CLI tests — registration, dispatch, and the
deprecation contract for the legacy ``--strategy`` path.

The new ``--config`` path is the canonical entry point post-B.5 PR 2;
the legacy path is retained for one release behind a
``DeprecationWarning`` and will be removed in PR 4.
"""

from __future__ import annotations

import warnings
from pathlib import Path

from typer.testing import CliRunner

from nautilus_trading.cli import app

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIGS_DIR = REPO_ROOT / "configs" / "backtest"


def test_backtest_command_is_registered():
    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "backtest" in result.stdout


def test_backtest_help_shows_config_option():
    """``nt backtest --help`` advertises the new --config flag."""
    runner = CliRunner()
    result = runner.invoke(app, ["backtest", "--help"])
    assert result.exit_code == 0
    assert "--config" in result.stdout


def test_backtest_help_still_shows_legacy_strategy_option():
    """Legacy ``--strategy`` flag must remain visible until PR 4 — its
    help text is annotated ``(deprecated)`` so users know to migrate."""
    runner = CliRunner()
    result = runner.invoke(app, ["backtest", "--help"])
    assert result.exit_code == 0
    assert "--strategy" in result.stdout
    assert "deprecated" in result.stdout


def test_backtest_config_unknown_strategy_is_usage_error(tmp_path):
    """Unknown strategy name in YAML → BadParameter listing valid names."""
    yaml_path = tmp_path / "run.yaml"
    yaml_path.write_text(
        "strategy: nonexistent_bot\n"
        "instrument_id: BTCUSDT.BINANCE\n"
        "bar_type: BTCUSDT.BINANCE-1-HOUR-LAST-EXTERNAL\n"
        "venue: BINANCE\n"
        "account_type: CASH\n"
        'starting_balances: ["1000000 USDT"]\n'
        "data_source:\n"
        "  type: catalog\n"
        "  path: /tmp/x\n"
    )
    runner = CliRunner()
    result = runner.invoke(app, ["backtest", "--config", str(yaml_path)])
    assert result.exit_code != 0
    assert "nonexistent_bot" in result.output


# NOTE: The PR 2-era guard `test_backtest_config_kronos_routed_to_legacy_friendly_error`
# was deleted in sub-project B.5 PR 3. Its purpose — rejecting hand-rolled
# `strategy: kronos` YAMLs with a friendly pointer at the legacy
# `--strategy` invocation — is obsolete: kronos.yaml now ships in
# `configs/backtest/` and the new path handles kronos like every other
# strategy. The successor test is
# `test_backtest_config_dispatches_kronos_yaml` below.


def test_backtest_config_unknown_yaml_field_is_usage_error(tmp_path):
    """Unknown top-level field → ValidationError → BadParameter."""
    yaml_path = tmp_path / "bad.yaml"
    yaml_path.write_text(
        "strategy: ema_cross\n"
        "instrument_id: BTCUSDT.BINANCE\n"
        "bar_type: BTCUSDT.BINANCE-1-HOUR-LAST-EXTERNAL\n"
        "venue: BINANCE\n"
        "account_type: CASH\n"
        'starting_balances: ["1000000 USDT"]\n'
        "data_source:\n"
        "  type: catalog\n"
        "  path: /tmp/x\n"
        "bogus_field: 42\n"
    )
    runner = CliRunner()
    result = runner.invoke(app, ["backtest", "--config", str(yaml_path)])
    assert result.exit_code != 0
    assert "bogus_field" in result.output


def test_backtest_config_dispatches_to_runner(tmp_path, monkeypatch):
    """``nt backtest --config <yaml>`` instantiates ``BacktestStrategyRunner``
    with the right spec and calls ``runner.main()`` exactly once.

    Captures the runner via ``__init__`` and stubs ``main()`` so we don't
    actually boot a ``BacktestEngine`` in this test (the real-engine
    smoke lives in ``tests/backtest/test_strategy_runner.py``).
    """
    from nautilus_trading.backtest.strategy_runner import BacktestStrategyRunner

    runners: list[BacktestStrategyRunner] = []
    main_calls: list[BacktestStrategyRunner] = []
    original_init = BacktestStrategyRunner.__init__

    def _capturing_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        runners.append(self)

    monkeypatch.setattr(BacktestStrategyRunner, "__init__", _capturing_init)
    monkeypatch.setattr(
        BacktestStrategyRunner,
        "main",
        lambda self: main_calls.append(self),
    )

    # Use the committed ema_cross.yaml — exercises the YAML decoder + the
    # CatalogDataSource dispatch path in one test.
    yaml_path = CONFIGS_DIR / "ema_cross.yaml"
    assert yaml_path.exists()

    runner_cli = CliRunner()
    result = runner_cli.invoke(app, ["backtest", "--config", str(yaml_path)])
    assert result.exit_code == 0, result.stdout
    assert len(runners) == 1
    assert len(main_calls) == 1
    assert runners[0].spec.name == "ema_cross"


def test_backtest_config_dispatches_kronos_yaml(tmp_path, monkeypatch):
    """``nt backtest --config configs/backtest/kronos.yaml`` must
    instantiate ``BacktestStrategyRunner`` with the kronos spec and
    call ``runner.main()`` exactly once.

    Mirrors :func:`test_backtest_config_dispatches_to_runner` but for
    kronos. Sub-project B.5 PR 3 ported kronos onto the generic runner
    via :data:`STRATEGY_SPECS["kronos"]` + :class:`BinanceRestDataSource`
    and retired the legacy ``KronosBacktestRunner``; this is now the
    canonical kronos backtest invocation.

    HTTP is not exercised — the test stubs ``BacktestStrategyRunner.main``
    so no network or engine wiring runs.
    """
    del tmp_path  # use the committed YAML — exercises the file decoder too
    from nautilus_trading.backtest.strategy_runner import BacktestStrategyRunner

    runners: list[BacktestStrategyRunner] = []
    main_calls: list[BacktestStrategyRunner] = []
    original_init = BacktestStrategyRunner.__init__

    def _capturing_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        runners.append(self)

    monkeypatch.setattr(BacktestStrategyRunner, "__init__", _capturing_init)
    monkeypatch.setattr(
        BacktestStrategyRunner,
        "main",
        lambda self: main_calls.append(self),
    )

    yaml_path = CONFIGS_DIR / "kronos.yaml"
    assert yaml_path.exists(), "configs/backtest/kronos.yaml must be committed alongside this test"

    runner_cli = CliRunner()
    result = runner_cli.invoke(app, ["backtest", "--config", str(yaml_path)])
    assert result.exit_code == 0, result.stdout
    assert len(runners) == 1
    assert len(main_calls) == 1
    assert runners[0].spec.name == "kronos"
    # Kronos is the only strategy with a sibling actor (KronosActor).
    assert runners[0].spec.actor_specs, "kronos spec must wire KronosActor"


def test_backtest_config_internal_typeerror_propagates(tmp_path, monkeypatch):
    """An adapter constructor raising ``TypeError`` (an internal bug —
    NOT a signature mismatch) must propagate as ``TypeError`` through
    the CLI, not be re-labelled as ``typer.BadParameter``.

    The whole point of ``_construct``'s two-phase validation in
    ``data_sources/__init__.py`` is to keep internal-bug ``TypeError`` s
    distinct from user-facing ``ValueError`` s. PR #42's CLI catch
    ``except (TypeError, ValueError)`` around ``build_data_source``
    silently undid that — re-mapping internal bugs to ``BadParameter``
    degrades the diagnostic quality of crash reports.

    Mirror of the ``_BoomAdapter`` regression guard in
    ``tests/backtest/test_data_sources.py::test_build_data_source_propagates_internal_typeerror``.
    """
    from nautilus_trading.backtest.data_sources import binance_rest as br_mod

    class _BoomAdapter:
        # Signature matches BinanceRestDataSource exactly so kwarg
        # validation passes; the body simulates a real bug.
        def __init__(self, *, symbol, interval):  # noqa: ARG002
            raise TypeError("simulated internal bug in adapter __init__")

        def load(self, *, instrument_id, bar_type, start=None, end=None):  # noqa: ARG002
            return None

    monkeypatch.setattr(br_mod, "BinanceRestDataSource", _BoomAdapter)

    yaml_path = tmp_path / "run.yaml"
    yaml_path.write_text(
        "strategy: ema_cross\n"
        "instrument_id: BTCUSDT.BINANCE\n"
        "bar_type: BTCUSDT.BINANCE-1-HOUR-LAST-EXTERNAL\n"
        "venue: BINANCE\n"
        "account_type: CASH\n"
        'starting_balances: ["1000000 USDT"]\n'
        "data_source:\n"
        "  type: binance_rest\n"
        "  symbol: BTCUSDT\n"
        "  interval: 1h\n"
        "date_range:\n"
        '  start: "2024-01-01"\n'
        '  end: "2024-01-02"\n'
    )
    runner = CliRunner()
    result = runner.invoke(app, ["backtest", "--config", str(yaml_path)])
    assert result.exit_code != 0
    # The CLI wrapper must let TypeError surface — NOT swallow it as
    # BadParameter / ValueError.
    assert isinstance(result.exception, TypeError), (
        f"Expected TypeError to propagate, got: "
        f"{type(result.exception).__name__}: {result.exception}"
    )
    assert "simulated internal bug" in str(result.exception)


def test_backtest_legacy_strategy_path_emits_deprecation_warning(tmp_path, monkeypatch):
    """Calling ``nt backtest`` without ``--config`` must emit a
    ``DeprecationWarning``. The warning is raised before any heavy
    legacy work, so we stub the legacy entry function to a no-op and
    just observe the warning."""
    # Reach the *module*, not the re-exported ``backtest`` function in
    # ``cli/__init__.py``. The package's ``from .backtest import backtest``
    # leaves the function at ``nautilus_trading.cli.backtest`` while the
    # submodule lives in ``sys.modules`` under the same dotted name —
    # pull from there so monkeypatch hits attributes on the module.
    import sys

    backtest_module = sys.modules["nautilus_trading.cli.backtest"]

    monkeypatch.setattr(backtest_module, "_run_legacy_backtest", lambda **_: None)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        runner_cli = CliRunner()
        # Pass --strategy explicitly to avoid relying on the default —
        # the test still works if someone tightens the default later.
        result = runner_cli.invoke(
            app,
            ["backtest", "--strategy", "strategies.forex.ema_cross:EMACrossStrategy"],
        )
        assert result.exit_code == 0, result.stdout

    deprecations = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert deprecations, f"expected a DeprecationWarning; got: {[w.category for w in caught]}"
    msg = str(deprecations[0].message)
    assert "deprecated" in msg.lower()
    assert "--config" in msg
