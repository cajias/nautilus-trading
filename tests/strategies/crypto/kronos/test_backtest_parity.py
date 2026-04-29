"""Parity gate for the kronos backtest migration (sub-project B.5 PR 3).

Two tests with distinct lifetimes:

1. ``test_kronos_backtest_runner_parity_pre_deletion`` — the deletion
   gate. Asserts that the OLD
   :class:`~strategies.crypto.kronos.backtest.KronosBacktestRunner`
   and the NEW
   :class:`~nautilus_trading.backtest.strategy_runner.BacktestStrategyRunner`
   (driven by ``STRATEGY_SPECS["kronos"]`` and
   :class:`BinanceRestDataSource`) produce equivalent kronos backtest
   wiring on a pinned config. **Lives only as long as the OLD runner**;
   PR 3 Task #20 deletes both this test and the OLD runner together.

2. ``test_kronos_backtest_runner_matches_quarantined_snapshot`` — the
   durable anchor. Asserts that the NEW runner matches the frozen
   snapshot in ``_quarantined_backtest_snapshot.py``. **Survives PR 3**;
   forms the long-term regression guard against drift in the kronos
   backtest contract.

HTTP is mocked via ``monkeypatch`` — no real Binance calls in CI. Both
the OLD path's :func:`fetch_bars_from_binance` and the NEW
:class:`BinanceRestDataSource` import :mod:`requests` from independent
module references, so both must be patched in tests that exercise the
data-load path.
"""

from __future__ import annotations

from decimal import Decimal

import pytest


class _FakeResp:
    """Minimal ``requests.Response`` stand-in for the kronos fetch logic.

    Mirrors the helper in ``tests/backtest/test_data_sources.py`` —
    duplicated rather than imported so this test file stays
    self-contained when the OLD runner is deleted in Task #20.
    """

    def __init__(self, payload: list) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> list:
        return self._payload


