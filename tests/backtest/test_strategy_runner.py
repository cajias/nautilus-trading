"""Tests for ``BacktestStrategyRunner`` — the generic backtest runner that
parallels ``PaperTradeStrategyRunner`` from PR 1.

The runner is a freestanding ``@dataclass`` parameterized by a
``StrategySpec`` + a ``BacktestRunConfig`` + a ``DataSource``. It produces
a ``BacktestEngineConfig`` carrying any declared actors before the
strategy (preserving the Kronos "actor publishes → strategy consumes"
signal-flow contract) and exposes the standard ``BacktestRunner`` ABC
lifecycle (``build_config``, ``add_data``, ``run``, ``print_results``,
``main``).

PR-2 scope reminders that shape this suite:

- **Kronos engine boot is OFF-LIMITS**: kronos still rides the old
  ``KronosBacktestRunner`` until PR 3 ports it. We can still use the
  kronos spec for config-shape + actor-ordering tests because those
  paths only call ``spec.builder.build()`` / ``actor_spec.builder.build()``
  — they don't import ``KronosActor``. We just don't instantiate the
  ``BacktestEngine`` for kronos here.
- **End-to-end smoke uses the committed crypto fixture catalog**
  (``tests/fixtures/crypto/catalog/``, BTCUSDT 1H 2024-01-01..14). Both
  ``ema_cross`` and ``grid_bot`` get a real engine boot — that's the
  safety gate against silent miswiring in the runner.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CRYPTO_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "crypto" / "catalog"


# -- Fixture helpers -------------------------------------------------------


def _ema_run_config():
    """A valid ``BacktestRunConfig`` for an ema_cross run on the fixture catalog."""
    from nautilus_trading.backtest.run_config import BacktestRunConfig, DateRange

    return BacktestRunConfig(
        strategy="ema_cross",
        instrument_id="BTCUSDT.BINANCE",
        bar_type="BTCUSDT.BINANCE-1-HOUR-LAST-EXTERNAL",
        trade_size="0.001",
        venue="BINANCE",
        account_type="CASH",
        starting_balances=["1000000 USDT"],
        data_source={"type": "catalog", "path": str(CRYPTO_FIXTURE)},
        date_range=DateRange(start="2024-01-01", end="2024-01-14"),
        params={"fast_ema": 10, "slow_ema": 20},
    )


def _grid_run_config():
    from nautilus_trading.backtest.run_config import BacktestRunConfig, DateRange

    return BacktestRunConfig(
        strategy="grid_bot",
        instrument_id="BTCUSDT.BINANCE",
        bar_type="BTCUSDT.BINANCE-1-HOUR-LAST-EXTERNAL",
        trade_size="0.001",
        venue="BINANCE",
        account_type="CASH",
        starting_balances=["1000000 USDT"],
        data_source={"type": "catalog", "path": str(CRYPTO_FIXTURE)},
        date_range=DateRange(start="2024-01-01", end="2024-01-14"),
        params={"upper_price": "50000", "lower_price": "40000", "grid_levels": 8},
    )


def _kronos_run_config():
    """Kronos run-config for build-shape tests only (no engine boot)."""
    from nautilus_trading.backtest.run_config import BacktestRunConfig, DateRange

    return BacktestRunConfig(
        strategy="kronos",
        instrument_id="BTCUSDT.BINANCE",
        bar_type="BTCUSDT.BINANCE-1-HOUR-LAST-EXTERNAL",
        trade_size="0.001",
        venue="BINANCE",
        account_type="CASH",
        starting_balances=["500 USDT"],
        data_source={"type": "binance_rest", "symbol": "BTCUSDT", "interval": "1h"},
        date_range=DateRange(start="2024-01-01", end="2024-01-07"),
        params={},
    )


def _make_runner(run_config, data_source=None):
    from nautilus_trading.backtest.data_sources import build_data_source
    from nautilus_trading.backtest.strategy_runner import BacktestStrategyRunner

    from nautilus_trading.cli._strategy_specs import STRATEGY_SPECS

    return BacktestStrategyRunner(
        spec=STRATEGY_SPECS[run_config.strategy],
        run_config=run_config,
        data_source=data_source or build_data_source(run_config.data_source),
    )


# -- build_config: non-actor strategy (ema_cross) -------------------------


def test_ema_cross_build_config_returns_engine_config():
    from nautilus_trader.backtest.engine import BacktestEngineConfig

    runner = _make_runner(_ema_run_config())
    assert isinstance(runner.build_config(), BacktestEngineConfig)


def test_ema_cross_build_config_has_single_strategy_no_actors():
    runner = _make_runner(_ema_run_config())
    config = runner.build_config()

    assert len(config.strategies) == 1
    # Non-actor strategies must not inject stray actors.
    assert list(config.actors) == []
    assert config.strategies[0].strategy_path == "strategies.forex.ema_cross:EMACrossStrategy"
    assert config.strategies[0].config_path == "strategies.forex.ema_cross:EMACrossConfig"


def test_ema_cross_strategy_config_matches_spec_builder_output():
    from nautilus_trading.cli._strategy_specs import STRATEGY_SPECS

    run_config = _ema_run_config()
    runner = _make_runner(run_config)
    expected_params = {
        **run_config.params,
        "instrument_id": run_config.instrument_id,
        "bar_type": run_config.bar_type,
        "trade_size": run_config.trade_size,
    }
    expected = STRATEGY_SPECS["ema_cross"].builder.build(expected_params)

    assert runner.build_config().strategies[0].config == expected


# -- build_config: actor-bearing strategy (kronos, no engine boot) --------


def test_kronos_build_config_has_exactly_one_actor():
    """Build-shape only; kronos engine boot remains on the old runner."""
    runner = _make_runner(_kronos_run_config())
    config = runner.build_config()

    assert len(config.actors) == 1
    assert config.actors[0].actor_path == "strategies.crypto.kronos.actor:KronosActor"
    assert config.actors[0].config_path == "strategies.crypto.kronos.actor:KronosActorConfig"


def test_kronos_strategy_path_preserved_in_config():
    runner = _make_runner(_kronos_run_config())
    strat = runner.build_config().strategies[0]

    assert strat.strategy_path == "strategies.crypto.kronos.strategy:KronosStrategy"
    assert strat.config_path == "strategies.crypto.kronos.strategy:KronosStrategyConfig"


def test_kronos_strategy_config_contains_only_base_fields():
    """``KronosStrategyConfig`` accepts only instrument_id / bar_type /
    trade_size — ML hyperparameters live on the sibling
    ``KronosActorConfig``. Mirror of the paper-trade runner test."""
    runner = _make_runner(_kronos_run_config())
    strat_config = runner.build_config().strategies[0].config

    assert strat_config == {
        "instrument_id": "BTCUSDT.BINANCE",
        "bar_type": "BTCUSDT.BINANCE-1-HOUR-LAST-EXTERNAL",
        "trade_size": "0.001",
    }


# -- Signal-flow ordering contract ----------------------------------------


def test_actor_builder_called_before_strategy_builder():
    """Actors must be prepared before the strategy — preserves the Kronos
    signal-flow contract (actor publishes → strategy consumes). Mirrors
    the equivalent paper-trade test."""
    from nautilus_trading.cli._strategy_specs import STRATEGY_SPECS

    spec = STRATEGY_SPECS["kronos"]
    call_order: list[str] = []

    original_strategy_build = spec.builder.build
    original_actor_build = spec.actor_specs[0].builder.build

    def _track_strategy(args):
        call_order.append("strategy")
        return original_strategy_build(args)

    def _track_actor(args):
        call_order.append("actor")
        return original_actor_build(args)

    with (
        patch.object(spec.builder, "build", side_effect=_track_strategy),
        patch.object(spec.actor_specs[0].builder, "build", side_effect=_track_actor),
    ):
        _make_runner(_kronos_run_config()).build_config()

    assert call_order == ["actor", "strategy"], (
        f"Expected actors built before strategy; got {call_order}"
    )


def test_actor_appears_in_config_actors_list_not_in_strategies():
    """Actors must land in ``config.actors`` (BacktestEngine starts them
    there during boot), NOT be smuggled into ``config.strategies``."""
    runner = _make_runner(_kronos_run_config())
    config = runner.build_config()

    strategy_paths = {s.strategy_path for s in config.strategies}
    actor_paths = {a.actor_path for a in config.actors}

    assert "strategies.crypto.kronos.actor:KronosActor" in actor_paths
    assert "strategies.crypto.kronos.actor:KronosActor" not in strategy_paths


# -- log_level pass-through -----------------------------------------------


def test_log_level_propagates_to_engine_config():
    from nautilus_trading.backtest.run_config import BacktestRunConfig, DateRange

    rc = BacktestRunConfig(
        strategy="ema_cross",
        instrument_id="BTCUSDT.BINANCE",
        bar_type="BTCUSDT.BINANCE-1-HOUR-LAST-EXTERNAL",
        trade_size="0.001",
        venue="BINANCE",
        account_type="CASH",
        starting_balances=["1000000 USDT"],
        data_source={"type": "catalog", "path": str(CRYPTO_FIXTURE)},
        date_range=DateRange(start="2024-01-01", end="2024-01-14"),
        params={"fast_ema": 10, "slow_ema": 20},
        log_level="DEBUG",
    )
    runner = _make_runner(rc)
    assert runner.build_config().logging.log_level == "DEBUG"


# -- build_config invariant: called exactly once per main() ---------------


def test_main_builds_config_exactly_once(monkeypatch):
    """Apply Task #10's lesson: ``main()`` must reuse the result of a
    single ``build_config()`` invocation rather than rebuilding it.
    Same anti-pattern that bit paper-trade, blocked here pre-emptively."""
    from nautilus_trading.backtest.strategy_runner import BacktestStrategyRunner

    runner = _make_runner(_ema_run_config())

    call_count = [0]
    original_build = BacktestStrategyRunner.build_config

    def _counting(self):
        call_count[0] += 1
        return original_build(self)

    monkeypatch.setattr(BacktestStrategyRunner, "build_config", _counting)
    # Stub engine wiring so we don't actually run a backtest here.
    monkeypatch.setattr(
        BacktestStrategyRunner,
        "_build_engine",
        lambda self, config: _StubEngine(),
        raising=False,
    )
    monkeypatch.setattr(BacktestStrategyRunner, "add_data", lambda self, e, c: None)
    monkeypatch.setattr(BacktestStrategyRunner, "run", lambda self, e: e)
    monkeypatch.setattr(BacktestStrategyRunner, "print_results", lambda self, r: None)

    runner.main()
    assert call_count[0] == 1, f"build_config must be called once; got {call_count[0]}"


# -- Registry-wide sanity: every non-kronos spec builds -------------------


_PER_SPEC_PARAMS: dict[str, dict] = {
    "grid_bot": {"upper_price": "50000", "lower_price": "40000", "grid_levels": 8},
    "dca_bot": {"buy_interval_bars": 4, "buy_amount": "100"},
    "ema_cross": {"fast_ema": 10, "slow_ema": 20},
    "timesfm_swing": {"fast_ema": 10, "slow_ema": 20},
    "hybrid_sma_r10": {
        "sma_fast": 10,
        "sma_slow": 30,
        "stop_fast": "0.02",
        "stop_slow": "0.05",
    },
    "timesfm_grid": {},
    "rvs_swing": {},
    "shock_guard": {},
}

# `_PER_SPEC_TRADE_SIZE` overrides for strategies that don't carry the field.
_PER_SPEC_TRADE_SIZE: dict[str, str | None] = {
    "hybrid_sma_r10": None,  # sizes from equity
}


@pytest.mark.parametrize("spec_name", list(_PER_SPEC_PARAMS.keys()))
def test_runner_builds_valid_config_for_every_non_kronos_spec(spec_name):
    """Every non-kronos spec in ``STRATEGY_SPECS`` must produce a valid
    ``BacktestEngineConfig`` when the generic runner drives it. Kronos
    excluded — its engine boot lives on the old ``KronosBacktestRunner``
    until PR 3 ports it. Exclusion mirrors the YAML scope: 8 of 9."""
    from nautilus_trading.backtest.run_config import BacktestRunConfig, DateRange

    from nautilus_trading.cli._strategy_specs import STRATEGY_SPECS

    rc = BacktestRunConfig(
        strategy=spec_name,
        instrument_id="BTCUSDT.BINANCE",
        bar_type="BTCUSDT.BINANCE-1-HOUR-LAST-EXTERNAL",
        trade_size=_PER_SPEC_TRADE_SIZE.get(spec_name, "0.001"),
        venue="BINANCE",
        account_type="CASH",
        starting_balances=["1000000 USDT"],
        data_source={"type": "catalog", "path": str(CRYPTO_FIXTURE)},
        date_range=DateRange(start="2024-01-01", end="2024-01-14"),
        params=_PER_SPEC_PARAMS[spec_name],
    )
    spec = STRATEGY_SPECS[spec_name]

    runner = _make_runner(rc)
    config = runner.build_config()

    assert len(config.strategies) == 1
    assert config.strategies[0].strategy_path == spec.strategy_path
    assert config.strategies[0].config_path == spec.config_path
    # Actors present iff the spec declares them — non-kronos = empty.
    assert len(config.actors) == len(spec.actor_specs)


# -- End-to-end smoke (real BacktestEngine boot) --------------------------


# A small stub used by test_main_builds_config_exactly_once above.
class _StubEngine:
    def add_venue(self, **kwargs):
        return None

    def add_instrument(self, instrument):
        return None

    def add_data(self, data):
        return None

    def run(self):
        return None

    def dispose(self):
        return None


@pytest.mark.skipif(not CRYPTO_FIXTURE.exists(), reason="fixture catalog missing")
def test_ema_cross_end_to_end_smoke_runs_without_crashing():
    """Boot a real ``BacktestEngine`` via the runner on the fixture
    catalog (336 hourly BTCUSDT bars, 2024-01). Asserts only that
    ``runner.main()`` completes without raising — content correctness
    of the backtest is NOT in scope here; this is a wiring safety
    gate against silent miswiring of the strategy / engine / data-source
    composition. Mirror of the paper-trade smoke test, but it can run
    in unit-suite default because ``BacktestEngine`` doesn't need
    Testnet credentials."""
    runner = _make_runner(_ema_run_config())
    runner.main()  # should complete cleanly


@pytest.mark.skipif(not CRYPTO_FIXTURE.exists(), reason="fixture catalog missing")
def test_grid_bot_end_to_end_smoke_runs_without_crashing():
    """Grid-bot smoke — covers the per-strategy params path
    (``upper_price`` / ``lower_price`` / ``grid_levels``) reaching
    ``GridBotConfig`` correctly through the runner."""
    runner = _make_runner(_grid_run_config())
    runner.main()
