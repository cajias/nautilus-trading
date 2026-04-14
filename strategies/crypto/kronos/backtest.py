"""Kronos integration backtest runner.

Runs KronosActor + KronosStrategy together in a NautilusTrader BacktestEngine
against Binance OHLCV data fetched directly from the public REST API.

Usage
-----
    # From repo root:
    cd nautilus && uv run python ../strategies/crypto/kronos/backtest.py

    # With config overrides:
    KRONOS_MODEL_SIZE=mini KRONOS_SYMBOL=ETHUSDT \\
        uv run python ../strategies/crypto/kronos/backtest.py

Environment variables (all optional)
-------------------------------------
    KRONOS_MODEL_SIZE       mini | base              (default: mini)
    KRONOS_SYMBOL           e.g. BTCUSDT             (default: BTCUSDT)
    KRONOS_INTERVAL         1h | 4h | 1d             (default: 1h)
    KRONOS_START            YYYY-MM-DD               (default: 2024-01-01)
    KRONOS_END              YYYY-MM-DD               (default: 2024-12-31)
    KRONOS_INITIAL_CAPITAL  float (USDT)             (default: 500.0)
    KRONOS_TRADE_SIZE       float (base asset units) (default: 0.001)
    KRONOS_N_SAMPLES        Monte Carlo samples      (default: 50)
    KRONOS_FORECAST_BARS    forecast horizon in bars (default: 24)
    KRONOS_INFERENCE_INTERVAL every N bars           (default: 4)

Backtest architecture
---------------------
    BacktestEngine
        ├── BacktestVenueConfig (BINANCE, SPOT, CASH, USDT)
        ├── KronosActor           ← inference + signal publication
        └── KronosStrategy        ← subscribes to signals, submits orders

Note: This script uses BacktestEngine directly (not BacktestNode) so we can
add_actor() alongside add_strategy(). BacktestNode does not yet expose
actor injection via config dicts in v1.x.
"""

from __future__ import annotations

import os
import sys
import time
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd
import requests

# ---------------------------------------------------------------------------
# Ensure repo root is on sys.path for strategy imports
# ---------------------------------------------------------------------------

_REPO_ROOT = str(Path(__file__).resolve().parents[4])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.currencies import USDT
from nautilus_trader.model.data import Bar, BarSpecification, BarType
from nautilus_trader.model.enums import (
    AccountType,
    AggregationSource,
    BarAggregation,
    OmsType,
    PriceType,
)
from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
from nautilus_trader.model.instruments import CurrencyPair
from nautilus_trader.model.objects import Money, Price, Quantity

from strategies.crypto.kronos.actor import KronosActor, KronosActorConfig
from strategies.crypto.kronos.strategy import KronosStrategy, KronosStrategyConfig

# ---------------------------------------------------------------------------
# Configuration from environment
# ---------------------------------------------------------------------------

MODEL_SIZE = os.getenv("KRONOS_MODEL_SIZE", "mini")
SYMBOL = os.getenv("KRONOS_SYMBOL", "BTCUSDT")
INTERVAL = os.getenv("KRONOS_INTERVAL", "1h")
START = os.getenv("KRONOS_START", "2024-01-01")
END = os.getenv("KRONOS_END", "2024-12-31")
INITIAL_CAPITAL = float(os.getenv("KRONOS_INITIAL_CAPITAL", "500.0"))
TRADE_SIZE = Decimal(os.getenv("KRONOS_TRADE_SIZE", "0.001"))
N_SAMPLES = int(os.getenv("KRONOS_N_SAMPLES", "50"))
FORECAST_BARS = int(os.getenv("KRONOS_FORECAST_BARS", "24"))
INFERENCE_INTERVAL = int(os.getenv("KRONOS_INFERENCE_INTERVAL", "4"))

VENUE = Venue("BINANCE")
INSTRUMENT_ID = InstrumentId(Symbol(SYMBOL), VENUE)

# Binance interval → NautilusTrader BarAggregation mapping
_NT_AGGREGATION: dict[str, tuple[int, BarAggregation]] = {
    "1m": (1, BarAggregation.MINUTE),
    "5m": (5, BarAggregation.MINUTE),
    "15m": (15, BarAggregation.MINUTE),
    "1h": (1, BarAggregation.HOUR),
    "4h": (4, BarAggregation.HOUR),
    "1d": (1, BarAggregation.DAY),
}


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------