def _install_binance_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch the two ``requests.get`` import sites + sleep.

    The OLD path imports ``requests`` in
    ``strategies.crypto.kronos._fetch_binance``; the NEW path imports
    it in ``nautilus_trading.backtest.data_sources.binance_rest``. Both
    must be patched independently — Python caches imports per module.

    A single one-row kline payload satisfies both fetchers' pagination
    loops: the second call returns ``[]`` and exits the loop.
    """
    fake_kline_payload = [
        # [open_time, open, high, low, close, volume, close_time, qv,
        #  trades, tbb, tbq, ignore]
        [
            1704067200000,  # 2024-01-01 00:00:00 UTC
            "42000.00",
            "42500.00",
            "41800.00",
            "42300.00",
            "100.5",
        ]
        + ["x"] * 6,
    ]

    call_count = {"old": 0, "new": 0}

    def _fake_get_old(url, params, timeout):  # noqa: ARG001
        call_count["old"] += 1
        return _FakeResp(fake_kline_payload if call_count["old"] == 1 else [])

    def _fake_get_new(url, params, timeout):  # noqa: ARG001
        call_count["new"] += 1
        return _FakeResp(fake_kline_payload if call_count["new"] == 1 else [])

    monkeypatch.setattr(
        "strategies.crypto.kronos._fetch_binance.requests.get",
        _fake_get_old,
    )
    monkeypatch.setattr(
        "nautilus_trading.backtest.data_sources.binance_rest.requests.get",
        _fake_get_new,
    )
    monkeypatch.setattr(
        "strategies.crypto.kronos._fetch_binance.time.sleep",
        lambda *_a, **_kw: None,
    )
    monkeypatch.setattr(
        "nautilus_trading.backtest.data_sources.binance_rest.time.sleep",
        lambda *_a, **_kw: None,
    )


# Pinned config — single source of truth for both parity tests so the
# OLD and NEW runners are exercised against identical inputs.
_INSTRUMENT_ID = "BTCUSDT.BINANCE"
_BAR_TYPE = "BTCUSDT.BINANCE-1-HOUR-LAST-EXTERNAL"
_TRADE_SIZE = "0.001"
_SYMBOL = "BTCUSDT"
_INTERVAL = "1h"
_START = "2024-01-01"
_END = "2024-01-31"
_INITIAL_CAPITAL = "500"
_MODEL_SIZE = "mini"
_FORECAST_HORIZON = 24
_N_SAMPLES = 10
_INFERENCE_INTERVAL = 4


def _build_new_runner():
    """Construct the NEW ``BacktestStrategyRunner`` with the pinned config."""
    from nautilus_trading.backtest.data_sources import build_data_source
    from nautilus_trading.backtest.run_config import BacktestRunConfig, DateRange
    from nautilus_trading.backtest.strategy_runner import BacktestStrategyRunner
    from nautilus_trading.cli._strategy_specs import STRATEGY_SPECS

    run_config = BacktestRunConfig(
        strategy="kronos",
        instrument_id=_INSTRUMENT_ID,
        bar_type=_BAR_TYPE,
        trade_size=_TRADE_SIZE,
        venue="BINANCE",
        account_type="CASH",
        starting_balances=[f"{_INITIAL_CAPITAL} USDT"],
        data_source={"type": "binance_rest", "symbol": _SYMBOL, "interval": _INTERVAL},
        date_range=DateRange(start=_START, end=_END),
        params={
            "model_size": _MODEL_SIZE,
            "n_samples": _N_SAMPLES,
            "forecast_horizon": _FORECAST_HORIZON,
            "inference_interval_bars": _INFERENCE_INTERVAL,
        },
    )
    data_source = build_data_source(run_config.data_source)
    return BacktestStrategyRunner(
        spec=STRATEGY_SPECS["kronos"],
        run_config=run_config,
        data_source=data_source,
    )


def _build_old_runner():
    """Construct the OLD ``KronosBacktestRunner`` with the pinned config."""
    from strategies.crypto.kronos.backtest import KronosBacktestRunner

    return KronosBacktestRunner(
        symbol=_SYMBOL,
        interval=_INTERVAL,
        start=_START,
        end=_END,
        initial_capital=Decimal(_INITIAL_CAPITAL),
        trade_size=Decimal(_TRADE_SIZE),
        model_size=_MODEL_SIZE,
        forecast_bars=_FORECAST_HORIZON,
        n_samples=_N_SAMPLES,
        inference_interval=_INFERENCE_INTERVAL,
    )


def _extract_old_snapshot() -> dict:
    """Translate the OLD runner's outputs into the snapshot shape.

    The OLD runner emits a dict from ``build_config()`` + imperative
    ``add_data()``; we read both call sites to assemble the same 4-key
    snapshot the NEW runner produces.
    """
    from strategies.crypto.kronos.backtest_config import build_bar_type

    old = _build_old_runner()
    cfg = old.build_config()
    instrument = cfg["instrument"]
    venue = cfg["venue"]
    bar_type = build_bar_type(instrument, interval=old.interval)

    # add_data() builds the strategy + actor configs; extract them
    # without actually wiring an engine.
    strategy_config = {
        "instrument_id": str(instrument.id),
        "bar_type": str(bar_type),
        "trade_size": str(old.trade_size),
    }
    actor_config = {
        "instrument_id": str(instrument.id),
        "bar_type": str(bar_type),
        "model_size": old.model_size,
        "n_samples": old.n_samples,
        "forecast_horizon": old.forecast_bars,
        "inference_interval_bars": old.inference_interval,
    }

    return {
        "strategy": {
            "strategy_path": "strategies.crypto.kronos.strategy:KronosStrategy",
            "config_path": "strategies.crypto.kronos.strategy:KronosStrategyConfig",
            "config": strategy_config,
        },
        "actor": {
            "actor_path": "strategies.crypto.kronos.actor:KronosActor",
            "config_path": "strategies.crypto.kronos.actor:KronosActorConfig",
            "config": actor_config,
        },
        "venue": {
            "name": venue.name,
            "account_type": venue.account_type.name,
            "oms_type": venue.oms_type.name,
            "base_currency": venue.base_currency,
            "starting_balances": list(venue.starting_balances),
        },
        "instrument": {
            "id": str(instrument.id),
            "raw_symbol": str(instrument.raw_symbol),
            "base_currency": instrument.base_currency.code,
            "quote_currency": instrument.quote_currency.code,
            "price_precision": instrument.price_precision,
            "size_precision": instrument.size_precision,
            "price_increment": str(instrument.price_increment),
            "size_increment": str(instrument.size_increment),
            "min_quantity": str(instrument.min_quantity),
            "maker_fee": str(instrument.maker_fee),
            "taker_fee": str(instrument.taker_fee),
        },
    }


def _extract_new_snapshot(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Translate the NEW runner's outputs into the snapshot shape.

    Calls ``build_config()`` (for strategy / actor wiring) and
    ``data_source.load(...)`` (for instrument shape) — the engine wiring
    in :meth:`BacktestStrategyRunner.main` doesn't actually need to run
    for parity, just the config builders.

    HTTP must be mocked because ``data_source.load`` walks the real
    fetch path; the caller is expected to install ``_install_binance_mock``
    before invoking this helper.
    """
    from nautilus_trader.model.objects import Money

    runner = _build_new_runner()
    eng_cfg = runner.build_config()
    actor = eng_cfg.actors[0]
    strategy = eng_cfg.strategies[0]

    # Run the data source to materialize the instrument (HTTP mocked).
    result = runner.data_source.load(
        instrument_id=runner.run_config.instrument_id,
        bar_type=runner.run_config.bar_type,
        start=runner.run_config.date_range.start,
        end=runner.run_config.date_range.end,
    )
    instrument = result.instrument

    # Mirror BacktestStrategyRunner.main()'s wiring so the venue snapshot
    # captures what would actually go into ``engine.add_venue``.
    new_balances = [str(Money.from_str(b)) for b in runner.run_config.starting_balances]

    return {
        "strategy": {
            "strategy_path": strategy.strategy_path,
            "config_path": strategy.config_path,
            "config": dict(strategy.config),
        },
        "actor": {
            "actor_path": actor.actor_path,
            "config_path": actor.config_path,
            "config": dict(actor.config),
        },
        "venue": {
            "name": runner.run_config.venue,
            "account_type": runner.run_config.account_type,
            "oms_type": "NETTING",  # hardcoded in BacktestStrategyRunner.main
            "base_currency": None,  # hardcoded — CASH SPOT
            "starting_balances": new_balances,
        },
        "instrument": {
            "id": str(instrument.id),
            "raw_symbol": str(instrument.raw_symbol),
            "base_currency": instrument.base_currency.code,
            "quote_currency": instrument.quote_currency.code,
            "price_precision": instrument.price_precision,
            "size_precision": instrument.size_precision,
            "price_increment": str(instrument.price_increment),
            "size_increment": str(instrument.size_increment),
            "min_quantity": str(instrument.min_quantity),
            "maker_fee": str(instrument.maker_fee),
            "taker_fee": str(instrument.taker_fee),
        },
    }


