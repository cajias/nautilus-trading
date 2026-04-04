"""Data provider abstraction for multiple market data sources."""

from __future__ import annotations

import json
import time
import urllib.request
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from nautilus_trader.model.data import Bar, BarSpecification, BarType, QuoteTick
from nautilus_trader.model.enums import AggregationSource, BarAggregation, CurrencyType, PriceType
from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
from nautilus_trader.model.instruments import CurrencyPair
from nautilus_trader.model.objects import Currency, Money, Price, Quantity
from nautilus_trader.persistence.catalog import ParquetDataCatalog
from nautilus_trader.persistence.wranglers import QuoteTickDataWrangler
from nautilus_trader.test_kit.providers import CSVTickDataLoader, TestInstrumentProvider


class DataProvider(ABC):
    """Base class for market data providers."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider identifier (e.g., 'test', 'binance')."""

    @abstractmethod
    def ensure_catalog(self, catalog_path: Path) -> ParquetDataCatalog:
        """Download data and return a ready catalog."""


class TestDataProvider(DataProvider):
    """Built-in test data provider (EUR/USD ticks from nautilus_data)."""

    _DEFAULT_URL = (
        "https://raw.githubusercontent.com/nautechsystems/nautilus_data/"
        "main/raw_data/fx_hist_data/DAT_ASCII_EURUSD_T_202001.csv.gz"
    )
    _DEFAULT_FILENAME = "EURUSD_202001.csv.gz"
    _DEFAULT_PAIR = "EUR/USD"

    def __init__(
        self,
        *,
        url: str = _DEFAULT_URL,
        filename: str = _DEFAULT_FILENAME,
        pair: str = _DEFAULT_PAIR,
    ) -> None:
        self._url = url
        self._filename = filename
        self._pair = pair

    @property
    def name(self) -> str:
        return "test"

    def ensure_catalog(self, catalog_path: Path) -> ParquetDataCatalog:
        """Download sample EUR/USD tick data (if needed) and write to a Parquet catalog.

        Returns the populated catalog instance.
        """
        catalog_path.mkdir(parents=True, exist_ok=True)

        if self._catalog_has_data(catalog_path):
            print(f"Catalog already populated at {catalog_path}")
            return ParquetDataCatalog(str(catalog_path))

        download_path = catalog_path / self._filename
        if not download_path.exists():
            print(f"Downloading sample tick data from {self._url} ...")
            try:
                with urllib.request.urlopen(self._url, timeout=60) as resp:
                    download_path.write_bytes(resp.read())
            except Exception:
                download_path.unlink(missing_ok=True)
                raise

        instrument = TestInstrumentProvider.default_fx_ccy(self._pair)
        wrangler = QuoteTickDataWrangler(instrument)

        df = CSVTickDataLoader.load(
            str(download_path), index_col=0, datetime_format="%Y%m%d %H%M%S%f"
        )
        df.columns = ["bid_price", "ask_price", "size"]
        ticks: list[QuoteTick] = wrangler.process(df)

        catalog = ParquetDataCatalog(str(catalog_path))
        catalog.write_data([instrument])
        catalog.write_data(ticks)

        # Clean up the compressed CSV after ingestion
        download_path.unlink(missing_ok=True)

        print(f"Loaded {len(ticks):,} ticks into catalog at {catalog_path}")
        return catalog

    def _catalog_has_data(self, catalog_path: Path) -> bool:
        """Check whether the catalog directory already contains instrument data."""
        try:
            catalog = ParquetDataCatalog(str(catalog_path))
            instruments = catalog.instruments()
            return any(
                self._pair.replace("/", "") in str(inst.id) for inst in instruments
            )
        except Exception:
            return False


# ---------------------------------------------------------------------------
# Binance kline interval -> NautilusTrader BarAggregation mapping
# ---------------------------------------------------------------------------

_BINANCE_INTERVAL_MAP: dict[str, tuple[int, BarAggregation]] = {
    "1s": (1, BarAggregation.SECOND),
    "1m": (1, BarAggregation.MINUTE),
    "3m": (3, BarAggregation.MINUTE),
    "5m": (5, BarAggregation.MINUTE),
    "15m": (15, BarAggregation.MINUTE),
    "30m": (30, BarAggregation.MINUTE),
    "1h": (1, BarAggregation.HOUR),
    "2h": (2, BarAggregation.HOUR),
    "4h": (4, BarAggregation.HOUR),
    "6h": (6, BarAggregation.HOUR),
    "8h": (8, BarAggregation.HOUR),
    "12h": (12, BarAggregation.HOUR),
    "1d": (1, BarAggregation.DAY),
    "3d": (3, BarAggregation.DAY),
    "1w": (1, BarAggregation.WEEK),
    "1M": (1, BarAggregation.MONTH),
}

