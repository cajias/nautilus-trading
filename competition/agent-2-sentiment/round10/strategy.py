"""
Agent 2 - Sentiment Trader - ROUND 10 (FINAL)

Approach: "Conviction Concentration v3 — Bold Final"
- 6-asset crypto universe (BTC, ETH, SOL, BNB, XRP, DOGE), 4h bars
- Tournament on TRAIN (2024) over momentum+panic sentiment breakout params
- Pick the single best-scoring asset (return - 0.2*dd) at best config
- Lock 100% capital onto that asset for TEST
- Signals: EMA trend + volume surge + Donchian breakout + taker-buy ratio
           spike (sentiment proxy), plus oversold panic reversal
- Pyramiding up to 3 units on winners, ATR trailing stop
- Self-contained: fetches Binance public klines, caches to /tmp

Standard interface:
    run_backtest(start, end, initial_capital) -> dict
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import requests

CACHE = Path("/tmp/a2_r10_klines")
CACHE.mkdir(parents=True, exist_ok=True)

UNIVERSE = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT"]
INTERVAL = "4h"
BAR_MS = 4 * 60 * 60 * 1000
FEE = 0.0004  # 4 bps per side


# ---------- Data ----------
def _fetch_klines(symbol: str, start_ms: int, end_ms: int) -> pd.DataFrame:
    key = CACHE / f"{symbol}_{INTERVAL}_{start_ms}_{end_ms}.parquet"
    if key.exists():
        return pd.read_parquet(key)
    url = "https://api.binance.com/api/v3/klines"
    rows: list[list] = []
    cur = start_ms
    while cur < end_ms:
        r = requests.get(
            url,
            params={
                "symbol": symbol,
                "interval": INTERVAL,
                "startTime": cur,
                "endTime": end_ms,
                "limit": 1000,
            },
            timeout=30,
        )
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        rows.extend(batch)
        cur = batch[-1][0] + BAR_MS
        if len(batch) < 1000:
            break
        time.sleep(0.12)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(
        rows,
        columns=[
            "open_time", "open", "high", "low", "close", "volume",
            "close_time", "quote_vol", "trades", "taker_buy_base",
            "taker_buy_quote", "ignore",
        ],
    )
    for c in ["open", "high", "low", "close", "volume", "taker_buy_base"]:
        df[c] = df[c].astype(float)
    df["time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df = df[["time", "open", "high", "low", "close", "volume", "taker_buy_base"]]
    df.to_parquet(key)
    return df


def _load(symbol: str, start: str, end: str) -> pd.DataFrame:
    s = int(datetime.fromisoformat(start).replace(tzinfo=timezone.utc).timestamp() * 1000)
    e = int(datetime.fromisoformat(end).replace(tzinfo=timezone.utc).timestamp() * 1000)
    # Pad start for indicator warmup
    s_pad = s - 400 * BAR_MS
    df = _fetch_klines(symbol, s_pad, e)
    if df.empty:
        return df
    df = df.set_index("time").sort_index()
    return df


# ---------- Indicators ----------
def _ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def _rsi(s: pd.Series, n: int = 14) -> pd.Series:
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50)


def _atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    tr = pd.concat(
        [(h - l), (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1
    ).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


# ---------- Backtest core ----------
@dataclass
class Params:
    ema_fast: int = 30
    ema_slow: int = 100
    donch: int = 20
    vol_mult: float = 1.4
    tbr_z: float = 0.5
    rsi_panic: float = 28.0
    panic_drop: float = 0.04
    atr_init: float = 3.0
    atr_trail: float = 4.0
    pyramid_max: int = 3
    pyramid_gap_atr: float = 1.5


def _simulate(df: pd.DataFrame, p: Params, start: str, end: str,
              initial_capital: float) -> dict[str, Any]:
    d = df.copy()
    d["ema_f"] = _ema(d["close"], p.ema_fast)
    d["ema_s"] = _ema(d["close"], p.ema_slow)
    d["rsi"] = _rsi(d["close"], 14)
    d["atr"] = _atr(d, 14)
    d["vol_ma"] = d["volume"].rolling(20).mean()
    d["donch_hi"] = d["high"].rolling(p.donch).max().shift(1)
    d["ret"] = d["close"].pct_change()
    tbr = d["taker_buy_base"] / d["volume"].replace(0, np.nan)
    d["tbr_z"] = (tbr - tbr.rolling(50).mean()) / tbr.rolling(50).std()

    s_ts = pd.Timestamp(start, tz="UTC")
    e_ts = pd.Timestamp(end, tz="UTC")
    d = d.dropna()
    mask = (d.index >= s_ts) & (d.index <= e_ts)
    idx = d.index[mask]
    if len(idx) < 20:
        return {"final_equity": initial_capital, "total_return_pct": 0,
                "sharpe_ratio": 0, "max_drawdown_pct": 0,
                "num_trades": 0, "win_rate": 0, "equity_curve": []}

    cash = initial_capital
    units: list[dict] = []  # each: qty, entry, stop
    equity_curve = []
    trades = []

    def total_qty():
        return sum(u["qty"] for u in units)

    for t in idx:
        row = d.loc[t]
        price = float(row["close"])
        atr = float(row["atr"])

        # Update trailing stops
        for u in units:
            new_stop = price - p.atr_trail * atr
            if new_stop > u["stop"]:
                u["stop"] = new_stop

        # Stop out ALL if price hits worst stop (use min stop)
        if units:
            worst = max(u["stop"] for u in units)  # highest stop = tightest
            low = float(row["low"])
            if low <= max(u["stop"] for u in units):
                # exit units whose stop was hit
                remaining = []
                for u in units:
                    if low <= u["stop"]:
                        exit_p = u["stop"] * (1 - FEE)
                        cash += u["qty"] * exit_p
                        trades.append({
                            "entry": u["entry"], "exit": exit_p,
                            "pnl_pct": (exit_p - u["entry"]) / u["entry"] * 100,
                        })
                    else:
                        remaining.append(u)
                units = remaining

        # Entry signals
        trend_up = row["ema_f"] > row["ema_s"] and price > row["ema_s"]
        vol_surge = row["volume"] > p.vol_mult * row["vol_ma"]
        breakout = price > row["donch_hi"]
        tbr_hot = row["tbr_z"] > p.tbr_z
        momentum_sig = trend_up and vol_surge and breakout and tbr_hot

        panic_sig = (
            row["rsi"] < p.rsi_panic
            and row["ret"] < -p.panic_drop
            and vol_surge
            and price > 0.9 * row["ema_s"]
        )

        signal = momentum_sig or panic_sig

        if signal and len(units) < p.pyramid_max:
            # Pyramid: require price above last entry by gap*atr
            can_enter = True
            if units:
                last = units[-1]
                if price < last["entry"] + p.pyramid_gap_atr * atr:
                    can_enter = False
            if can_enter and cash > 20:
                # size: 50% of equity for first, 25% for next, 25% for third
                eq = cash + total_qty() * price
                alloc_pct = [0.6, 0.25, 0.15][len(units)]
                alloc = eq * alloc_pct
                alloc = min(alloc, cash * 0.99)
                qty = alloc / (price * (1 + FEE))
                if qty * price > 10:
                    cash -= qty * price * (1 + FEE)
                    units.append({
                        "qty": qty,
                        "entry": price,
                        "stop": price - p.atr_init * atr,
                    })

        equity = cash + total_qty() * price
        equity_curve.append((t, equity))

    # Close any open at end
    if units:
        last_price = float(d.loc[idx[-1], "close"])
        for u in units:
            exit_p = last_price * (1 - FEE)
            cash += u["qty"] * exit_p
            trades.append({
                "entry": u["entry"], "exit": exit_p,
                "pnl_pct": (exit_p - u["entry"]) / u["entry"] * 100,
            })
        units = []

    final_eq = cash
    eq_ser = pd.Series([e for _, e in equity_curve])
    rets = eq_ser.pct_change().dropna()
    sharpe = float(rets.mean() / rets.std() * np.sqrt(365 * 6)) if rets.std() > 0 else 0.0
    roll_max = eq_ser.cummax()
    dd = float(((eq_ser - roll_max) / roll_max).min() * 100) if len(eq_ser) else 0.0
    wr = float(np.mean([1 if t["pnl_pct"] > 0 else 0 for t in trades]) * 100) if trades else 0.0
    return {
        "final_equity": float(final_eq),
        "total_return_pct": float((final_eq / initial_capital - 1) * 100),
        "sharpe_ratio": sharpe,
        "max_drawdown_pct": dd,
        "num_trades": len(trades),
        "win_rate": wr,
        "equity_curve": [(str(t), float(e)) for t, e in equity_curve],
    }


# ---------- Tournament ----------
def _tournament(train_start: str, train_end: str, capital: float) -> tuple[str, Params]:
    grid = []
    for ef, es in [(20, 80), (30, 100), (40, 150)]:
        for dn in [15, 20, 30]:
            for vm in [1.3, 1.6]:
                for tb in [0.3, 0.6]:
                    grid.append(Params(
                        ema_fast=ef, ema_slow=es, donch=dn,
                        vol_mult=vm, tbr_z=tb,
                    ))
    best = None
    best_score = -1e9
    best_sym = None
    for sym in UNIVERSE:
        try:
            df = _load(sym, train_start, train_end)
        except Exception as e:
            print(f"[load fail] {sym}: {e}")
            continue
        if df.empty:
            continue
        for p in grid:
            try:
                r = _simulate(df, p, train_start, train_end, capital)
            except Exception:
                continue
            score = r["total_return_pct"] - 0.2 * abs(r["max_drawdown_pct"])
            if r["num_trades"] < 3:
                score -= 20
            if score > best_score:
                best_score = score
                best = p
                best_sym = sym
    if best is None:
        return "BTCUSDT", Params()
    print(f"[tournament] selected {best_sym} score={best_score:.2f} params={best}")
    return best_sym, best


# ---------- Public API ----------
def run_backtest(start: str, end: str, initial_capital: float = 1000.0) -> dict:
    train_start = "2024-01-01"
    train_end = "2024-12-31"
    sym, params = _tournament(train_start, train_end, initial_capital)
    df = _load(sym, start, end)
    if df.empty:
        return {"final_equity": initial_capital, "total_return_pct": 0,
                "sharpe_ratio": 0, "max_drawdown_pct": 0,
                "num_trades": 0, "win_rate": 0, "selected_symbol": sym}
    result = _simulate(df, params, start, end, initial_capital)
    result["selected_symbol"] = sym
    result["params"] = params.__dict__
    return result


if __name__ == "__main__":
    cap = 1000.0
    print("=== TRAIN 2024 ===")
    tr = run_backtest("2024-01-01", "2024-12-31", cap)
    print(json.dumps({k: v for k, v in tr.items() if k != "equity_curve"}, indent=2, default=str))
    print("=== TEST 2025 H1 ===")
    te = run_backtest("2025-01-01", "2025-06-30", cap)
    print(json.dumps({k: v for k, v in te.items() if k != "equity_curve"}, indent=2, default=str))

    out = {
        "agent": "Agent 2 - Sentiment Trader",
        "round": 10,
        "approach": (
            "Conviction concentration v3 FINAL: tournament momentum+panic "
            "sentiment breakout across 6-asset crypto universe on TRAIN 2024, "
            "lock 100% capital on best asset/params for TEST 2025 H1. "
            "Pyramiding up to 3 units, ATR trailing stops."
        ),
        "train_period": ["2024-01-01", "2024-12-31"],
        "test_period": ["2025-01-01", "2025-06-30"],
        "train": {k: v for k, v in tr.items() if k != "equity_curve"},
        "test": {k: v for k, v in te.items() if k != "equity_curve"},
    }
    Path(__file__).parent.joinpath("results.txt").write_text(
        json.dumps(out, indent=2, default=str)
    )
    print("wrote results.txt")
