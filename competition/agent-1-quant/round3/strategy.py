"""
Round 3 Strategy — Regime-Adaptive Multi-Strategy Selector
Agent 1: Quantitative Trader

Combines three sub-strategies selected by regime detection:
1. Mean Reversion (BB + RSI) — range-bound/choppy markets
2. Momentum (EMA cross + ADX filter) — trending markets
3. Volatility Breakout (Keltner Channel) — expansion phases

Regime detection: ADX + rolling volatility percentile.
4h BTCUSDT, 0.1% fees, 1.5x ATR stops, drawdown-scaled sizing.
"""

import requests
import pandas as pd
import numpy as np
import json
from dataclasses import dataclass
from typing import Optional


def fetch_binance_klines(
    symbol: str = "BTCUSDT",
    interval: str = "4h",
    start: str = "2024-07-01",
    end: str = "2025-03-31",
) -> pd.DataFrame:
    url = "https://api.binance.com/api/v3/klines"
    start_ms = int(pd.Timestamp(start).timestamp() * 1000)
    end_ms = int(pd.Timestamp(end).timestamp() * 1000)
    rows = []
    cur = start_ms
    while cur < end_ms:
        r = requests.get(url, params={
            "symbol": symbol, "interval": interval,
            "startTime": cur, "endTime": end_ms, "limit": 1000
        }, timeout=30)
        r.raise_for_status()
        d = r.json()
        if not d:
            break
        rows.extend(d)
        cur = d[-1][6] + 1

    df = pd.DataFrame(rows, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades", "taker_buy_base",
        "taker_buy_quote", "ignore",
    ])
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = df[c].astype(float)
    df["timestamp"] = pd.to_datetime(df["open_time"], unit="ms")
    df = df.set_index("timestamp").sort_index()
    return df


# ---------------------------------------------------------------------------
# Indicators (per-bar slicing, matching the approach that worked)
# ---------------------------------------------------------------------------

def ema(s, span):
    return s.ewm(span=span, adjust=False).mean()

def sma(s, w):
    return s.rolling(w).mean()

def calc_atr(df, period=14):
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - df["close"].shift(1)).abs(),
        (df["low"] - df["close"].shift(1)).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()

def calc_adx(df, period=14):
    up = df["high"] - df["high"].shift(1)
    down = df["low"].shift(1) - df["low"]
    pdm = np.where((up > down) & (up > 0), up, 0.0)
    mdm = np.where((down > up) & (down > 0), down, 0.0)
    atr_v = calc_atr(df, period)
    pdi = 100 * pd.Series(pdm, index=df.index).ewm(span=period, adjust=False).mean() / atr_v
    mdi = 100 * pd.Series(mdm, index=df.index).ewm(span=period, adjust=False).mean() / atr_v
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(span=period, adjust=False).mean()

def bb(close, w=20, n=2.0):
    mid = sma(close, w)
    std = close.rolling(w).std()
    return mid - n * std, mid, mid + n * std

def keltner(df, ema_p=20, atr_p=14, mult=1.5):
    mid = ema(df["close"], ema_p)
    a = calc_atr(df, atr_p)
    return mid - mult * a, mid, mid + mult * a

def calc_rsi(close, period=14):
    delta = close.diff()
    g = delta.clip(lower=0).ewm(span=period, adjust=False).mean()
    l = (-delta.clip(upper=0)).ewm(span=period, adjust=False).mean()
    return 100 - 100 / (1 + g / l.replace(0, np.nan))


# ---------------------------------------------------------------------------
# Regime detection
# ---------------------------------------------------------------------------

def detect_regime(df, lookback=30):
    adx_v = calc_adx(df, 14)
    vol = df["close"].pct_change().rolling(lookback).std()
    vol_pct = vol.rolling(120).rank(pct=True) * 100
    regime = pd.Series("mean_revert", index=df.index)
    regime[adx_v > 25] = "trending"
    regime[vol_pct > 70] = "volatile"
    return regime


# ---------------------------------------------------------------------------
# Sub-strategies
# ---------------------------------------------------------------------------

@dataclass
class Signal:
    side: Optional[str] = None
    sl: Optional[float] = None
    tp: Optional[float] = None


