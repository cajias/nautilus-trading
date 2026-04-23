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


def test_load_run_config_rejects_unknown_top_level_field(tmp_path: Path):
    """Unknown top-level key → msgspec.ValidationError."""
    path = tmp_path / "run.yaml"
    path.write_text(
        "strategy: ema_cross\n"
        "instrument_id: X\n"
        "bar_type: Y\n"
        "trade_size: \"0.001\"\n"
        "bogus_field: 1\n"
    )
    with pytest.raises(msgspec.ValidationError) as excinfo:
        load_run_config(path)
    assert "bogus_field" in str(excinfo.value)


def test_load_run_config_rejects_missing_required_field(tmp_path: Path):
    """Missing required top-level field → msgspec.ValidationError."""
    path = tmp_path / "run.yaml"
    path.write_text(
        "strategy: ema_cross\n"
        "instrument_id: X\n"
        # bar_type missing
        "trade_size: \"0.001\"\n"
    )
    with pytest.raises(msgspec.ValidationError) as excinfo:
        load_run_config(path)
    assert "bar_type" in str(excinfo.value)


def test_load_run_config_accepts_null_trade_size(tmp_path: Path):
    """trade_size is optional — null decodes to None (hybrid_sma_r10 case)."""
    path = tmp_path / "run.yaml"
    path.write_text(
        "strategy: hybrid_sma_r10\n"
        "instrument_id: BTCUSDT.BINANCE\n"
        "bar_type: BTCUSDT.BINANCE-1-MINUTE-LAST-EXTERNAL\n"
        "trade_size: null\n"
        "params:\n"
        "  sma_fast: 10\n"
        "  sma_slow: 30\n"
    )
    cfg = load_run_config(path)
    assert cfg.trade_size is None
