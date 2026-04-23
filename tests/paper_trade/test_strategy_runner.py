"""Tests for PaperTradeStrategyRunner — the generic paper-trade runner that
supersedes the 8 ``*_paper.py`` shims + ``kronos/paper_runner.py``.

The runner is a concrete ``PaperTradeRunner`` subclass parameterized by a
``StrategySpec`` + a params dict; it emits a ``TradingNodeConfig`` with any
declared actors attached before the strategy (preserving the Kronos
"actor publishes → strategy consumes" signal-flow contract).

Tests assert on the static ``TradingNodeConfig`` shape only — they never boot
a ``TradingNode``. The opt-in ``binance_testnet`` smoke suite in
``tests/paper_trade/test_smoke_paper.py`` covers live wiring; these unit
tests cover the pure-compositional layer.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

# -- Fixture helpers -------------------------------------------------------


def _grid_bot_params() -> dict:
    return {
        "instrument_id": "BTCUSDT.BINANCE",
        "bar_type": "BTCUSDT.BINANCE-1-HOUR-LAST-EXTERNAL",
        "trade_size": "0.001",
        "upper_price": "50000",
        "lower_price": "40000",
        "grid_levels": 8,
    }


def _kronos_params() -> dict:
    return {
        "instrument_id": "BTCUSDT.BINANCE",
        "bar_type": "BTCUSDT.BINANCE-1-MINUTE-LAST-INTERNAL",
        "trade_size": "0.001",
    }


# -- Importability + ABC subclassing --------------------------------------


def test_paper_trade_strategy_runner_subclasses_paper_trade_runner():
    from nautilus_trading.paper_trade.strategy_runner import PaperTradeStrategyRunner

    from nautilus_trading.paper_trade.runner_base import PaperTradeRunner

    assert issubclass(PaperTradeStrategyRunner, PaperTradeRunner)


# -- build_config: non-actor strategy (grid_bot) --------------------------


def test_grid_bot_build_config_returns_trading_node_config():
    from nautilus_trader.config import TradingNodeConfig
    from nautilus_trading.cli._strategy_specs import STRATEGY_SPECS
    from nautilus_trading.paper_trade.strategy_runner import PaperTradeStrategyRunner

    runner = PaperTradeStrategyRunner(
        spec=STRATEGY_SPECS["grid_bot"],
        params=_grid_bot_params(),
    )
    assert isinstance(runner.build_config(), TradingNodeConfig)


def test_grid_bot_build_config_has_single_strategy_no_actors():
    from nautilus_trading.cli._strategy_specs import STRATEGY_SPECS
    from nautilus_trading.paper_trade.strategy_runner import PaperTradeStrategyRunner

    runner = PaperTradeStrategyRunner(
        spec=STRATEGY_SPECS["grid_bot"],
        params=_grid_bot_params(),
    )
    config = runner.build_config()

    assert len(config.strategies) == 1
    # Non-actor strategies must not inject stray actors — preserves the
    # strategy-only shape every pre-kronos runner has shipped since PR 3.
    assert list(config.actors) == []
    assert config.strategies[0].strategy_path == "strategies.crypto.grid_bot:GridBotStrategy"
    assert config.strategies[0].config_path == "strategies.crypto.grid_bot:GridBotConfig"


def test_grid_bot_strategy_config_matches_spec_builder_output():
    from nautilus_trading.cli._strategy_specs import STRATEGY_SPECS
    from nautilus_trading.paper_trade.strategy_runner import PaperTradeStrategyRunner

    params = _grid_bot_params()
    runner = PaperTradeStrategyRunner(spec=STRATEGY_SPECS["grid_bot"], params=params)
    expected = STRATEGY_SPECS["grid_bot"].builder.build(params)

    assert runner.build_config().strategies[0].config == expected


# -- build_config: actor-bearing strategy (kronos) ------------------------


def test_kronos_build_config_has_exactly_one_actor():
    from nautilus_trading.cli._strategy_specs import STRATEGY_SPECS
    from nautilus_trading.paper_trade.strategy_runner import PaperTradeStrategyRunner

    runner = PaperTradeStrategyRunner(
        spec=STRATEGY_SPECS["kronos"],
        params=_kronos_params(),
    )
    config = runner.build_config()

    assert len(config.actors) == 1
    assert config.actors[0].actor_path == "strategies.crypto.kronos.actor:KronosActor"
    assert config.actors[0].config_path == "strategies.crypto.kronos.actor:KronosActorConfig"


def test_kronos_strategy_path_preserved_in_config():
    from nautilus_trading.cli._strategy_specs import STRATEGY_SPECS
    from nautilus_trading.paper_trade.strategy_runner import PaperTradeStrategyRunner

    runner = PaperTradeStrategyRunner(
        spec=STRATEGY_SPECS["kronos"],
        params=_kronos_params(),
    )
    strat = runner.build_config().strategies[0]

    assert strat.strategy_path == "strategies.crypto.kronos.strategy:KronosStrategy"
    assert strat.config_path == "strategies.crypto.kronos.strategy:KronosStrategyConfig"


def test_kronos_actor_config_applies_paper_runner_defaults():
    from nautilus_trading.cli._strategy_specs import STRATEGY_SPECS
    from nautilus_trading.paper_trade.strategy_runner import PaperTradeStrategyRunner

    runner = PaperTradeStrategyRunner(
        spec=STRATEGY_SPECS["kronos"],
        params=_kronos_params(),
    )
    actor_config = runner.build_config().actors[0].config

    assert actor_config == {
        "instrument_id": "BTCUSDT.BINANCE",
        "bar_type": "BTCUSDT.BINANCE-1-MINUTE-LAST-INTERNAL",
        "model_size": "mini",
        "n_samples": 10,
        "forecast_horizon": 24,
        "inference_interval_bars": 4,
    }


def test_kronos_actor_config_honors_overrides_from_params():
    from nautilus_trading.cli._strategy_specs import STRATEGY_SPECS
    from nautilus_trading.paper_trade.strategy_runner import PaperTradeStrategyRunner

    params = _kronos_params() | {
        "model_size": "small",
        "n_samples": 20,
        "forecast_horizon": 48,
        "inference_interval_bars": 2,
    }
    runner = PaperTradeStrategyRunner(spec=STRATEGY_SPECS["kronos"], params=params)
    actor_config = runner.build_config().actors[0].config

    assert actor_config["model_size"] == "small"
    assert actor_config["n_samples"] == 20
    assert actor_config["forecast_horizon"] == 48
    assert actor_config["inference_interval_bars"] == 2


def test_kronos_strategy_config_contains_only_base_fields():
    """``KronosStrategyConfig`` accepts only instrument_id / bar_type / trade_size.
    ML hyperparameters live on the sibling ``KronosActorConfig`` — the runner
    must keep these two dicts strictly separate."""
    from nautilus_trading.cli._strategy_specs import STRATEGY_SPECS
    from nautilus_trading.paper_trade.strategy_runner import PaperTradeStrategyRunner

    runner = PaperTradeStrategyRunner(spec=STRATEGY_SPECS["kronos"], params=_kronos_params())
    strat_config = runner.build_config().strategies[0].config

    assert strat_config == {
        "instrument_id": "BTCUSDT.BINANCE",
        "bar_type": "BTCUSDT.BINANCE-1-MINUTE-LAST-INTERNAL",
        "trade_size": "0.001",
    }


# -- Signal-flow ordering contract ----------------------------------------


def test_actor_builder_called_before_strategy_builder():
    """Actors must be prepared before the strategy — this mirrors the order
    NautilusTrader starts them at node boot and preserves the Kronos
    signal-flow contract documented in
    ``strategies/crypto/kronos/strategy.py`` ("actor publishes signals first,
    strategy consumes them").
    """
    from nautilus_trading.cli._strategy_specs import STRATEGY_SPECS
    from nautilus_trading.paper_trade.strategy_runner import PaperTradeStrategyRunner

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
        PaperTradeStrategyRunner(spec=spec, params=_kronos_params()).build_config()

    assert call_order == ["actor", "strategy"], (
        f"Expected actors built before strategy; got {call_order}"
    )


def test_actor_appears_in_config_actors_list_not_in_strategies():
    """Actors must land in ``config.actors`` (NautilusTrader starts them
    there during node boot), NOT be smuggled into ``config.strategies``."""
    from nautilus_trading.cli._strategy_specs import STRATEGY_SPECS
    from nautilus_trading.paper_trade.strategy_runner import PaperTradeStrategyRunner

    runner = PaperTradeStrategyRunner(spec=STRATEGY_SPECS["kronos"], params=_kronos_params())
    config = runner.build_config()

    strategy_paths = {s.strategy_path for s in config.strategies}
    actor_paths = {a.actor_path for a in config.actors}

    assert "strategies.crypto.kronos.actor:KronosActor" in actor_paths
    assert "strategies.crypto.kronos.actor:KronosActor" not in strategy_paths


# -- log_level pass-through -----------------------------------------------


def test_log_level_propagates_to_node_config():
    from nautilus_trading.cli._strategy_specs import STRATEGY_SPECS
    from nautilus_trading.paper_trade.strategy_runner import PaperTradeStrategyRunner

    runner = PaperTradeStrategyRunner(
        spec=STRATEGY_SPECS["grid_bot"],
        params=_grid_bot_params(),
        log_level="DEBUG",
    )
    assert runner.build_config().logging.log_level == "DEBUG"


def test_log_level_defaults_to_info():
    from nautilus_trading.cli._strategy_specs import STRATEGY_SPECS
    from nautilus_trading.paper_trade.strategy_runner import PaperTradeStrategyRunner

    runner = PaperTradeStrategyRunner(
        spec=STRATEGY_SPECS["grid_bot"],
        params=_grid_bot_params(),
    )
    assert runner.build_config().logging.log_level == "INFO"


# -- Behavioral parity with the shims that get deleted in Task C ----------
#
# These tests are the SAFETY GATE for Task C's shim deletion. They assert
# three layers of parity between each shim's ``build_config()`` output and
# the generic ``PaperTradeStrategyRunner``'s output:
#
#   1. Strategy shape — import paths and the strategy_config dict.
#   2. Actor shape (kronos only) — same for the attached actor.
#   3. Environment shape — trader_id, log level, and the registered venue
#      adapters on both sides. This catches default-drift in
#      ``build_paper_trade_node_config`` that wouldn't surface from the
#      per-strategy comparisons alone — e.g., accidentally flipping
#      Binance testnet → prod, or dropping an exec_client registration.
#
# If the shim's output could be swapped for the runner's without anyone
# noticing at TradingNode boot, these three layers should prove that.


def _assert_env_parity(shim_config, runner_config) -> None:
    """Environment-shape parity: fields the shim and generic runner must agree
    on irrespective of which strategy drives them."""
    assert shim_config.trader_id == runner_config.trader_id
    assert shim_config.logging.log_level == runner_config.logging.log_level
    # Same venue adapters registered on both sides — prevents default-drift
    # in build_paper_trade_node_config from silently diverging (e.g. a
    # client dropped or an extra one added).
    assert set(shim_config.data_clients.keys()) == set(runner_config.data_clients.keys())
    assert set(shim_config.exec_clients.keys()) == set(runner_config.exec_clients.keys())
    # Confirm the Binance client stays pinned to Testnet on both paths —
    # a flip to PRODUCTION here would mean real money touched the wire.
    for venue in shim_config.data_clients:
        assert (
            shim_config.data_clients[venue].environment
            == runner_config.data_clients[venue].environment
        )
        assert (
            shim_config.exec_clients[venue].environment
            == runner_config.exec_clients[venue].environment
        )


def test_grid_bot_shim_parity():
    """PaperTradeStrategyRunner must produce the same TradingNodeConfig shape as
    ``GridBotPaperTradeRunner``. Parity is the prerequisite for deleting the
    shim in Task C."""
    from nautilus_trading.cli._strategy_specs import STRATEGY_SPECS
    from nautilus_trading.paper_trade.strategy_runner import PaperTradeStrategyRunner
    from strategies.crypto.grid_bot_paper import GridBotPaperTradeRunner

    shim_config = GridBotPaperTradeRunner(
        instrument_id="BTCUSDT.BINANCE",
        bar_type="BTCUSDT.BINANCE-1-HOUR-LAST-EXTERNAL",
        trade_size="0.001",
        upper_price="50000",
        lower_price="40000",
        grid_levels=8,
    ).build_config()

    runner_config = PaperTradeStrategyRunner(
        spec=STRATEGY_SPECS["grid_bot"],
        params=_grid_bot_params(),
    ).build_config()

    # Strategy parity
    assert shim_config.strategies[0].strategy_path == runner_config.strategies[0].strategy_path
    assert shim_config.strategies[0].config_path == runner_config.strategies[0].config_path
    assert shim_config.strategies[0].config == runner_config.strategies[0].config
    assert list(shim_config.actors) == list(runner_config.actors) == []
    # Environment-shape parity
    _assert_env_parity(shim_config, runner_config)


def test_kronos_shim_parity():
    """Same three-layer parity guarantee for kronos, which has an actor. This
    is the test that gates deletion of ``strategies/crypto/kronos/paper_runner.py``
    in Task C."""
    from nautilus_trading.cli._strategy_specs import STRATEGY_SPECS
    from nautilus_trading.paper_trade.strategy_runner import PaperTradeStrategyRunner
    from strategies.crypto.kronos.paper_runner import KronosPaperTradeRunner

    shim_config = KronosPaperTradeRunner(
        instrument_id="BTCUSDT.BINANCE",
        bar_type="BTCUSDT.BINANCE-1-MINUTE-LAST-INTERNAL",
        trade_size="0.001",
    ).build_config()

    runner_config = PaperTradeStrategyRunner(
        spec=STRATEGY_SPECS["kronos"],
        params=_kronos_params(),
    ).build_config()

    # Strategy parity
    assert shim_config.strategies[0].strategy_path == runner_config.strategies[0].strategy_path
    assert shim_config.strategies[0].config_path == runner_config.strategies[0].config_path
    assert shim_config.strategies[0].config == runner_config.strategies[0].config
    # Actor parity
    assert len(shim_config.actors) == 1
    assert len(runner_config.actors) == 1
    assert shim_config.actors[0].actor_path == runner_config.actors[0].actor_path
    assert shim_config.actors[0].config_path == runner_config.actors[0].config_path
    assert shim_config.actors[0].config == runner_config.actors[0].config
    # Environment-shape parity
    _assert_env_parity(shim_config, runner_config)


# -- Registry-wide sanity: every spec builds --------------------------------


_PER_SPEC_EXTRA_PARAMS: dict[str, dict] = {
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
    "kronos": {},
}


@pytest.mark.parametrize("spec_name", list(_PER_SPEC_EXTRA_PARAMS.keys()))
def test_runner_builds_valid_config_for_every_registered_spec(spec_name):
    """Every spec in ``STRATEGY_SPECS`` must produce a valid TradingNodeConfig
    when the generic runner drives it — catches spec-shape drift across the
    9-entry registry."""
    from nautilus_trading.cli._strategy_specs import STRATEGY_SPECS
    from nautilus_trading.paper_trade.strategy_runner import PaperTradeStrategyRunner

    base = {
        "instrument_id": "BTCUSDT.BINANCE",
        "bar_type": "BTCUSDT.BINANCE-1-HOUR-LAST-EXTERNAL",
        "trade_size": "0.001",
    }
    params = base | _PER_SPEC_EXTRA_PARAMS[spec_name]
    spec = STRATEGY_SPECS[spec_name]

    config = PaperTradeStrategyRunner(spec=spec, params=params).build_config()

    assert len(config.strategies) == 1
    assert config.strategies[0].strategy_path == spec.strategy_path
    assert config.strategies[0].config_path == spec.config_path
    # Actors present iff the spec declares them.
    assert len(config.actors) == len(spec.actor_specs)
