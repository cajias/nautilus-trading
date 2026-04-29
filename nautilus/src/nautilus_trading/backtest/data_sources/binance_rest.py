"""``BinanceRestDataSource`` — fetch klines via Binance public REST.

Originally ported from the now-deleted
``strategies/crypto/kronos/_fetch_binance.py`` (sub-project B.5 PR 2)
behind the :class:`~nautilus_trading.backtest.data_sources.DataSource`
protocol. After PR 3's parity-snapshot test confirmed equivalence,
the kronos helper was retired and this adapter became the canonical
Binance REST data source for any backtest YAML with
``data_source.type: binance_rest``.

No API key required (uses the unauthenticated ``/api/v3/klines``
endpoint). Pagination + 120ms throttle to stay well within Binance's
rate limits.

Instrument shape (precision, fees, currency mapping) is inlined here
rather than imported from a strategy module — keeps the adapter
self-contained and decoupled from any single strategy's wiring.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import pandas as pd  # type: ignore[import-untyped]
import requests  # type: ignore[import-untyped]
from nautilus_trader.model.currencies import BNB, BTC, ETH, SOL, USDT, Currency
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
from nautilus_trader.model.instruments import CurrencyPair
from nautilus_trader.model.objects import Price, Quantity

from nautilus_trading.backtest.data_sources import DataSourceResult

# Mirror of kronos/backtest_config._BASE_MAP — locked to this set until
# PR 3's parity test rejects any drift.
_BASE_MAP: dict[str, Currency] = {"BTC": BTC, "ETH": ETH, "BNB": BNB, "SOL": SOL}

_BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"
_REQUEST_LIMIT = 1000  # max candles per Binance request
_THROTTLE_SECONDS = 0.12  # sleep between paginated calls


def _build_crypto_instrument(symbol: str) -> CurrencyPair:
    """Inline of ``kronos/backtest_config.build_instrument``.

    Kept identical to the kronos helper so PR 3's parity-snapshot test
    doesn't fail for instrument-shape reasons. PR 3 deletes the kronos
    helper; the canonical version lives here from then on.
    """
    base_str = symbol.replace("USDT", "").replace("BUSD", "")
    base = _BASE_MAP.get(base_str) or Currency.from_str(base_str)
    instrument_id = InstrumentId(Symbol(symbol), Venue("BINANCE"))
    return CurrencyPair(
        instrument_id=instrument_id,
        raw_symbol=Symbol(symbol),
        base_currency=base,
        quote_currency=USDT,
        price_precision=2,
        size_precision=6,
        price_increment=Price.from_str("0.01"),
        size_increment=Quantity.from_str("0.000001"),
        lot_size=None,
        max_quantity=None,
        min_quantity=Quantity.from_str("0.000001"),
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


@dataclass(frozen=True)
class BinanceRestDataSource:
    """Adapter that pulls OHLCV klines from Binance public REST.

    Parameters
    ----------
    symbol : str
        Binance trading pair (e.g. ``"BTCUSDT"``).
    interval : str
        Kline interval (``"1m"``, ``"5m"``, ``"1h"``, ``"4h"``, …).
    """

    symbol: str
    interval: str

    def load(
        self,
        *,
        instrument_id: str,
        bar_type: str,
        start: str | None = None,
        end: str | None = None,
    ) -> DataSourceResult:
        if start is None or end is None:
            raise ValueError(
                "binance_rest requires a date range (start, end) — "
                "no synthetic-data fallback in runtime code",
            )

        instrument = _build_crypto_instrument(self.symbol)
        bt = BarType.from_str(bar_type)

        bars = self._fetch_bars(
            bt,
            start=start,
            end=end,
            price_precision=instrument.price_precision,
            size_precision=instrument.size_precision,
        )

        return DataSourceResult(instrument=instrument, data=bars)

    # ------------------------------------------------------------------
    # Internal: kline → Bar conversion (originally ported from the
    # now-deleted kronos/_fetch_binance.py — PR 3).
    # ------------------------------------------------------------------

    def _fetch_bars(
        self,
        bar_type: BarType,
        *,
        start: str,
        end: str,
        price_precision: int,
        size_precision: int,
    ) -> list[Bar]:
        start_ms = int(pd.Timestamp(start).timestamp() * 1000)
        end_ms = int(pd.Timestamp(end + " 23:59:59").timestamp() * 1000)

        all_rows: list[list[Any]] = []
        current = start_ms
        request_count = 0
        while current < end_ms:
            params = {
                "symbol": self.symbol,
                "interval": self.interval,
                "startTime": current,
                "endTime": end_ms,
                "limit": _REQUEST_LIMIT,
            }
            if request_count > 0:
                time.sleep(_THROTTLE_SECONDS)
            resp = requests.get(_BINANCE_KLINES_URL, params=params, timeout=30)
            resp.raise_for_status()
            rows = resp.json()
            request_count += 1
            if not rows:
                break
            all_rows.extend(rows)
            last_open_ms = int(rows[-1][0])
            if last_open_ms <= current:
                # Safety: prevent infinite loop if Binance returns the
                # same first kline twice.
                break
            current = last_open_ms + 1

        if not all_rows:
            return []

        price_fmt = f"{{:.{price_precision}f}}"
        size_fmt = f"{{:.{size_precision}f}}"

        bars: list[Bar] = []
        for row in all_rows:
            ts_ns = int(row[0]) * 1_000_000  # ms → ns
            bars.append(
                Bar(
                    bar_type=bar_type,
                    open=Price.from_str(price_fmt.format(float(row[1]))),
                    high=Price.from_str(price_fmt.format(float(row[2]))),
                    low=Price.from_str(price_fmt.format(float(row[3]))),
                    close=Price.from_str(price_fmt.format(float(row[4]))),
                    volume=Quantity.from_str(size_fmt.format(float(row[5]))),
                    ts_event=ts_ns,
                    ts_init=ts_ns,
                ),
            )
        return bars
