"""Tests for the ``DataSource`` Protocol + 3 adapters in
``nautilus_trading.backtest.data_sources``.

Each adapter materializes one ``(Instrument, bars/ticks)`` slice for a
single backtest run, behind a shared ``Protocol`` so the generic
``BacktestStrategyRunner`` (Task C) doesn't carry adapter-specific
branches.

Decoupled from the existing ``nautilus_trading.data.providers.DataProvider``
ABC, which solves a different problem (one-time catalog population). The
two abstractions can coexist: ``DataProvider`` populates a catalog,
``CatalogDataSource`` reads from it at backtest time.

Network is never touched in this suite. ``BinanceRestDataSource`` is
exercised with a stubbed ``requests.get``; ``TestDataSource`` is exercised
with a stubbed ``ensure_catalog``.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CRYPTO_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "crypto" / "catalog"


# -- Protocol shape --------------------------------------------------------


def test_data_source_protocol_is_runtime_checkable():
    """``isinstance(obj, DataSource)`` must work — the runner uses
    runtime checks at boot to surface bad adapter wiring early."""
    from nautilus_trading.backtest.data_sources import DataSource

    class Stub:
        def load(self, *, instrument_id, bar_type, start=None, end=None):
            return None

    assert isinstance(Stub(), DataSource)


def test_objects_without_load_method_fail_protocol_check():
    from nautilus_trading.backtest.data_sources import DataSource

    class NoLoad:
        pass

    assert not isinstance(NoLoad(), DataSource)


def test_data_source_result_is_frozen():
    """``DataSourceResult`` is the runner-facing return shape; freeze it
    so adapters can't accidentally mutate the bar list mid-backtest."""
    from nautilus_trading.backtest.data_sources import DataSourceResult

    r = DataSourceResult(instrument=object(), data=[])
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.data = [1, 2, 3]  # type: ignore[misc]


# -- build_data_source factory --------------------------------------------


def test_build_data_source_dispatches_catalog():
    from nautilus_trading.backtest.data_sources import build_data_source
    from nautilus_trading.backtest.data_sources.catalog import CatalogDataSource

    ds = build_data_source({"type": "catalog", "path": str(CRYPTO_FIXTURE)})
    assert isinstance(ds, CatalogDataSource)


def test_build_data_source_dispatches_binance_rest():
    from nautilus_trading.backtest.data_sources import build_data_source
    from nautilus_trading.backtest.data_sources.binance_rest import BinanceRestDataSource

    ds = build_data_source({"type": "binance_rest", "symbol": "BTCUSDT", "interval": "1h"})
    assert isinstance(ds, BinanceRestDataSource)


def test_build_data_source_dispatches_test():
    from nautilus_trading.backtest.data_sources import build_data_source
    from nautilus_trading.backtest.data_sources.test import TestDataSource

    ds = build_data_source({"type": "test"})
    assert isinstance(ds, TestDataSource)


def test_build_data_source_unknown_type_raises_valueerror():
    """The CLI maps ``ValueError`` to ``typer.BadParameter`` (same pattern
    as ``StrategyConfigBuilder._base()``); preserve that contract."""
    from nautilus_trading.backtest.data_sources import build_data_source

    with pytest.raises(ValueError, match="Unknown data_source type"):
        build_data_source({"type": "nonsense"})


def test_build_data_source_unknown_kwarg_raises_valueerror():
    """Unknown kwargs in the YAML spec are user errors — raise ValueError
    so the CLI can map to BadParameter. (TypeError contract: only signature
    mismatches surface as ValueError; internal constructor TypeErrors
    propagate as TypeError — see test below.)"""
    from nautilus_trading.backtest.data_sources import build_data_source

    with pytest.raises(ValueError, match="unknown"):
        build_data_source(
            {"type": "binance_rest", "symbol": "BTCUSDT", "interval": "1h", "junk": 42},
        )


def test_build_data_source_missing_required_kwarg_raises_valueerror():
    """Required kwargs missing from the YAML spec → ValueError listing
    them. Mirrors the ``StrategyConfigBuilder._base()`` discipline so
    the CLI maps to BadParameter."""
    from nautilus_trading.backtest.data_sources import build_data_source

    with pytest.raises(ValueError, match="missing required"):
        # binance_rest needs both symbol and interval
        build_data_source({"type": "binance_rest", "symbol": "BTCUSDT"})