# ---------------------------------------------------------------------------
# Test 1 — pre-deletion parity (lives until Task #20 deletes the OLD runner)
# ---------------------------------------------------------------------------


def test_kronos_backtest_runner_parity_pre_deletion(monkeypatch: pytest.MonkeyPatch) -> None:
    """OLD ``KronosBacktestRunner`` and NEW ``BacktestStrategyRunner``
    must produce equivalent kronos backtest wiring on a pinned config.

    This is the **deletion gate** — Task #20 cannot delete the OLD
    runner until this test passes. After Task #20 deletes the OLD
    runner, this test gets deleted with it; the durable parity is
    enforced by ``test_kronos_backtest_runner_matches_quarantined_snapshot``.
    """
    _install_binance_mock(monkeypatch)

    old = _extract_old_snapshot()
    new = _extract_new_snapshot(monkeypatch)

    assert new == old, (
        "kronos backtest parity drift between OLD KronosBacktestRunner "
        "and NEW BacktestStrategyRunner — "
        "Task #20 cannot proceed until parity is restored.\n"
        f"  OLD: {old}\n"
        f"  NEW: {new}"
    )


# ---------------------------------------------------------------------------
# Test 2 — durable snapshot anchor (survives Task #20)
# ---------------------------------------------------------------------------


def test_kronos_backtest_runner_matches_quarantined_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """NEW ``BacktestStrategyRunner`` kronos output must match the
    frozen snapshot in ``_quarantined_backtest_snapshot.py``.

    Long-term regression guard against drift in the kronos backtest
    contract. A failure here means either the snapshot needs an
    explicit, justified bump, or a regression slipped into the
    runner / spec / data source.
    """
    from tests.strategies.crypto.kronos._quarantined_backtest_snapshot import (
        build_quarantined_backtest_snapshot,
    )

    _install_binance_mock(monkeypatch)

    new = _extract_new_snapshot(monkeypatch)
    expected = build_quarantined_backtest_snapshot()

    assert new == expected, (
        "NEW kronos backtest output drifted from the frozen snapshot. "
        "If this is intentional, edit "
        "tests/strategies/crypto/kronos/_quarantined_backtest_snapshot.py "
        "and justify the delta in the PR description.\n"
        f"  expected: {expected}\n"
        f"  actual:   {new}"
    )
