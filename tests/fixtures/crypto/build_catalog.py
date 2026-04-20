"""
Build a small real-data ParquetDataCatalog fixture for sub-project A tests.

Downloads 336 real 1-hour BTCUSDT klines from Binance's public REST API
(2024-01-01 00:00 UTC .. 2024-01-14 23:00 UTC inclusive) and persists them,
along with a BTCUSDT.BINANCE CurrencyPair instrument, into:

    tests/fixtures/crypto/catalog/

Usage:
    cd nautilus && uv run python ../tests/fixtures/crypto/build_catalog.py

Self-contained; raises on any failure. NO synthetic-data fallback.
"""

from __future__ import annotations

import shutil
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parents[2]  # tests/fixtures/crypto -> tests/fixtures -> tests -> repo
if str(_REPO_ROOT / "nautilus" / "src") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "nautilus" / "src"))

import urllib.request

from nautilus_trader.model.currencies import BTC, USDT
from nautilus_trader.model.data import Bar, BarSpecification, BarType
from nautilus_trader.model.enums import AggregationSource, BarAggregation, PriceType
from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
from nautilus_trader.model.instruments import CurrencyPair
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.persistence.catalog.parquet import ParquetDataCatalog

CATALOG_DIR = _HERE / "catalog"
SYMBOL = "BTCUSDT"
INTERVAL = "1h"
START_MS = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
END_MS = int(datetime(2024, 1, 14, 23, tzinfo=timezone.utc).timestamp() * 1000)
BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"


def fetch_klines() -> list[list]:
    """Fetch 336 hourly klines from Binance public REST API. Raises on any failure."""
    params = f"?symbol={SYMBOL}&interval={INTERVAL}&startTime={START_MS}&endTime={END_MS}&limit=500"
    req = urllib.request.Request(BINANCE_KLINES_URL + params)
    with urllib.request.urlopen(req, timeout=30) as resp:
        import json as _json

        data = _json.loads(resp.read().decode())
    if len(data) < 300:
        raise RuntimeError(f"expected >=300 klines, got {len(data)}")
    return data


def build_instrument() -> CurrencyPair:
    instrument_id = InstrumentId(Symbol(SYMBOL), Venue("BINANCE"))
    return CurrencyPair(
        instrument_id=instrument_id,
        raw_symbol=Symbol(SYMBOL),
        base_currency=BTC,
        quote_currency=USDT,
        price_precision=2,
        size_precision=6,
        price_increment=Price.from_str("0.01"),
        size_increment=Quantity.from_str("0.000001"),
        lot_size=None,
        max_quantity=None,
        min_quantity=Quantity.from_str("0.00001"),
        max_notional=None,
        min_notional=None,
        max_price=None,
        min_price=None,
        margin_init=Decimal("0"),
        margin_maint=Decimal("0"),
        maker_fee=Decimal("0.001"),
        taker_fee=Decimal("0.001"),
        ts_event=0,
        ts_init=0,
    )


def build_bars(instrument: CurrencyPair, klines: list[list]) -> list[Bar]:
    bar_spec = BarSpecification(
        step=1,
        aggregation=BarAggregation.HOUR,
        price_type=PriceType.LAST,
    )
    bar_type = BarType(
        instrument_id=instrument.id,
        bar_spec=bar_spec,
        aggregation_source=AggregationSource.EXTERNAL,
    )
    bars: list[Bar] = []
    for k in klines:
        _open_ms, o, h, low, c, v, close_ms = k[0], k[1], k[2], k[3], k[4], k[5], k[6]
        ts_event_ns = int(close_ms) * 1_000_000
        bars.append(
            Bar(
                bar_type=bar_type,
                open=Price.from_str(str(o)),
                high=Price.from_str(str(h)),
                low=Price.from_str(str(low)),
                close=Price.from_str(str(c)),
                volume=Quantity.from_str(str(v)),
                ts_event=ts_event_ns,
                ts_init=ts_event_ns,
            )
        )
    if not bars:
        raise RuntimeError("produced 0 bars from Binance response")
    return bars


def main() -> None:
    if CATALOG_DIR.exists():
        shutil.rmtree(CATALOG_DIR)
    CATALOG_DIR.mkdir(parents=True, exist_ok=False)

    print(f"Fetching {SYMBOL} {INTERVAL} klines from Binance public API...")
    klines = fetch_klines()
    print(f"  got {len(klines)} klines")

    instrument = build_instrument()
    bars = build_bars(instrument, klines)

    catalog = ParquetDataCatalog(str(CATALOG_DIR))
    catalog.write_data([instrument])
    catalog.write_data(bars)

    print(f"Wrote fixture catalog to {CATALOG_DIR}")
    print(f"  {len(bars)} bars, 1 instrument ({instrument.id})")


if __name__ == "__main__":
    main()