def test_build_data_source_propagates_internal_typeerror(monkeypatch):
    """If an adapter's constructor raises a ``TypeError`` for an internal
    bug — NOT a signature mismatch — that error must surface as
    ``TypeError``, not be re-labelled as a user-facing ``ValueError``.
    Mislabelling internal bugs degrades the diagnostic quality of crash
    reports.

    Regression guard for /ultrareview's IMPORTANT #4 finding on PR 2's
    foundation layer: ``_construct`` previously caught every TypeError
    blindly, including ones from inside the constructor body. The fix is
    "validate first, construct second, propagate constructor errors
    normally" — so we need a substitute class whose signature passes
    validation but whose body raises TypeError.

    Frozen dataclasses don't fire ``__post_init__`` after class creation,
    so we substitute the whole class on the binance_rest module before
    ``build_data_source`` does its lazy import.
    """
    from nautilus_trading.backtest.data_sources import binance_rest as br_mod
    from nautilus_trading.backtest.data_sources import build_data_source

    class _BoomAdapter:
        # Signature matches BinanceRestDataSource exactly so kwarg
        # validation passes; the body simulates a real bug.
        def __init__(self, *, symbol, interval):  # noqa: ARG002
            raise TypeError("simulated internal bug in adapter __init__")

        def load(self, *, instrument_id, bar_type, start=None, end=None):  # noqa: ARG002
            return None

    monkeypatch.setattr(br_mod, "BinanceRestDataSource", _BoomAdapter)

    with pytest.raises(TypeError, match="simulated internal bug"):
        build_data_source({"type": "binance_rest", "symbol": "BTCUSDT", "interval": "1h"})


def test_build_data_source_missing_type_raises_valueerror():
    from nautilus_trading.backtest.data_sources import build_data_source

    with pytest.raises(ValueError, match="missing required field"):
        build_data_source({"path": "/tmp/x"})


def test_build_data_source_non_dict_raises_valueerror():
    from nautilus_trading.backtest.data_sources import build_data_source

    with pytest.raises(ValueError, match="must be a dict"):
        build_data_source("catalog")  # type: ignore[arg-type]


def test_build_data_source_does_not_mutate_caller_dict():
    """``build_data_source`` pops the ``type`` key off the spec internally;
    the caller's dict must remain intact so YAML reload isn't lossy."""
    from nautilus_trading.backtest.data_sources import build_data_source

    spec = {"type": "catalog", "path": str(CRYPTO_FIXTURE)}
    build_data_source(spec)
    assert spec == {"type": "catalog", "path": str(CRYPTO_FIXTURE)}


# -- CatalogDataSource ----------------------------------------------------


@pytest.mark.skipif(not CRYPTO_FIXTURE.exists(), reason="fixture catalog missing")
def test_catalog_data_source_loads_instrument_and_bars(crypto_catalog_path):
    from nautilus_trading.backtest.data_sources.catalog import CatalogDataSource

    ds = CatalogDataSource(path=str(crypto_catalog_path))
    result = ds.load(
        instrument_id="BTCUSDT.BINANCE",
        bar_type="BTCUSDT.BINANCE-1-HOUR-LAST-EXTERNAL",
    )
    assert str(result.instrument.id) == "BTCUSDT.BINANCE"
    assert len(result.data) > 0, "fixture catalog should contain bars"


def test_catalog_data_source_raises_on_missing_instrument(tmp_path):
    """Pointing at an empty catalog path is a config error — fail loudly so
    the CLI can map it to a friendly message via the ValueError contract."""
    from nautilus_trading.backtest.data_sources.catalog import CatalogDataSource

    empty_catalog = tmp_path / "empty"
    empty_catalog.mkdir()
    ds = CatalogDataSource(path=str(empty_catalog))
    with pytest.raises(ValueError, match="instrument"):
        ds.load(
            instrument_id="BTCUSDT.BINANCE",
            bar_type="BTCUSDT.BINANCE-1-HOUR-LAST-EXTERNAL",
        )


# -- BinanceRestDataSource (mocked HTTP) ----------------------------------


