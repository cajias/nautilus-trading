"""Durable parity anchor for the kronos backtest contract.

Sub-project B.5 PR 3 retired the OLD ``KronosBacktestRunner`` after
the pre-deletion parity test confirmed equivalence with
:class:`~nautilus_trading.backtest.strategy_runner.BacktestStrategyRunner`
+ :data:`STRATEGY_SPECS["kronos"]` + :class:`BinanceRestDataSource` on
a pinned Binance window. The pre-deletion gate test was deleted along
with the OLD runner in PR 3 Task #20.

What's left is the durable snapshot-anchor test. It compares the
current generic-runner kronos output against the frozen snapshot in
``_quarantined_backtest_snapshot.py`` — a regression here means
either:

* the kronos contract has shifted intentionally and the snapshot
  needs an explicit, justified bump in the PR description; or
* an unintended drift slipped into ``BacktestStrategyRunner``,
  ``STRATEGY_SPECS["kronos"]``, ``KronosConfigBuilder``,
  ``KronosActorConfigBuilder``, or ``BinanceRestDataSource`` —
  fix the regression rather than the snapshot.

HTTP is mocked — no real Binance calls in CI.
"""

from __future__ import annotations

import pytest


class _FakeResp:
    """Minimal ``requests.Response`` stand-in for ``BinanceRestDataSource``.

    Mirrors the helper in ``tests/backtest/test_data_sources.py`` —
    duplicated rather than imported so this test file stays
    self-contained.
    """

    def __init__(self, payload: list) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> list:
        return self._payload


def _install_binance_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch ``requests.get`` + ``time.sleep`` on the
    :class:`BinanceRestDataSource` import site.

    A single one-row kline payload satisfies the fetcher's pagination
    loop: the second call returns ``[]`` and exits the loop.
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

    call_count = {"new": 0}

    def _fake_get(url, params, timeout):  # noqa: ARG001
        call_count["new"] += 1
        return _FakeResp(fake_kline_payload if call_count["new"] == 1 else [])

    monkeypatch.setattr(
        "nautilus_trading.backtest.data_sources.binance_rest.requests.get",
        _fake_get,
    )
    monkeypatch.setattr(
        "nautilus_trading.backtest.data_sources.binance_rest.time.sleep",
        lambda *_a, **_kw: None,
    )


# Pinned config — single source of truth for the snapshot extractor.
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
    """Construct the ``BacktestStrategyRunner`` with the pinned config."""
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


def _extract_new_snapshot() -> dict:
    """Translate the generic runner's outputs into the snapshot shape.

    Calls ``build_config()`` (for strategy / actor wiring) and
    ``data_source.load(...)`` (for instrument shape) — the engine
    wiring in :meth:`BacktestStrategyRunner.main` doesn't actually
    need to run for the parity check, just the config builders.

    HTTP must be mocked because ``data_source.load`` walks the real
    fetch path; the caller is expected to install
    ``_install_binance_mock`` before invoking this helper.
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
# Durable snapshot anchor — survived Task #20's deletion of the OLD runner.
# ---------------------------------------------------------------------------


def test_kronos_backtest_runner_matches_quarantined_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Generic ``BacktestStrategyRunner`` kronos output must match the
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

    new = _extract_new_snapshot()
    expected = build_quarantined_backtest_snapshot()

    assert new == expected, (
        "Generic-runner kronos backtest output drifted from the frozen "
        "snapshot. If this is intentional, edit "
        "tests/strategies/crypto/kronos/_quarantined_backtest_snapshot.py "
        "and justify the delta in the PR description.\n"
        f"  expected: {expected}\n"
        f"  actual:   {new}"
    )
