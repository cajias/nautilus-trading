"""
Build a small real-data ParquetDataCatalog fixture for R11 evaluator tests.

Downloads 336 real 1-hour BTCUSDT klines from Binance's public REST API
(2024-01-01 00:00 UTC .. 2024-01-14 23:00 UTC inclusive) and persists them,
along with a BTCUSDT.BINANCE CurrencyPair instrument, into the fixture
ParquetDataCatalog at:

    tests/competition/fixtures/catalog/

Usage:
    cd nautilus && uv run python ../tests/competition/fixtures/build_catalog.py

The helper is deliberately self-contained (logic copied, not imported, from
competition/evaluate_round11.py) so it survives evaluator refactors. It raises
on any failure — there is NO synthetic-data fallback.
"""

from __future__ import annotations

import shutil
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

# Repo-relative imports: evaluator lives at competition/evaluate_round11.py.
# We intentionally do NOT import from competition/ — keep this self-contained.
_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parents[3]  # tests/competition/fixtures → tests/competition → tests → repo
if str(_REPO_ROOT / "nautilus" / "src") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "nautilus" / "src"))

from nautilus_trader.model.currencies import BTC, USDT
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.instruments import CurrencyPair
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.persistence.catalog.parquet import ParquetDataCatalog

# ---------------------------------------------------------------------------
# Spec
# ---------------------------------------------------------------------------

INSTRUMENT_ID = InstrumentId.from_str("BTCUSDT.BINANCE")
BAR_TYPE_STR = "BTCUSDT.BINANCE-1-HOUR-LAST-EXTERNAL"
SYMBOL = "BTCUSDT"

START_UTC = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
END_UTC_INCLUSIVE = datetime(2024, 1, 14, 23, 0, tzinfo=timezone.utc)
EXPECTED_BAR_COUNT = 14 * 24  # 336

CATALOG_PATH = _HERE / "catalog"


def _get_instrument() -> CurrencyPair:
    """BTCUSDT.BINANCE Spot instrument. Matches evaluator _get_instrument()."""
    return CurrencyPair(
        instrument_id=INSTRUMENT_ID,
        raw_symbol=INSTRUMENT_ID.symbol,
        base_currency=BTC,
        quote_currency=USDT,
        price_precision=2,
        size_precision=5,
        price_increment=Price.from_str("0.01"),
        size_increment=Quantity.from_str("0.00001"),
        margin_init=Decimal("0"),
        margin_maint=Decimal("0"),
        maker_fee=Decimal("0.001"),
        taker_fee=Decimal("0.001"),
        ts_event=0,
        ts_init=0,
    )


def fetch_binance_bars_1h(
    symbol: str,
    start_utc: datetime,
    end_utc_inclusive: datetime,
) -> list[Bar]:
    """Download 1-hour klines from Binance public REST API.

    Returns exactly the bars whose open time lies in
    ``[start_utc, end_utc_inclusive]`` — i.e. inclusive on both sides.
    Binance's ``endTime`` is inclusive on the bar *open* boundary when it
    aligns with the bar grid, so we pass ``end_utc_inclusive`` directly.
    """
    try:
        import requests
    except ImportError as err:  # pragma: no cover
        raise RuntimeError("requests not installed: uv add requests") from err

    start_ms = int(start_utc.timestamp() * 1000)
    end_ms = int(end_utc_inclusive.timestamp() * 1000)

    url = "https://api.binance.com/api/v3/klines"
    raw: list[list] = []
    cur = start_ms
    print(f"  Fetching {symbol} 1h klines {start_utc.isoformat()} .. {end_utc_inclusive.isoformat()}")

    while cur <= end_ms:
        resp = requests.get(
            url,
            params={
                "symbol": symbol,
                "interval": "1h",
                "startTime": cur,
                "endTime": end_ms,
                "limit": 1000,
            },
            timeout=30,
        )
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        raw.extend(batch)
        last_open_ms = batch[-1][0]
        if last_open_ms >= end_ms or len(batch) < 1000:
            break
        cur = last_open_ms + 1
        time.sleep(0.12)

    # Dedupe on open-time (in case of pagination overlap) and filter to range.
    by_open: dict[int, list] = {}
    for k in raw:
        open_ms = k[0]
        if start_ms <= open_ms <= end_ms:
            by_open[open_ms] = k
    ordered = [by_open[ms] for ms in sorted(by_open)]

    instrument = _get_instrument()
    bar_type = BarType.from_str(BAR_TYPE_STR)
    price_prec = instrument.price_precision
    size_prec = instrument.size_precision

    bars: list[Bar] = []
    for k in ordered:
        ts_init = k[0] * 1_000_000  # open time ms → ns
        ts_event = k[6] * 1_000_000  # close time ms → ns
        bars.append(
            Bar(
                bar_type=bar_type,
                open=Price(float(k[1]), price_prec),
                high=Price(float(k[2]), price_prec),
                low=Price(float(k[3]), price_prec),
                close=Price(float(k[4]), price_prec),
                volume=Quantity(float(k[5]), size_prec),
                ts_event=ts_event,
                ts_init=ts_init,
            )
        )

    print(f"  Got {len(bars)} bars")
    return bars


def build_catalog() -> None:
    bars = fetch_binance_bars_1h(SYMBOL, START_UTC, END_UTC_INCLUSIVE)
    if len(bars) != EXPECTED_BAR_COUNT:
        raise RuntimeError(
            f"expected {EXPECTED_BAR_COUNT} bars, got {len(bars)} — check start/end bounds"
        )

    instrument = _get_instrument()

    # Wipe and recreate the catalog directory so the build is deterministic.
    if CATALOG_PATH.exists():
        shutil.rmtree(CATALOG_PATH)
    CATALOG_PATH.mkdir(parents=True, exist_ok=True)

    catalog = ParquetDataCatalog(str(CATALOG_PATH))
    catalog.write_data([instrument])
    catalog.write_data(bars)

    # Summary
    total_bytes = sum(p.stat().st_size for p in CATALOG_PATH.rglob("*") if p.is_file())
    print("=" * 60)
    print(f"Catalog:        {CATALOG_PATH}")
    print(f"Instruments:    1  ({INSTRUMENT_ID})")
    print(f"Bars:           {len(bars)}  ({BAR_TYPE_STR})")
    print(f"First ts_event: {bars[0].ts_event}")
    print(f"Last ts_event:  {bars[-1].ts_event}")
    print(f"Total size:     {total_bytes:,} bytes ({total_bytes / 1024:.1f} KiB)")
    print("=" * 60)


if __name__ == "__main__":
    build_catalog()
