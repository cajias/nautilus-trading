"""Schema + loader tests for PaperRunConfig."""

from __future__ import annotations

from pathlib import Path

import msgspec
import pytest

from nautilus_trading.paper_trade.run_config import PaperRunConfig, load_run_config


def test_load_run_config_round_trips_minimal(tmp_path: Path):
    """Minimal valid YAML → PaperRunConfig with defaults filled."""
    yaml_text = """\
strategy: ema_cross
instrument_id: BTCUSDT.BINANCE
bar_type: BTCUSDT.BINANCE-1-MINUTE-LAST-EXTERNAL
trade_size: "0.001"
params:
  fast_ema: 12
  slow_ema: 26
"""
    path = tmp_path / "run.yaml"
    path.write_text(yaml_text)

    cfg = load_run_config(path)

    assert isinstance(cfg, PaperRunConfig)
    assert cfg.strategy == "ema_cross"
    assert cfg.instrument_id == "BTCUSDT.BINANCE"
    assert cfg.bar_type == "BTCUSDT.BINANCE-1-MINUTE-LAST-EXTERNAL"
    assert cfg.trade_size == "0.001"
    assert cfg.log_level == "INFO"  # default
    assert cfg.duration is None      # default
    assert cfg.params == {"fast_ema": 12, "slow_ema": 26}