def mr_signal(df, i):
    """Bollinger Band mean reversion + RSI."""
    close = df["close"].iloc[:i+1]
    if len(close) < 30:
        return Signal()
    lo, mid, up = bb(close, 20, 2.0)
    rsi_v = calc_rsi(close, 14)
    price = close.iloc[-1]
    atr_v = calc_atr(df.iloc[:i+1], 14).iloc[-1]

    if price < lo.iloc[-1] and rsi_v.iloc[-1] < 35:
        return Signal("long", price - 1.5 * atr_v, mid.iloc[-1])
    elif price > up.iloc[-1] and rsi_v.iloc[-1] > 65:
        return Signal("short", price + 1.5 * atr_v, mid.iloc[-1])
    return Signal()


def mom_signal(df, i):
    """EMA crossover + ADX filter."""
    sub = df.iloc[:i+1]
    if len(sub) < 50:
        return Signal()
    close = sub["close"]
    fast = ema(close, 12)
    slow = ema(close, 26)
    adx_v = calc_adx(sub, 14)
    price = close.iloc[-1]
    atr_v = calc_atr(sub, 14).iloc[-1]

    if adx_v.iloc[-1] < 20:
        return Signal()
    if fast.iloc[-1] > slow.iloc[-1] and fast.iloc[-2] <= slow.iloc[-2]:
        return Signal("long", price - 2.0 * atr_v, price + 3.0 * atr_v)
    elif fast.iloc[-1] < slow.iloc[-1] and fast.iloc[-2] >= slow.iloc[-2]:
        return Signal("short", price + 2.0 * atr_v, price - 3.0 * atr_v)
    return Signal()


def brk_signal(df, i):
    """Keltner Channel breakout."""
    sub = df.iloc[:i+1]
    if len(sub) < 30:
        return Signal()
    klo, _, kup = keltner(sub, 20, 14, 1.5)
    price = sub["close"].iloc[-1]
    prev = sub["close"].iloc[-2]
    atr_v = calc_atr(sub, 14).iloc[-1]

    if price > kup.iloc[-1] and prev <= kup.iloc[-2]:
        return Signal("long", price - 2.0 * atr_v, price + 3.0 * atr_v)
    elif price < klo.iloc[-1] and prev >= klo.iloc[-2]:
        return Signal("short", price + 2.0 * atr_v, price - 3.0 * atr_v)
    return Signal()


# ---------------------------------------------------------------------------
# Backtest
# ---------------------------------------------------------------------------

FEE = 0.001

@dataclass
class Pos:
    side: str
    entry: float
    sz: float
    sl: float
    tp: float
    t: str
    regime: str


