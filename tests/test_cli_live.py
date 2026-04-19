"""Characterization tests for nautilus_trading.cli.live.

We capture the strat_config dict that cli.live assembles for each strategy
branch by monkeypatching `build_live_config` and `run_live`. This locks in
the current per-strategy logic before PR 5 moves it behind a registry.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest


@dataclass
class _CapturedLiveCall:
    strategy_config: dict[str, Any] = field(default_factory=dict)
    strategy_path: str = ""
    config_path: str = ""
    instrument_id: str = ""
    testnet: bool = True


@pytest.fixture
def capture_live(monkeypatch):
    captured = _CapturedLiveCall()

    def fake_build_live_config(**kwargs):
        captured.strategy_config = kwargs["strategy_config"]
        captured.strategy_path = kwargs["strategy_path"]
        captured.config_path = kwargs["config_path"]
        captured.instrument_id = kwargs["instrument_id"]
        captured.testnet = kwargs["testnet"]
        return object()  # sentinel

    def fake_run_live(config):  # noqa: ARG001
        return None

    monkeypatch.setattr("nautilus_trading.live.runner.build_live_config", fake_build_live_config)
    monkeypatch.setattr("nautilus_trading.live.runner.run_live", fake_run_live)
    return captured


def test_live_grid_bot_config(cli_runner, nt_app, capture_live):
    result = cli_runner.invoke(
        nt_app,
        [
            "live",
            "-s", "strategies.crypto.grid_bot",
            "-i", "BTCUSDT.BINANCE",
            "--bar-type", "BTCUSDT.BINANCE-1-HOUR-LAST-EXTERNAL",
            "--trade-size", "0.001",
            "--upper-price", "50000",
            "--lower-price", "40000",
            "--grid-levels", "8",
            "--testnet",
        ],
    )
    assert result.exit_code == 0, result.output
    cfg = capture_live.strategy_config
    assert cfg["trade_size"] == "0.001"
    assert cfg["upper_price"] == "50000"
    assert cfg["lower_price"] == "40000"
    assert cfg["grid_levels"] == 8
    assert capture_live.testnet is True


def test_live_grid_bot_requires_prices(cli_runner, nt_app, capture_live):
    result = cli_runner.invoke(
        nt_app,
        [
            "live",
            "-s", "strategies.crypto.grid_bot",
            "-i", "BTCUSDT.BINANCE",
            "--bar-type", "BTCUSDT.BINANCE-1-HOUR-LAST-EXTERNAL",
            "--trade-size", "0.001",
        ],
    )
    assert result.exit_code != 0
    assert "--upper-price" in result.output and "--lower-price" in result.output


def test_live_dca_bot_config(cli_runner, nt_app, capture_live):
    result = cli_runner.invoke(
        nt_app,
        [
            "live",
            "-s", "strategies.crypto.dca_bot",
            "-i", "BTCUSDT.BINANCE",
            "--bar-type", "BTCUSDT.BINANCE-1-HOUR-LAST-EXTERNAL",
            "--trade-size", "0.001",
            "--buy-amount", "5.0",
            "--buy-interval", "60",
        ],
    )
    assert result.exit_code == 0, result.output
    cfg = capture_live.strategy_config
    assert cfg["buy_amount"] == "5.0"
    assert cfg["buy_interval_bars"] == 60


def test_live_ema_cross_config(cli_runner, nt_app, capture_live):
    result = cli_runner.invoke(
        nt_app,
        [
            "live",
            "-s", "strategies.forex.ema_cross",
            "-i", "BTCUSDT.BINANCE",
            "--bar-type", "BTCUSDT.BINANCE-1-HOUR-LAST-EXTERNAL",
            "--trade-size", "0.001",
            "--fast-ema", "20",
            "--slow-ema", "50",
        ],
    )
    assert result.exit_code == 0, result.output
    cfg = capture_live.strategy_config
    assert cfg["fast_ema_period"] == 20
    assert cfg["slow_ema_period"] == 50
    assert cfg["ema_period"] == 50  # existing code sets this too; regression-lock


def test_live_timesfm_swing_fallback(cli_runner, nt_app, capture_live):
    result = cli_runner.invoke(
        nt_app,
        [
            "live",
            "-s", "strategies.crypto.timesfm_swing",
            "-i", "BTCUSDT.BINANCE",
            "--bar-type", "BTCUSDT.BINANCE-1-HOUR-LAST-EXTERNAL",
            "--trade-size", "0.01",
            "--fast-ema", "20",
            "--slow-ema", "100",
        ],
    )
    assert result.exit_code == 0, result.output
    cfg = capture_live.strategy_config
    assert cfg["fallback_fast_ema_period"] == 20
    assert cfg["ema_period"] == 100


def test_live_hybrid_sma_skips_trade_size_and_decimalizes(cli_runner, nt_app, capture_live):
    result = cli_runner.invoke(
        nt_app,
        [
            "live",
            "-s", "strategies.crypto.hybrid_sma_r10",
            "-i", "BTCUSDT.BINANCE",
            "--bar-type", "BTCUSDT.BINANCE-1-HOUR-LAST-EXTERNAL",
            "--trade-size", "0.01",
        ],
    )
    assert result.exit_code == 0, result.output
    cfg = capture_live.strategy_config
    assert "trade_size" not in cfg, "hybrid_sma_r10 must not receive trade_size"
    assert "sma_fast" in cfg and isinstance(cfg["sma_fast"], int)
    assert "stop_fast" in cfg and isinstance(cfg["stop_fast"], str)  # Decimal-as-string