# Pair symbol -> (price_precision, size_precision, min_quantity, price_increment, size_increment)
# Reasonable defaults; the provider fetches exchange info when possible.
_PAIR_DEFAULTS: dict[str, tuple[int, int, str, str, str]] = {
    "BTCUSDT": (2, 5, "0.00001", "0.01", "0.00001"),
    "ETHUSDT": (2, 4, "0.0001", "0.01", "0.0001"),
    "SOLUSDT": (2, 2, "0.01", "0.01", "0.01"),
    "BNBUSDT": (2, 3, "0.001", "0.01", "0.001"),
    "XRPUSDT": (4, 1, "0.1", "0.0001", "0.1"),
    "DOGEUSDT": (5, 0, "1", "0.00001", "1"),
    "ADAUSDT": (4, 1, "0.1", "0.0001", "0.1"),
}

# Fallback when a pair is not in the defaults table above
_FALLBACK_DEFAULTS: tuple[int, int, str, str, str] = (4, 4, "0.0001", "0.0001", "0.0001")


def _parse_base_quote(symbol: str) -> tuple[str, str]:
    """Split a Binance symbol like 'BTCUSDT' into ('BTC', 'USDT').

    Handles common quote currencies: USDT, USDC, BUSD, BTC, ETH, BNB, FDUSD.
    """
    for quote in ("FDUSD", "USDT", "USDC", "BUSD", "BTC", "ETH", "BNB"):
        if symbol.endswith(quote) and len(symbol) > len(quote):
            return symbol[: -len(quote)], quote
    # Fallback: assume last 4 chars are quote
    return symbol[:-4], symbol[-4:]


def _get_or_create_currency(code: str) -> Currency:
    """Return a Currency from the internal map, or create a crypto currency."""
    currency = Currency.from_internal_map(code)
    if currency is not None:
        return currency
    # Create a crypto currency with reasonable precision
    precision = 8 if code not in ("USDT", "USDC", "BUSD", "FDUSD") else 2
    return Currency(
        code=code,
        precision=precision,
        iso4217=0,
        name=code,
        currency_type=CurrencyType.CRYPTO,
    )


def _build_crypto_instrument(
    symbol: str,
    *,
    ts_now_ns: int,
) -> CurrencyPair:
    """Build a CurrencyPair instrument for a Binance crypto pair."""
    base_code, quote_code = _parse_base_quote(symbol)
    base_currency = _get_or_create_currency(base_code)
    quote_currency = _get_or_create_currency(quote_code)

    defaults = _PAIR_DEFAULTS.get(symbol, _FALLBACK_DEFAULTS)
    price_prec, size_prec, min_qty_str, price_inc_str, size_inc_str = defaults

    instrument_id = InstrumentId(
        symbol=Symbol(symbol),
        venue=Venue("BINANCE"),
    )

    return CurrencyPair(
        instrument_id=instrument_id,
        raw_symbol=Symbol(symbol),
        base_currency=base_currency,
        quote_currency=quote_currency,
        price_precision=price_prec,
        size_precision=size_prec,
        price_increment=Price.from_str(price_inc_str),
        size_increment=Quantity.from_str(size_inc_str),
        lot_size=Quantity.from_str(min_qty_str),
        min_quantity=Quantity.from_str(min_qty_str),
        max_quantity=Quantity.from_str("9000"),
        min_notional=Money(10.0, quote_currency),
        max_notional=Money(9_000_000.0, quote_currency),
        max_price=Price.from_str("1000000." + "0" * price_prec),
        min_price=Price.from_str(price_inc_str),
        margin_init=Decimal("0"),
        margin_maint=Decimal("0"),
        maker_fee=Decimal("0.001"),
        taker_fee=Decimal("0.001"),
        ts_event=ts_now_ns,
        ts_init=ts_now_ns,
        info={"exchange": "BINANCE"},
    )