def run_backtest(
    start: str = "2024-07-01",
    end: str = "2024-12-31",
    initial_capital: float = 1000.0,
) -> dict:
    buf = (pd.Timestamp(start) - pd.Timedelta(days=60)).strftime("%Y-%m-%d")
    fetch_end = (pd.Timestamp(end) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    df = fetch_binance_klines("BTCUSDT", "4h", buf, fetch_end)
    regime_s = detect_regime(df, 30)

    s_ts, e_ts = pd.Timestamp(start), pd.Timestamp(end)
    eq = initial_capital
    pk = initial_capital
    mdd = 0.0
    pos: Optional[Pos] = None
    log = []

    def psz(e, p):
        dd = (p - e) / p if p > 0 else 0
        pct = 0.40
        if dd > 0.10:
            pct = 0.25
        if dd > 0.20:
            pct = 0.15
        return e * pct

    strat_map = {"mean_revert": mr_signal, "trending": mom_signal, "volatile": brk_signal}

    for i in range(1, len(df)):
        ts = df.index[i]
        if ts < s_ts or ts > e_ts:
            continue

        price = df["close"].iloc[i]
        hi = df["high"].iloc[i]
        lo = df["low"].iloc[i]

        # Exit
        if pos is not None:
            ex = exr = None
            if pos.side == "long":
                if lo <= pos.sl:
                    ex, exr = pos.sl, "sl"
                elif hi >= pos.tp:
                    ex, exr = pos.tp, "tp"
            else:
                if hi >= pos.sl:
                    ex, exr = pos.sl, "sl"
                elif lo <= pos.tp:
                    ex, exr = pos.tp, "tp"

            if ex:
                btc = pos.sz / pos.entry
                fee = btc * ex * FEE
                pnl = btc * ((ex - pos.entry) if pos.side == "long" else (pos.entry - ex)) - fee
                eq += pnl
                pk = max(pk, eq)
                dd = (pk - eq) / pk * 100
                mdd = max(mdd, dd)
                log.append({
                    "entry_time": pos.t, "exit_time": str(ts),
                    "side": pos.side,
                    "entry_price": round(pos.entry, 2), "exit_price": round(ex, 2),
                    "pnl": round(pnl, 2), "exit_reason": exr,
                    "regime": pos.regime, "equity_after": round(eq, 2),
                })
                pos = None

        # Entry
        if pos is None:
            regime = regime_s.iloc[i]
            fn = strat_map.get(regime, mr_signal)
            sig = fn(df, i)
            # Fallback: try other strategies
            if sig.side is None:
                for alt_r, alt_fn in strat_map.items():
                    if alt_fn != fn:
                        sig = alt_fn(df, i)
                        if sig.side is not None:
                            regime = alt_r
                            break

            if sig.side and sig.sl:
                s = psz(eq, pk)
                eq -= s * FEE
                pos = Pos(sig.side, price, s, sig.sl, sig.tp, str(ts), regime)

    # Force close
    if pos:
        fp = df["close"].iloc[-1]
        btc = pos.sz / pos.entry
        fee = btc * fp * FEE
        pnl = btc * ((fp - pos.entry) if pos.side == "long" else (pos.entry - fp)) - fee
        eq += pnl
        log.append({
            "entry_time": pos.t, "exit_time": str(df.index[-1]),
            "side": pos.side, "entry_price": round(pos.entry, 2),
            "exit_price": round(fp, 2), "pnl": round(pnl, 2),
            "exit_reason": "eod", "regime": pos.regime,
            "equity_after": round(eq, 2),
        })

    pk = max(pk, eq)
    mdd = max(mdd, (pk - eq) / pk * 100)

    if log:
        rets = [t["pnl"] / initial_capital for t in log]
        sharpe = (np.mean(rets) / np.std(rets) * np.sqrt(len(rets))) if np.std(rets) > 0 else 0
        wr = sum(1 for t in log if t["pnl"] > 0) / len(log) * 100
    else:
        sharpe = wr = 0.0

    return {
        "final_equity": round(eq, 2),
        "total_return_pct": round((eq - initial_capital) / initial_capital * 100, 2),
        "sharpe_ratio": round(sharpe, 2),
        "max_drawdown_pct": round(mdd, 2),
        "num_trades": len(log),
        "win_rate": round(wr, 2),
        "trade_log": log,
    }


if __name__ == "__main__":
    print("=" * 60)
    print("ROUND 3 — Agent 1: Regime-Adaptive Multi-Strategy (4h)")
    print("=" * 60)

    print("\n--- TRAIN (2024-07-01 to 2024-12-31) ---")
    train = run_backtest("2024-07-01", "2024-12-31", 1000)
    for k, v in train.items():
        if k != "trade_log":
            print(f"  {k}: {v}")

    print("\n--- TEST (2025-01-01 to 2025-03-31) ---")
    test = run_backtest("2025-01-01", "2025-03-31", 1000)
    for k, v in test.items():
        if k != "trade_log":
            print(f"  {k}: {v}")

    results = {
        "agent": "Agent 1 — Quantitative Trader",
        "round": 3,
        "strategy_name": "Regime-Adaptive Multi-Strategy Selector (4h)",
        "description": (
            "Three sub-strategies (BB mean reversion, EMA momentum, Keltner breakout) "
            "selected by ADX+vol regime detection. 4h BTCUSDT, 0.1% fees, "
            "1.5-2x ATR stops, drawdown-scaled sizing (40% base)."
        ),
        "train": {k: v for k, v in train.items() if k != "trade_log"},
        "test": {k: v for k, v in test.items() if k != "trade_log"},
        "train_trade_log": train["trade_log"],
        "test_trade_log": test["trade_log"],
    }

    path = "/Users/rc/Projects/workspace/nautilus-trading/competition/agent-1-quant/round3/results.txt"
    with open(path, "w") as f:
        f.write(json.dumps(results, indent=2))
    print(f"\nResults saved to {path}")