def _fetch_binance_klines(symbol: str, interval: str, start: str, end: str) -> pd.DataFrame:
    """Fetch OHLCV from Binance public REST API, handling pagination."""
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

    cols = ["open_time", "open", "high", "low", "close", "volume",
            "close_time", "qv", "trades", "tbb", "tbq", "ignore"]
    df = pd.DataFrame(all_rows, columns=cols)
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = df[c].astype(float)
    df["timestamp"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    return df.set_index("timestamp").sort_index()


def _build_bars(
    df: pd.DataFrame,
    bar_type: BarType,
    price_precision: int = 2,
    size_precision: int = 6,
) -> list[Bar]:
    """Convert a Binance klines DataFrame into NautilusTrader Bar objects.

    price_precision and size_precision must match the instrument definition —
    NautilusTrader validates bar prices against instrument.price_precision.
    """
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


# ---------------------------------------------------------------------------
# Instrument builder
# ---------------------------------------------------------------------------

def _build_instrument(symbol: str, venue: Venue) -> CurrencyPair:
    """Build a minimal CurrencyPair instrument for the backtest engine."""
    from nautilus_trader.model.currencies import BNB, BTC, ETH, SOL

    _BASE_MAP = {
        "BTC": BTC,
        "ETH": ETH,
        "BNB": BNB,
        "SOL": SOL,
    }
    # Extract base currency from symbol (e.g. "BTCUSDT" → "BTC")
    base_str = symbol.replace("USDT", "").replace("BUSD", "")
    from nautilus_trader.model.currencies import Currency
    base_currency = _BASE_MAP.get(base_str) or Currency.from_str(base_str)

    instrument_id = InstrumentId(Symbol(symbol), venue)
    return CurrencyPair(
        instrument_id=instrument_id,
        raw_symbol=Symbol(symbol),
        base_currency=base_currency,
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


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def _compute_metrics(engine: BacktestEngine) -> dict[str, Any]:
    """Extract backtest performance metrics from engine reports."""
    try:
        fills = engine.trader.generate_order_fills_report()
        account = engine.trader.generate_account_report(VENUE)
    except Exception:
        fills = pd.DataFrame()
        account = pd.DataFrame()

    num_trades = len(fills) // 2 if len(fills) > 0 else 0

    # Approximate final equity from account report
    final_equity = INITIAL_CAPITAL
    if not account.empty and "balance" in account.columns:
        try:
            final_equity = float(account["balance"].iloc[-1])
        except Exception:
            pass

    total_return_pct = (final_equity - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100

    # Win rate from fills
    win_rate = 0.0
    if not fills.empty:
        try:
            pnls = fills["realized_pnl"].str.replace(r"\s+\w+$", "", regex=True).astype(float)
            sells = pnls[pnls != 0]
            if len(sells) > 0:
                win_rate = (sells > 0).sum() / len(sells) * 100
        except Exception:
            pass

    return {
        "model_size": MODEL_SIZE,
        "symbol": SYMBOL,
        "interval": INTERVAL,
        "start": START,
        "end": END,
        "initial_capital": INITIAL_CAPITAL,
        "final_equity": round(final_equity, 2),
        "total_return_pct": round(total_return_pct, 2),
        "num_trades": num_trades,
        "win_rate": round(win_rate, 1),
    }


# ---------------------------------------------------------------------------
# Main backtest
# ---------------------------------------------------------------------------

def run_backtest() -> dict[str, Any]:
    """Fetch data, build engine, run Kronos actor+strategy, return metrics."""
    # 1. Fetch data
    print(f"Fetching {SYMBOL} {INTERVAL} bars from {START} to {END} ...")
    df = _fetch_binance_klines(SYMBOL, INTERVAL, START, END)
    print(f"  Got {len(df)} bars")

    # 2. Build bar type
    step, aggregation = _NT_AGGREGATION.get(INTERVAL, (1, BarAggregation.HOUR))
    bar_spec = BarSpecification(step, aggregation, PriceType.LAST)
    bar_type = BarType(INSTRUMENT_ID, bar_spec, AggregationSource.EXTERNAL)

    # 3. Build instrument
    instrument = _build_instrument(SYMBOL, VENUE)

    # 4. Build bars (precision must match the instrument definition)
    bars = _build_bars(
        df,
        bar_type,
        price_precision=instrument.price_precision,
        size_precision=instrument.size_precision,
    )

    # 5. Build engine
    engine = BacktestEngine(
        config=BacktestEngineConfig(
            logging=LoggingConfig(log_level="WARNING"),
        ),
    )
    engine.add_venue(
        venue=VENUE,
        oms_type=OmsType.NETTING,
        account_type=AccountType.CASH,
        base_currency=None,  # multi-currency
        starting_balances=[Money(INITIAL_CAPITAL, USDT)],
    )
    engine.add_instrument(instrument)
    engine.add_data(bars)

    # 6. Build and add actor (Kronos inference)
    actor_config = KronosActorConfig(
        instrument_id=INSTRUMENT_ID,
        bar_type=bar_type,
        model_size=MODEL_SIZE,
        forecast_horizon=FORECAST_BARS,
        inference_interval_bars=INFERENCE_INTERVAL,
        n_samples=N_SAMPLES,
    )
    actor = KronosActor(config=actor_config)
    engine.add_actor(actor)

    # 7. Build and add strategy (subscribes to KronosSignal)
    strategy_config = KronosStrategyConfig(
        instrument_id=INSTRUMENT_ID,
        bar_type=bar_type,
        trade_size=TRADE_SIZE,
    )
    strategy = KronosStrategy(config=strategy_config)
    engine.add_strategy(strategy)

    # 8. Run
    print("Running backtest ...")
    engine.run()

    # 9. Collect metrics
    metrics = _compute_metrics(engine)
    engine.dispose()
    return metrics


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    print("=" * 60)
    print("KRONOS INTEGRATION BACKTEST")
    print(f"  Model : {MODEL_SIZE}")
    print(f"  Symbol: {SYMBOL} ({INTERVAL})")
    print(f"  Period: {START} → {END}")
    print(f"  Capital: ${INITIAL_CAPITAL:,.2f} USDT")
    print("=" * 60)

    metrics = run_backtest()

    print("\nRESULTS")
    print("-" * 40)
    for k, v in metrics.items():
        print(f"  {k:<22}: {v}")
    print("-" * 40)
    ret = metrics.get("total_return_pct", 0)
    print(f"\n{'PROFIT' if ret >= 0 else 'LOSS'}: {ret:+.2f}%")


if __name__ == "__main__":
    main()
