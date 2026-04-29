"""Tests for ``LiveStrategyRunner`` + ``LiveRunConfig`` — Binance PROD scaffold.

Real-money execution is out of scope per the 2026-04-21 no-real-money
directive. ``LiveStrategyRunner.main()`` raises ``NotImplementedError``;
``build_config()`` is allowed to construct a valid ``TradingNodeConfig``
for shape symmetry with the paper-trade and backtest runners.

All assertions are static — these tests must NEVER boot a ``TradingNode``.
"""

from __future__ import annotations

from pathlib import Path

import msgspec
import pytest

# ---------------------------------------------------------------------------
# Param fixtures
# ---------------------------------------------------------------------------


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


def _ema_cross_params() -> dict:
    return {
        "instrument_id": "BTCUSDT.BINANCE",
        "bar_type": "BTCUSDT.BINANCE-1-MINUTE-LAST-EXTERNAL",
        "trade_size": "0.001",
        "fast_ema": 10,
        "slow_ema": 20,
    }


# ---------------------------------------------------------------------------
# LiveStrategyRunner.main() — the contract
# ---------------------------------------------------------------------------


def test_live_strategy_runner_main_raises_not_implemented():
    """``main()`` must raise ``NotImplementedError`` with a message that
    explicitly references the 2026-04-21 directive — this is the contract."""
    from nautilus_trading.live.strategy_runner import LiveStrategyRunner

    from nautilus_trading.cli._strategy_specs import STRATEGY_SPECS

    runner = LiveStrategyRunner(
        spec=STRATEGY_SPECS["ema_cross"],
        params=_ema_cross_params(),
    )
    with pytest.raises(NotImplementedError) as excinfo:
        runner.main()
    assert "2026-04-21" in str(excinfo.value)


def test_live_strategy_runner_main_message_references_directive():
    """The error message must mention 'directive' so operators searching the
    codebase for 'real-money directive' land on the canonical scaffold."""
    from nautilus_trading.live.strategy_runner import LiveStrategyRunner

    from nautilus_trading.cli._strategy_specs import STRATEGY_SPECS

    runner = LiveStrategyRunner(
        spec=STRATEGY_SPECS["ema_cross"],
        params=_ema_cross_params(),
    )
    with pytest.raises(NotImplementedError) as excinfo:
        runner.main()
    assert "directive" in str(excinfo.value).lower()


# ---------------------------------------------------------------------------
# LiveStrategyRunner.build_config() — shape symmetry with paper-trade
# ---------------------------------------------------------------------------


def test_live_strategy_runner_build_config_returns_trading_node_config():
    """``build_config()`` must succeed (failure happens at boot, not at
    config validation) so future real-money work has a structural template."""
    from nautilus_trader.config import TradingNodeConfig
    from nautilus_trading.live.strategy_runner import LiveStrategyRunner

    from nautilus_trading.cli._strategy_specs import STRATEGY_SPECS

    runner = LiveStrategyRunner(
        spec=STRATEGY_SPECS["ema_cross"],
        params=_ema_cross_params(),
    )
    assert isinstance(runner.build_config(), TradingNodeConfig)


def test_live_strategy_runner_build_config_uses_binance_live_environment():
    """The PROD scaffold MUST flip the Binance environment to LIVE — this is
    the structural difference from the paper-trade runner. If it ever
    silently fell back to TESTNET, an over-eager future implementer of
    ``main()`` would route real-money traffic to Testnet."""
    from nautilus_trader.adapters.binance.common.enums import BinanceEnvironment
    from nautilus_trading.live.strategy_runner import LiveStrategyRunner

    from nautilus_trading.cli._strategy_specs import STRATEGY_SPECS

    runner = LiveStrategyRunner(
        spec=STRATEGY_SPECS["ema_cross"],
        params=_ema_cross_params(),
    )
    config = runner.build_config()
    # Both data + exec clients must point at LIVE — asymmetry would create a
    # silent split-brain where market data comes from one env and orders go
    # to another.
    for client_config in (*config.data_clients.values(), *config.exec_clients.values()):
        assert client_config.environment == BinanceEnvironment.LIVE, (
            f"Expected LIVE environment; got {client_config.environment}. "
            "Live runner must not silently fall back to TESTNET."
        )


def test_live_strategy_runner_log_level_propagates():
    from nautilus_trading.live.strategy_runner import LiveStrategyRunner

    from nautilus_trading.cli._strategy_specs import STRATEGY_SPECS

    runner = LiveStrategyRunner(
        spec=STRATEGY_SPECS["ema_cross"],
        params=_ema_cross_params(),
        log_level="DEBUG",
    )
    assert runner.build_config().logging.log_level == "DEBUG"


def test_live_strategy_runner_log_level_defaults_to_info():
    from nautilus_trading.live.strategy_runner import LiveStrategyRunner

    from nautilus_trading.cli._strategy_specs import STRATEGY_SPECS

    runner = LiveStrategyRunner(
        spec=STRATEGY_SPECS["ema_cross"],
        params=_ema_cross_params(),
    )
    assert runner.build_config().logging.log_level == "INFO"


