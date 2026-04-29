"""Tests for ``BacktestRunConfig`` (msgspec schema for ``configs/backtest/*.yaml``).

Mirrors ``tests/cli/test_paper_trade_configs.py``: every committed YAML
must round-trip through the strict msgspec decoder, and the ``params``
bucket must reach the strategy spec's builder so the runner can produce
a valid ``BacktestEngineConfig`` chain in Task C.

PR 3 added **kronos.yaml**, bringing the suite to 9 backtest YAMLs;
the legacy ``KronosBacktestRunner`` was retired at the same time.
"""

from __future__ import annotations

from pathlib import Path

import msgspec
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIGS_DIR = REPO_ROOT / "configs" / "backtest"

# 9 strategy YAMLs covering the full strategy registry. PR 3 added
# kronos.yaml after the parity-snapshot test confirmed the generic
# runner produces equivalent kronos output to the (now-deleted) legacy
# ``KronosBacktestRunner``.
BACKTEST_YAMLS = [
    "ema_cross.yaml",
    "grid_bot.yaml",
    "dca_bot.yaml",
    "timesfm_swing.yaml",
    "hybrid_sma_r10.yaml",
    "timesfm_grid.yaml",
    "rvs_swing.yaml",
    "shock_guard.yaml",
    "kronos.yaml",
]


# -- Schema basics ---------------------------------------------------------


def test_backtest_run_config_decodes_minimal_yaml(tmp_path):
    from nautilus_trading.backtest.run_config import BacktestRunConfig, load_run_config

    yaml_path = tmp_path / "minimal.yaml"
    yaml_path.write_text(
        "strategy: ema_cross\n"
        "instrument_id: BTCUSDT.BINANCE\n"
        "bar_type: BTCUSDT.BINANCE-1-HOUR-LAST-EXTERNAL\n"
        "venue: BINANCE\n"
        "account_type: CASH\n"
        'starting_balances: ["1000000 USDT"]\n'
        "data_source:\n"
        "  type: catalog\n"
        "  path: /tmp/some/catalog\n"
    )
    cfg = load_run_config(yaml_path)
    assert isinstance(cfg, BacktestRunConfig)
    assert cfg.strategy == "ema_cross"
    assert cfg.instrument_id == "BTCUSDT.BINANCE"
    assert cfg.venue == "BINANCE"
    assert cfg.account_type == "CASH"
    assert cfg.starting_balances == ["1000000 USDT"]
    assert cfg.data_source == {"type": "catalog", "path": "/tmp/some/catalog"}
    # Defaults
    assert cfg.trade_size is None
    assert cfg.log_level == "INFO"
    assert cfg.params == {}
    assert cfg.date_range is None


def test_backtest_run_config_decodes_full_yaml(tmp_path):
    from nautilus_trading.backtest.run_config import DateRange, load_run_config

    yaml_path = tmp_path / "full.yaml"
    yaml_path.write_text(
        "strategy: grid_bot\n"
        "instrument_id: BTCUSDT.BINANCE\n"
        "bar_type: BTCUSDT.BINANCE-1-HOUR-LAST-EXTERNAL\n"
        'trade_size: "0.001"\n'
        "venue: BINANCE\n"
        "account_type: CASH\n"
        'starting_balances: ["1000000 USDT"]\n'
        "log_level: DEBUG\n"
        "data_source:\n"
        "  type: binance_rest\n"
        "  symbol: BTCUSDT\n"
        "  interval: 1h\n"
        "date_range:\n"
        '  start: "2024-01-01"\n'
        '  end: "2024-01-31"\n'
        "params:\n"
        '  upper_price: "72000"\n'
        '  lower_price: "60000"\n'
        "  grid_levels: 8\n"
    )
    cfg = load_run_config(yaml_path)
    assert cfg.trade_size == "0.001"
    assert cfg.log_level == "DEBUG"
    assert isinstance(cfg.date_range, DateRange)
    assert cfg.date_range.start == "2024-01-01"
    assert cfg.date_range.end == "2024-01-31"
    assert cfg.params["grid_levels"] == 8


def test_backtest_run_config_rejects_unknown_top_level_field(tmp_path):
    """Strict schema (``forbid_unknown_fields=True``) — typos surface as
    ValidationError so the future CLI can map them to BadParameter."""
    from nautilus_trading.backtest.run_config import load_run_config

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
        "bogus_field: 1\n"
    )
    with pytest.raises(msgspec.ValidationError, match="bogus_field"):
        load_run_config(yaml_path)


def test_backtest_run_config_rejects_missing_required_field(tmp_path):
    """``venue`` is required — missing it raises ValidationError."""
    from nautilus_trading.backtest.run_config import load_run_config

    yaml_path = tmp_path / "missing.yaml"
    yaml_path.write_text(
        "strategy: ema_cross\n"
        "instrument_id: BTCUSDT.BINANCE\n"
        "bar_type: BTCUSDT.BINANCE-1-HOUR-LAST-EXTERNAL\n"
        "account_type: CASH\n"
        'starting_balances: ["1000000 USDT"]\n'
        "data_source:\n"
        "  type: catalog\n"
        "  path: /tmp/x\n"
    )
    with pytest.raises(msgspec.ValidationError):
        load_run_config(yaml_path)


def test_backtest_run_config_rejects_unknown_date_range_field(tmp_path):
    """``DateRange`` is a strict struct too — typos like ``begin`` instead
    of ``start`` should fail loudly."""
    from nautilus_trading.backtest.run_config import load_run_config

    yaml_path = tmp_path / "bad_date.yaml"
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
        "date_range:\n"
        '  begin: "2024-01-01"\n'
        '  end: "2024-01-31"\n'
    )
    with pytest.raises(msgspec.ValidationError):
        load_run_config(yaml_path)


# -- Committed YAML round-trip --------------------------------------------


@pytest.mark.parametrize("filename", BACKTEST_YAMLS)
def test_committed_backtest_yaml_decodes(filename):
    """Each committed config in ``configs/backtest/`` decodes to a valid
    ``BacktestRunConfig`` and references a strategy registered in
    ``STRATEGY_SPECS`` — guards both schema drift and registry drift."""
    from nautilus_trading.backtest.run_config import load_run_config
    from nautilus_trading.cli._strategy_specs import STRATEGY_SPECS

    path = CONFIGS_DIR / filename
    assert path.exists(), f"missing committed backtest config: {path}"

    cfg = load_run_config(path)
    assert cfg.strategy in STRATEGY_SPECS, (
        f"{filename}: strategy '{cfg.strategy}' is not registered in STRATEGY_SPECS"
    )
    # Sanity: data_source must declare a known type so the future
    # build_data_source() factory can dispatch.
    assert cfg.data_source.get("type") in ("catalog", "binance_rest", "test"), (
        f"{filename}: unexpected data_source.type {cfg.data_source.get('type')!r}"
    )


def test_all_backtest_yamls_committed():
    """The committed set in ``configs/backtest/`` must match the
    canonical ``BACKTEST_YAMLS`` list above. A drift (added, renamed,
    or deleted file) fails this test explicitly rather than silently
    changing the strategy coverage."""
    actual = sorted(p.name for p in CONFIGS_DIR.glob("*.yaml"))
    assert actual == sorted(BACKTEST_YAMLS), (
        f"configs/backtest YAML drift: expected {sorted(BACKTEST_YAMLS)}, got {actual}"
    )