class BinanceDataProvider(DataProvider):
    """Download historical klines (OHLCV bars) from the Binance public REST API.

    No API key required -- uses the unauthenticated ``/api/v3/klines`` endpoint.

    Parameters
    ----------
    pairs : list[str], default ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
        Binance trading pair symbols.
    interval : str, default "1h"
        Kline interval (e.g. "1m", "5m", "15m", "1h", "4h", "1d").
    start_date : datetime | None
        Start of the date range (UTC).  Defaults to 90 days ago.
    end_date : datetime | None
        End of the date range (UTC).  Defaults to now.
    """

    _BASE_URL = "https://api.binance.com/api/v3/klines"
    _LIMIT = 1000  # max candles per request

    def __init__(
        self,
        *,
        pairs: list[str] | None = None,
        interval: str = "1h",
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> None:
        self._pairs = pairs or ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
        if interval not in _BINANCE_INTERVAL_MAP:
            valid = ", ".join(sorted(_BINANCE_INTERVAL_MAP.keys()))
            raise ValueError(f"Unsupported interval '{interval}'. Valid: {valid}")
        self._interval = interval
        now = datetime.now(timezone.utc)
        self._start_date = start_date or (now - timedelta(days=90))
        self._end_date = end_date or now

    @property
    def name(self) -> str:
        return "binance"

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def ensure_catalog(self, catalog_path: Path) -> ParquetDataCatalog:
        """Download Binance klines for each pair and write to a Parquet catalog."""
        catalog_path.mkdir(parents=True, exist_ok=True)
        catalog = ParquetDataCatalog(str(catalog_path))

        ts_now_ns = int(time.time() * 1e9)

        for pair in self._pairs:
            if self._catalog_has_pair(catalog, pair):
                print(f"[binance] {pair} already in catalog, skipping.")
                continue

            instrument = _build_crypto_instrument(pair, ts_now_ns=ts_now_ns)
            bars = self._download_klines(pair, instrument)

            if not bars:
                print(f"[binance] WARNING: no bars returned for {pair}")
                continue

            catalog.write_data([instrument])
            catalog.write_data(bars)
            print(
                f"[binance] {pair}: wrote {len(bars):,} bars "
                f"({self._interval}) to {catalog_path}"
            )

        return catalog

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _catalog_has_pair(self, catalog: ParquetDataCatalog, pair: str) -> bool:
        """Return True if the catalog already contains data for *pair*."""
        try:
            instruments = catalog.instruments()
            return any(pair in str(inst.id) for inst in instruments)
        except Exception:
            return False

    def _download_klines(
        self,
        pair: str,
        instrument: CurrencyPair,
    ) -> list[Bar]:
        """Paginate through the Binance klines endpoint and return Bar objects."""
        step, aggregation = _BINANCE_INTERVAL_MAP[self._interval]
        bar_spec = BarSpecification(step, aggregation, PriceType.LAST)
        bar_type = BarType(instrument.id, bar_spec, AggregationSource.EXTERNAL)

        price_precision = instrument.price_precision
        size_precision = instrument.size_precision

        start_ms = int(self._start_date.timestamp() * 1000)
        end_ms = int(self._end_date.timestamp() * 1000)

        all_bars: list[Bar] = []
        current_start_ms = start_ms
        request_count = 0

        while current_start_ms < end_ms:
            url = (
                f"{self._BASE_URL}"
                f"?symbol={pair}"
                f"&interval={self._interval}"
                f"&startTime={current_start_ms}"
                f"&endTime={end_ms}"
                f"&limit={self._LIMIT}"
            )

            # Rate-limit: 100ms between requests to stay well within Binance limits
            request_count += 1
            if request_count > 1:
                time.sleep(0.1)

            try:
                req = urllib.request.Request(url)
                req.add_header("Accept", "application/json")
                with urllib.request.urlopen(req, timeout=30) as resp:
                    raw = json.loads(resp.read().decode())
            except Exception as exc:
                raise RuntimeError(
                    f"Binance API error fetching {pair} klines "
                    f"(startTime={current_start_ms}): {exc}"
                ) from exc

            if not raw:
                break

            for kline in raw:
                # Binance kline format:
                # [0] open_time, [1] open, [2] high, [3] low, [4] close,
                # [5] volume, [6] close_time, ...
                ts_ns = int(kline[0]) * 1_000_000  # ms -> ns

                bar = Bar(
                    bar_type=bar_type,
                    open=Price(float(kline[1]), precision=price_precision),
                    high=Price(float(kline[2]), precision=price_precision),
                    low=Price(float(kline[3]), precision=price_precision),
                    close=Price(float(kline[4]), precision=price_precision),
                    volume=Quantity(float(kline[5]), precision=size_precision),
                    ts_event=ts_ns,
                    ts_init=ts_ns,
                )
                all_bars.append(bar)

            # Advance past the last kline's open_time to avoid duplicates
            last_open_ms = int(raw[-1][0])
            if last_open_ms <= current_start_ms:
                # Safety: prevent infinite loop if API returns same data
                break
            current_start_ms = last_open_ms + 1

            print(
                f"[binance] {pair}: fetched {len(raw)} klines "
                f"(total: {len(all_bars):,}, requests: {request_count})"
            )

        return all_bars
