"""Agent 5 - Round 7: Hybrid Tournament Strategist.

Strategy: Tournament-select the best (strategy, params, symbol) on TRAIN, then
apply to TEST. Candidates:
  - momentum_breakout (Donchian)
  - ema_trend (with ATR trailing stop)
  - bb_reversion (Bollinger mean reversion)

Scoring prefers high return gated by Sharpe and low DD to reduce overfit.

Usage:
  run_backtest(start, end, initial_capital=1000.0) -> dict
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests

API = "https://api.binance.com/api/v3/klines"
FEE = 0.001
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]


def _ms(dt: str) -> int:
    return int(datetime.fromisoformat(dt).replace(tzinfo=timezone.utc).timestamp() * 1000)


def fetch_klines(symbol: str, start: str, end: str, interval: str = "1d") -> pd.DataFrame:
    out = []
    start_ms = _ms(start)
    end_ms = _ms(end)
    cur = start_ms
    while cur < end_ms:
        params = dict(symbol=symbol, interval=interval, startTime=cur, endTime=end_ms, limit=1000)
        for attempt in range(5):
            try:
                r = requests.get(API, params=params, timeout=15)
                r.raise_for_status()
                data = r.json()
                break
            except Exception:
                time.sleep(1 + attempt)
        else:
            raise RuntimeError(f"failed fetch {symbol}")
        if not data:
            break
        out.extend(data)
        last = data[-1][0]
        if last <= cur:
            break
        cur = last + 1
        if len(data) < 1000:
            break
        time.sleep(0.05)
    df = pd.DataFrame(
        out,
        columns=[
            "open_time", "open", "high", "low", "close", "volume",
            "close_time", "qav", "trades", "tbbav", "tbqav", "ignore",
        ],
    )
    if df.empty:
        return df
    df = df.astype({"open": float, "high": float, "low": float, "close": float, "volume": float})
    df["date"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    return df[["date", "open", "high", "low", "close", "volume"]].reset_index(drop=True)


# ---------- indicators ----------
def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    tr = pd.concat([(h - l), (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


# ---------- backtest core ----------
def _metrics(equity: list[float], trades: list[dict]) -> dict:
    eq = np.array(equity, dtype=float)
    if len(eq) < 2:
        return dict(final_equity=float(eq[-1]) if len(eq) else 0.0,
                    total_return_pct=0.0, sharpe_ratio=0.0, max_drawdown_pct=0.0,
                    num_trades=0, win_rate=0.0)
    rets = np.diff(eq) / eq[:-1]
    sharpe = float(np.mean(rets) / (np.std(rets) + 1e-12) * np.sqrt(365)) if rets.std() > 0 else 0.0
    peak = np.maximum.accumulate(eq)
    dd = (eq - peak) / peak
    max_dd = float(dd.min() * -100.0)
    wins = sum(1 for t in trades if t.get("pnl", 0) > 0)
    n = sum(1 for t in trades if "pnl" in t)
    wr = (wins / n * 100.0) if n else 0.0
    return dict(
        final_equity=float(eq[-1]),
        total_return_pct=float((eq[-1] / eq[0] - 1.0) * 100.0),
        sharpe_ratio=sharpe,
        max_drawdown_pct=max_dd,
        num_trades=int(n),
        win_rate=float(wr),
    )


def _simulate(df: pd.DataFrame, signals: list[int], initial: float) -> tuple[list[float], list[dict]]:
    """signals[i] in {-1,0,1} is desired position AT end of bar i (filled next open)."""
    cash = initial
    pos_qty = 0.0
    entry = 0.0
    equity = [initial]
    trades: list[dict] = []
    n = len(df)
    for i in range(1, n):
        price = df["close"].iloc[i]
        # mark-to-market
        mtm = cash + pos_qty * price
        equity.append(mtm)
        # act on signal from i-1, fill at open[i]... use close[i] as approx
        sig = signals[i - 1]
        want_long = sig == 1
        if pos_qty == 0 and want_long:
            alloc = cash * 0.98
            qty = alloc / price
            fee = alloc * FEE
            cash -= (alloc + fee)
            pos_qty = qty
            entry = price
            trades.append({"date": str(df["date"].iloc[i].date()), "action": "BUY", "price": float(price)})
        elif pos_qty > 0 and not want_long:
            proceeds = pos_qty * price
            fee = proceeds * FEE
            cash += (proceeds - fee)
            pnl = (price - entry) * pos_qty - fee
            trades.append({"date": str(df["date"].iloc[i].date()), "action": "SELL",
                           "price": float(price), "pnl": float(pnl)})
            pos_qty = 0.0
    # close at end
    if pos_qty > 0:
        price = df["close"].iloc[-1]
        proceeds = pos_qty * price
        fee = proceeds * FEE
        cash += (proceeds - fee)
        pnl = (price - entry) * pos_qty - fee
        trades.append({"date": str(df["date"].iloc[-1].date()), "action": "SELL_EOD",
                       "price": float(price), "pnl": float(pnl)})
        equity[-1] = cash
        pos_qty = 0.0
    return equity, trades


# ---------- signal generators ----------
def sig_breakout(df: pd.DataFrame, lookback: int, exit_period: int) -> list[int]:
    hi = df["high"].rolling(lookback).max().shift(1)
    lo = df["low"].rolling(exit_period).min().shift(1)
    pos = 0
    sigs = []
    for i in range(len(df)):
        c = df["close"].iloc[i]
        if pos == 0 and not np.isnan(hi.iloc[i]) and c > hi.iloc[i]:
            pos = 1
        elif pos == 1 and not np.isnan(lo.iloc[i]) and c < lo.iloc[i]:
            pos = 0
        sigs.append(pos)
    return sigs


def sig_ema_trend(df: pd.DataFrame, fast: int, slow: int, atr_mult: float) -> list[int]:
    ef = ema(df["close"], fast)
    es = ema(df["close"], slow)
    a = atr(df, 14)
    pos = 0
    trail = 0.0
    highest = 0.0
    sigs = []
    for i in range(len(df)):
        c = df["close"].iloc[i]
        if pos == 0:
            if i > slow and ef.iloc[i] > es.iloc[i] and ef.iloc[i - 1] <= es.iloc[i - 1]:
                pos = 1
                highest = c
                trail = c - atr_mult * a.iloc[i]
        else:
            highest = max(highest, c)
            trail = max(trail, highest - atr_mult * a.iloc[i])
            if c < trail or ef.iloc[i] < es.iloc[i]:
                pos = 0
        sigs.append(pos)
    return sigs


def sig_bb_reversion(df: pd.DataFrame, period: int, nstd: float, stop_pct: float) -> list[int]:
    m = df["close"].rolling(period).mean()
    sd = df["close"].rolling(period).std()
    upper = m + nstd * sd
    lower = m - nstd * sd
    pos = 0
    entry = 0.0
    sigs = []
    for i in range(len(df)):
        c = df["close"].iloc[i]
        if pos == 0:
            if not np.isnan(lower.iloc[i]) and c < lower.iloc[i]:
                pos = 1
                entry = c
        else:
            if c >= m.iloc[i] or c < entry * (1 - stop_pct):
                pos = 0
        sigs.append(pos)
    return sigs


# ---------- tournament ----------
def _candidates():
    cands = []
    for lb in (15, 20, 25, 30, 40):
        for ex in (5, 10, 15):
            cands.append(("breakout", dict(lookback=lb, exit_period=ex)))
    for f, s in ((5, 13), (8, 21), (10, 30), (12, 26), (20, 50)):
        for m in (2.0, 2.5, 3.0):
            cands.append(("ema_trend", dict(fast=f, slow=s, atr_mult=m)))
    for p in (15, 20, 25):
        for ns in (1.5, 2.0, 2.5):
            for sp in (0.03, 0.05):
                cands.append(("bb_reversion", dict(period=p, nstd=ns, stop_pct=sp)))
    return cands


def _gen(name: str, df: pd.DataFrame, params: dict) -> list[int]:
    if name == "breakout":
        return sig_breakout(df, **params)
    if name == "ema_trend":
        return sig_ema_trend(df, **params)
    if name == "bb_reversion":
        return sig_bb_reversion(df, **params)
    raise ValueError(name)


def _score(m: dict) -> float:
    # prefer return, penalize DD, require decent sharpe
    if m["num_trades"] == 0:
        return -1e9
    return m["total_return_pct"] - 0.5 * m["max_drawdown_pct"] + 5.0 * max(0.0, m["sharpe_ratio"])


def tournament(train_data: dict[str, pd.DataFrame], initial: float) -> tuple[str, dict, str, dict]:
    best = None
    for sym, df in train_data.items():
        if len(df) < 60:
            continue
        for name, params in _candidates():
            sigs = _gen(name, df, params)
            eq, tr = _simulate(df, sigs, initial)
            m = _metrics(eq, tr)
            s = _score(m)
            if best is None or s > best[0]:
                best = (s, name, params, sym, m)
    return best[1], best[2], best[3], best[4]


# ---------- public ----------
def run_backtest(start: str, end: str, initial_capital: float = 1000.0) -> dict:
    train_start, train_end = "2024-01-01", "2024-06-30"
    test_start, test_end = "2024-07-01", "2024-12-31"

    # Fetch all symbols for TRAIN window; pick best
    train_data = {s: fetch_klines(s, train_start, train_end) for s in SYMBOLS}
    name, params, sym, train_m = tournament(train_data, initial_capital)

    # Run on requested window with chosen (sym, name, params)
    df = fetch_klines(sym, start, end)
    sigs = _gen(name, df, params)
    eq, trades = _simulate(df, sigs, initial_capital)
    m = _metrics(eq, trades)
    m["_selected"] = {"strategy": name, "params": params, "symbol": sym}
    m["_train_metrics"] = train_m
    return m


def main():
    out = Path(__file__).parent
    # TRAIN eval (same as tournament selection window)
    train = run_backtest("2024-01-01", "2024-06-30", 1000.0)
    test = run_backtest("2024-07-01", "2024-12-31", 1000.0)
    lines = []
    lines.append("Agent 5 - Round 7 Results")
    lines.append("=" * 50)
    sel = train.get("_selected", {})
    lines.append(f"Selected: {sel}")
    for label, m in (("TRAIN (2024-01-01..2024-06-30)", train),
                     ("TEST  (2024-07-01..2024-12-31)", test)):
        lines.append("")
        lines.append(label)
        lines.append(f"  final_equity:    ${m['final_equity']:.2f}")
        lines.append(f"  total_return_pct:{m['total_return_pct']:.2f}")
        lines.append(f"  sharpe_ratio:    {m['sharpe_ratio']:.3f}")
        lines.append(f"  max_drawdown_pct:{m['max_drawdown_pct']:.2f}")
        lines.append(f"  num_trades:      {m['num_trades']}")
        lines.append(f"  win_rate:        {m['win_rate']:.1f}")
    txt = "\n".join(lines)
    (out / "results.txt").write_text(txt)
    print(txt)


if __name__ == "__main__":
    main()
