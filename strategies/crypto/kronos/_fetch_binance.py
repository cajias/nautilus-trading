"""Fetches OHLCV data from the Binance public REST API and converts it to NautilusTrader Bar objects."""

from __future__ import annotations

import time
from typing import Any

import pandas as pd
import requests
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.objects import Price, Quantity


def fetch_bars_from_binance(
    *,
    symbol: str,
    interval: str,
    start: str,
    end: str,
    bar_type: BarType,
    price_precision: int = 2,
    size_precision: int = 6,
) -> list[Bar]:
    """Fetch Binance klines and convert to NautilusTrader Bars."""
    url = "https://api.binance.com/api/v3/klines"
    start_ms = int(pd.Timestamp(start).timestamp() * 1000)
    end_ms = int(pd.Timestamp(end + " 23:59:59").timestamp() * 1000)
    all_rows: list[list[Any]] = []
    current = start_ms

    while current < end_ms:
        params = {
            "symbol": symbol,
            "interval": interval,
            "startTime": current,
            "endTime": end_ms,
            "limit": 1000,
        }
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        rows = resp.json()
        if not rows:
            break
        all_rows.extend(rows)
        current = rows[-1][0] + 1
        time.sleep(0.12)

    cols = [
        "open_time",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "close_time",
        "qv",
        "trades",
        "tbb",
        "tbq",
        "ignore",
    ]
    df = pd.DataFrame(all_rows, columns=cols)
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = df[c].astype(float)
    df["timestamp"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df = df.set_index("timestamp").sort_index()

    price_fmt = f"{{:.{price_precision}f}}"
    size_fmt = f"{{:.{size_precision}f}}"
    bars: list[Bar] = []
    for ts, row in df.iterrows():
        ts_ns = int(ts.timestamp() * 1_000_000_000)
        bars.append(
            Bar(
                bar_type=bar_type,
                open=Price.from_str(price_fmt.format(row["open"])),
                high=Price.from_str(price_fmt.format(row["high"])),
                low=Price.from_str(price_fmt.format(row["low"])),
                close=Price.from_str(price_fmt.format(row["close"])),
                volume=Quantity.from_str(size_fmt.format(row["volume"])),
                ts_event=ts_ns,
                ts_init=ts_ns,
            )
        )
    return bars
