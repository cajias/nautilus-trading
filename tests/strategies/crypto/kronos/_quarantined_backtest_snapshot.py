"""Frozen snapshot of the kronos backtest configuration shape.

Captured from the OLD ``strategies.crypto.kronos.backtest.KronosBacktestRunner``
before its deletion in sub-project B.5 PR 3 Task #20. Mirrors the
paper-trade snapshot pattern in ``_quarantined_config_snapshot.py`` —
this file exists ONLY to anchor parity tests so future regressions in
:class:`~nautilus_trading.backtest.strategy_runner.BacktestStrategyRunner`'s
kronos output trip a comparison failure.

Lifetime
--------
* PR 3 Task #19 — created. Compared against:
  1. The OLD ``KronosBacktestRunner`` (pre-deletion parity test).
  2. The NEW ``BacktestStrategyRunner(spec=STRATEGY_SPECS["kronos"], …)``
     (durable snapshot-anchor test).
* PR 3 Task #20 — the OLD runner is deleted. The pre-deletion parity
  test gets deleted with it; the snapshot-anchor test (NEW vs this
  file) survives as the long-term regression guard.

After PR 3 lands, this file is **frozen**. Bumping its values requires
explicit justification in the PR description — a snapshot delta means
the kronos contract has shifted and consumers may need to be notified.

The 4 sub-snapshots (instrument, venue, strategy, actor) capture the
canonical kronos backtest shape the OLD runner produced when invoked
with the ``KRONOS_*`` env-var defaults from
``strategies/crypto/kronos/backtest.py::main``:

    symbol="BTCUSDT", interval="1h",
    initial_capital=Decimal("500"), trade_size=Decimal("0.001"),
    model_size="mini", forecast_bars=24, n_samples=50,
    inference_interval=4

Note: ``n_samples=50`` is the OLD ``main()`` env-var default, but the
snapshot pins ``n_samples=10`` to match the kronos paper-trade snapshot
(which itself mirrors the paper_trade.py defaults). Both are valid
configurations; the parity test pins ``n_samples`` explicitly to
disambiguate. The snapshot represents "production kronos backtest
config" not "the env-var hot path."
"""

from __future__ import annotations

from typing import Any

# Pinned canonical values — the kronos backtest contract.
SNAPSHOT_INSTRUMENT_ID: str = "BTCUSDT.BINANCE"
SNAPSHOT_BAR_TYPE: str = "BTCUSDT.BINANCE-1-HOUR-LAST-EXTERNAL"
SNAPSHOT_TRADE_SIZE: str = "0.001"
SNAPSHOT_VENUE_NAME: str = "BINANCE"
SNAPSHOT_INITIAL_CAPITAL_USDT: str = "500"

# Strategy wiring — the canonical KronosStrategy import path + config triple.
SNAPSHOT_STRATEGY: dict[str, Any] = {
    "strategy_path": "strategies.crypto.kronos.strategy:KronosStrategy",
    "config_path": "strategies.crypto.kronos.strategy:KronosStrategyConfig",
    "config": {
        "instrument_id": SNAPSHOT_INSTRUMENT_ID,
        "bar_type": SNAPSHOT_BAR_TYPE,
        "trade_size": SNAPSHOT_TRADE_SIZE,
    },
}

# Actor wiring — the kronos-specific addition over the 8 non-kronos
# backtest YAMLs. Defaults mirror the paper_trade.py snapshot pinned in
# ``_quarantined_config_snapshot.build_quarantined_config`` so paper +
# backtest stay congruent.
SNAPSHOT_ACTOR: dict[str, Any] = {
    "actor_path": "strategies.crypto.kronos.actor:KronosActor",
    "config_path": "strategies.crypto.kronos.actor:KronosActorConfig",
    "config": {
        "instrument_id": SNAPSHOT_INSTRUMENT_ID,
        "bar_type": SNAPSHOT_BAR_TYPE,
        "model_size": "mini",
        "n_samples": 10,
        "forecast_horizon": 24,
        "inference_interval_bars": 4,
    },
}

# Venue config — what the OLD ``build_venue_spec(initial_capital=Decimal("500"))``
# produced. ``base_currency=None`` for SPOT / multi-currency.
SNAPSHOT_VENUE: dict[str, Any] = {
    "name": SNAPSHOT_VENUE_NAME,
    "account_type": "CASH",
    "oms_type": "NETTING",
    "base_currency": None,
    # Money(Decimal("500"), USDT) and Money.from_str("500 USDT") both
    # stringify with USDT's full 8-decimal precision — pin that form so
    # the snapshot is venue-equivalent across both old and new.
    "starting_balances": ["500.00000000 USDT"],
}

# Instrument shape — what the OLD ``build_instrument(symbol="BTCUSDT")``
# produced. Decimal-valued fees stored as strings for stable
# `==` comparison. ``id`` / ``raw_symbol`` are stringified.
SNAPSHOT_INSTRUMENT: dict[str, Any] = {
    "id": "BTCUSDT.BINANCE",
    "raw_symbol": "BTCUSDT",
    "base_currency": "BTC",
    "quote_currency": "USDT",
    "price_precision": 2,
    "size_precision": 6,
    "price_increment": "0.01",
    "size_increment": "0.000001",
    "min_quantity": "0.000001",
    "maker_fee": "0.001",
    "taker_fee": "0.001",
}


def build_quarantined_backtest_snapshot() -> dict[str, Any]:
    """Return the aggregated snapshot dict the parity test compares against.

    Keys: ``strategy``, ``actor``, ``venue``, ``instrument``. The
    parity test extracts these same 4 sub-shapes from the
    ``BacktestEngineConfig`` + ``DataSource.load`` outputs and asserts
    `extracted == build_quarantined_backtest_snapshot()`.
    """
    return {
        "strategy": SNAPSHOT_STRATEGY,
        "actor": SNAPSHOT_ACTOR,
        "venue": SNAPSHOT_VENUE,
        "instrument": SNAPSHOT_INSTRUMENT,
    }