@pytest.mark.parametrize("spec_name", list(_PER_SPEC_EXTRA_PARAMS.keys()))
def test_live_strategy_runner_builds_valid_config_for_every_registered_spec(spec_name):
    """Every spec in ``STRATEGY_SPECS`` must produce a valid TradingNodeConfig
    when the live runner drives it — catches spec-shape drift across the
    9-entry registry. (We don't call ``main()`` here — that's covered above.)
    """
    from nautilus_trader.config import TradingNodeConfig
    from nautilus_trading.live.strategy_runner import LiveStrategyRunner

    from nautilus_trading.cli._strategy_specs import STRATEGY_SPECS

    base = {
        "instrument_id": "BTCUSDT.BINANCE",
        "bar_type": "BTCUSDT.BINANCE-1-HOUR-LAST-EXTERNAL",
        "trade_size": "0.001",
    }
    params = base | _PER_SPEC_EXTRA_PARAMS[spec_name]
    spec = STRATEGY_SPECS[spec_name]

    config = LiveStrategyRunner(spec=spec, params=params).build_config()

    assert isinstance(config, TradingNodeConfig)
    assert len(config.strategies) == 1
    assert config.strategies[0].strategy_path == spec.strategy_path
    assert len(config.actors) == len(spec.actor_specs)


# ---------------------------------------------------------------------------
# LiveRunConfig — friction against paste-errors
# ---------------------------------------------------------------------------


def _minimal_yaml_with_opt_in() -> str:
    """A minimal LiveRunConfig YAML with the friction flag set to true."""
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


def test_live_run_config_round_trips_minimal(tmp_path: Path):
    """Happy-path YAML with ``i_understand_real_money: true`` parses cleanly."""
    from nautilus_trading.live.run_config import LiveRunConfig, load_run_config

    path = tmp_path / "run.yaml"
    path.write_text(_minimal_yaml_with_opt_in())

    cfg = load_run_config(path)

    assert isinstance(cfg, LiveRunConfig)
    assert cfg.strategy == "ema_cross"
    assert cfg.instrument_id == "BTCUSDT.BINANCE"
    assert cfg.bar_type == "BTCUSDT.BINANCE-1-MINUTE-LAST-EXTERNAL"
    assert cfg.trade_size == "0.001"
    assert cfg.log_level == "INFO"  # default
    assert cfg.i_understand_real_money is True
    assert cfg.params == {"fast_ema": 12, "slow_ema": 26}


def test_live_run_config_rejects_missing_i_understand_real_money(tmp_path: Path):
    """Missing the friction field MUST fail schema validation — no default,
    no fallback. This is the deliberate paste-error guard."""
    from nautilus_trading.live.run_config import load_run_config

    path = tmp_path / "run.yaml"
    # Same YAML as the happy path BUT with i_understand_real_money omitted.
    path.write_text(
        "strategy: ema_cross\n"
        "instrument_id: BTCUSDT.BINANCE\n"
        "bar_type: BTCUSDT.BINANCE-1-MINUTE-LAST-EXTERNAL\n"
        'trade_size: "0.001"\n'
        "params:\n"
        "  fast_ema: 12\n"
        "  slow_ema: 26\n"
    )

    with pytest.raises(msgspec.ValidationError) as excinfo:
        load_run_config(path)
    assert "i_understand_real_money" in str(excinfo.value)


def test_live_run_config_rejects_i_understand_real_money_false(tmp_path: Path):
    """Explicit ``i_understand_real_money: false`` must be rejected — this is
    the second half of the friction guard. A user explicitly opting OUT
    cannot then route through the live path."""
    from nautilus_trading.live.run_config import load_run_config

    path = tmp_path / "run.yaml"
    path.write_text(
        "strategy: ema_cross\n"
        "instrument_id: BTCUSDT.BINANCE\n"
        "bar_type: BTCUSDT.BINANCE-1-MINUTE-LAST-EXTERNAL\n"
        'trade_size: "0.001"\n'
        "i_understand_real_money: false\n"
    )

    with pytest.raises(msgspec.ValidationError):
        load_run_config(path)


def test_live_run_config_rejects_unknown_top_level_field(tmp_path: Path):
    """Unknown top-level key → ``msgspec.ValidationError`` (forbid_unknown_fields)."""
    from nautilus_trading.live.run_config import load_run_config

    path = tmp_path / "run.yaml"
    path.write_text(
        "strategy: ema_cross\n"
        "instrument_id: BTCUSDT.BINANCE\n"
        "bar_type: BTCUSDT.BINANCE-1-MINUTE-LAST-EXTERNAL\n"
        'trade_size: "0.001"\n'
        "i_understand_real_money: true\n"
        "bogus_field: 1\n"
    )
    with pytest.raises(msgspec.ValidationError) as excinfo:
        load_run_config(path)
    assert "bogus_field" in str(excinfo.value)


def test_live_run_config_rejects_malformed_yaml(tmp_path: Path):
    """Syntactically broken YAML → ``msgspec.ValidationError`` (not raw
    DecodeError) — same funnel-through-one-except-clause as PaperRunConfig."""
    from nautilus_trading.live.run_config import load_run_config

    path = tmp_path / "run.yaml"
    path.write_text('strategy: "unterminated\n')
    with pytest.raises(msgspec.ValidationError):
        load_run_config(path)


def test_live_run_config_accepts_null_trade_size(tmp_path: Path):
    """``trade_size: null`` is allowed — hybrid_sma_r10 sizes from equity."""
    from nautilus_trading.live.run_config import load_run_config

    path = tmp_path / "run.yaml"
    path.write_text(
        "strategy: hybrid_sma_r10\n"
        "instrument_id: BTCUSDT.BINANCE\n"
        "bar_type: BTCUSDT.BINANCE-1-MINUTE-LAST-EXTERNAL\n"
        "trade_size: null\n"
        "i_understand_real_money: true\n"
        "params:\n"
        "  sma_fast: 10\n"
        "  sma_slow: 30\n"
    )
    cfg = load_run_config(path)
    assert cfg.trade_size is None
    assert cfg.i_understand_real_money is True