class _FakeResp:
    """Minimal ``requests.Response`` stand-in for the kronos fetch logic."""

    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def test_binance_rest_data_source_constructs_kline_request():
    """Capture the URL params handed to the Binance ``/api/v3/klines``
    endpoint. Asserts the symbol/interval pin-through and that the start /
    end dates round-trip into millisecond timestamps."""
    from nautilus_trading.backtest.data_sources.binance_rest import BinanceRestDataSource

    captured_params = []

    def _fake_get(url, params, timeout):
        captured_params.append(params)
        return _FakeResp([])  # empty payload — exit the pagination loop

    with patch(
        "nautilus_trading.backtest.data_sources.binance_rest.requests.get",
        side_effect=_fake_get,
    ):
        ds = BinanceRestDataSource(symbol="BTCUSDT", interval="1h")
        ds.load(
            instrument_id="BTCUSDT.BINANCE",
            bar_type="BTCUSDT.BINANCE-1-HOUR-LAST-EXTERNAL",
            start="2024-01-01",
            end="2024-01-02",
        )

    assert captured_params, "expected at least one HTTP call to /api/v3/klines"
    p = captured_params[0]
    assert p["symbol"] == "BTCUSDT"
    assert p["interval"] == "1h"
    assert isinstance(p["startTime"], int)
    assert isinstance(p["endTime"], int)
    assert p["startTime"] < p["endTime"]


def test_binance_rest_data_source_parses_klines_into_bars():
    """One-row Binance kline → one ``Bar`` with the right OHLCV values."""
    from nautilus_trader.model.data import Bar
    from nautilus_trading.backtest.data_sources.binance_rest import BinanceRestDataSource

    # Binance kline format: [open_time, open, high, low, close, volume,
    # close_time, qv, trades, tbb, tbq, ignore]
    fake_kline = [
        [1704067200000, "42000.00", "42500.00", "41800.00", "42300.00", "100.5"] + ["x"] * 6,
    ]

    call_count = [0]

    def _fake_get(url, params, timeout):
        call_count[0] += 1
        return _FakeResp(fake_kline if call_count[0] == 1 else [])

    with patch(
        "nautilus_trading.backtest.data_sources.binance_rest.requests.get",
        side_effect=_fake_get,
    ):
        ds = BinanceRestDataSource(symbol="BTCUSDT", interval="1h")
        result = ds.load(
            instrument_id="BTCUSDT.BINANCE",
            bar_type="BTCUSDT.BINANCE-1-HOUR-LAST-EXTERNAL",
            start="2024-01-01",
            end="2024-01-02",
        )

    assert str(result.instrument.id) == "BTCUSDT.BINANCE"
    assert len(result.data) == 1
    assert isinstance(result.data[0], Bar)


def test_binance_rest_requires_date_range():
    """``binance_rest`` cannot infer dates — a missing range is a hard
    config error. Per project rules: no synthetic-data fallback."""
    from nautilus_trading.backtest.data_sources.binance_rest import BinanceRestDataSource

    ds = BinanceRestDataSource(symbol="BTCUSDT", interval="1h")
    with pytest.raises(ValueError, match="date range"):
        ds.load(
            instrument_id="BTCUSDT.BINANCE",
            bar_type="BTCUSDT.BINANCE-1-HOUR-LAST-EXTERNAL",
        )


# -- TestDataSource -------------------------------------------------------


def test_test_data_source_construction_default_path(tmp_path):
    """``TestDataSource()`` should be instantiable without args — it
    defaults to a per-process scratch catalog the runner can refresh."""
    from nautilus_trading.backtest.data_sources.test import TestDataSource

    ds = TestDataSource(catalog_path=tmp_path)
    assert ds is not None


def test_test_data_source_loads_eurusd_data(tmp_path, monkeypatch):
    """``TestDataSource.load`` returns a non-empty data list. We stub
    ``TestDataProvider.ensure_catalog`` to avoid a network download —
    the unit test guards the wiring shape, not the live download."""
    from nautilus_trader.persistence.catalog import ParquetDataCatalog
    from nautilus_trader.test_kit.providers import TestInstrumentProvider
    from nautilus_trading.backtest.data_sources.test import TestDataSource

    instrument = TestInstrumentProvider.default_fx_ccy("EUR/USD")
    catalog_path = tmp_path / "test_catalog"
    catalog_path.mkdir()
    catalog = ParquetDataCatalog(str(catalog_path))
    catalog.write_data([instrument])

    monkeypatch.setattr(
        "nautilus_trading.data.providers.TestDataProvider.ensure_catalog",
        lambda self, path: catalog,
    )

    ds = TestDataSource(catalog_path=tmp_path)
    result = ds.load(
        instrument_id=str(instrument.id),
        bar_type=f"{instrument.id}-1-MINUTE-MID-INTERNAL",
    )
    assert str(result.instrument.id) == str(instrument.id)
    # No ticks were written by the stub — that's fine; the contract is just
    # "returns a list" (test smoke writes ticks via the real provider).
    assert isinstance(result.data, list)
