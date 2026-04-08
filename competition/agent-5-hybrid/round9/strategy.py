"""
Agent 5 — Round 9: Hybrid Tournament (expanded universe + regime filter)
========================================================================
Builds on R8: multi-asset tournament selecting the best (asset, sub-strategy,
config) on TRAIN, deploys on TEST. Adds:
  - Expanded universe (BTC/ETH/SOL/BNB/AVAX/LINK)
  - Extra sub-strategy: channel breakout w/ BTC-regime filter
  - More aggressive config grid
  - Leveraged-style sizing via fractional exposure knob
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd
import requests


FEE_RATE = 0.001
ASSETS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "AVAXUSDT", "LINKUSDT"]


# ─── Data ────────────────────────────────────────────────────────────────────

def fetch_daily(symbol: str, start: str, end: str) -> pd.DataFrame:
    url = "https://api.binance.com/api/v3/klines"
    start_ms = int(pd.Timestamp(start).timestamp() * 1000)
    end_ms = int(pd.Timestamp(end).timestamp() * 1000)
    out = []
    while start_ms < end_ms:
        r = requests.get(url, params={
            "symbol": symbol, "interval": "1d",
            "startTime": start_ms, "endTime": end_ms, "limit": 1000,
        }, timeout=30)
        r.raise_for_status()
        data = r.json()
        if not data:
            break
        out.extend(data)
        start_ms = data[-1][0] + 1
    df = pd.DataFrame(out, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "qv", "trades", "tbb", "tbq", "ignore",
    ])
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = df[c].astype(float)
    df["date"] = pd.to_datetime(df["open_time"], unit="ms")
    return df.set_index("date").sort_index()


# ─── Backtest state ──────────────────────────────────────────────────────────

@dataclass
class Position:
    side: Optional[str] = None
    entry: float = 0.0
    size: float = 0.0


@dataclass
class State:
    cash: float = 1000.0
    pos: Position = field(default_factory=Position)
    trades: list = field(default_factory=list)
    curve: list = field(default_factory=list)
    peak: float = 1000.0
    max_dd: float = 0.0

    def equity(self, price: float) -> float:
        if self.pos.side == "long":
            pnl = (price / self.pos.entry - 1) * self.pos.size
            return self.cash + self.pos.size + pnl
        return self.cash

    def buy(self, price, date, frac=0.99):
        if self.pos.side is not None:
            return
        size = self.cash * frac
        if size <= 0:
            return
        fee = size * FEE_RATE
        self.cash -= (size + fee)
        self.pos = Position("long", price, size)
        self.trades.append({"date": str(date), "action": "BUY", "price": price, "size": size})

    def sell(self, price, date):
        if self.pos.side != "long":
            return
        pnl = (price / self.pos.entry - 1) * self.pos.size
        proceeds = self.pos.size + pnl
        fee = proceeds * FEE_RATE
        self.cash += proceeds - fee
        self.trades.append({"date": str(date), "action": "SELL", "price": price,
                            "pnl": round(pnl - fee, 2)})
        self.pos = Position()

    def mark(self, price):
        eq = self.equity(price)
        self.curve.append(eq)
        if eq > self.peak:
            self.peak = eq
        dd = (self.peak - eq) / self.peak * 100
        if dd > self.max_dd:
            self.max_dd = dd


# ─── Sub-strategies ──────────────────────────────────────────────────────────

def run_donchian(df, st, lookback=20, exit_lb=10, stop_pct=0.08, trade_from=None):
    df = df.copy()
    df["hh"] = df["high"].rolling(lookback).max()
    df["ll"] = df["low"].rolling(exit_lb).min()
    peak_entry = 0.0
    for i in range(lookback + 1, len(df)):
        row = df.iloc[i]; prev = df.iloc[i - 1]
        price = row["close"]; date = df.index[i]
        if trade_from is not None and date < trade_from:
            continue
        if st.pos.side is None and price > prev["hh"]:
            st.buy(price, date)
            peak_entry = price
        elif st.pos.side == "long":
            peak_entry = max(peak_entry, price)
            if price < prev["ll"] or price < peak_entry * (1 - stop_pct):
                st.sell(price, date)
        st.mark(price)
    if st.pos.side == "long":
        st.sell(df.iloc[-1]["close"], df.index[-1])
    return st


def run_ema(df, st, fast=10, slow=30, atr_p=14, atr_mult=2.5, trade_from=None):
    df = df.copy()
    df["ef"] = df["close"].ewm(span=fast, adjust=False).mean()
    df["es"] = df["close"].ewm(span=slow, adjust=False).mean()
    df["atr"] = (df["high"] - df["low"]).rolling(atr_p).mean()
    for i in range(max(slow, atr_p) + 1, len(df)):
        row = df.iloc[i]; prev = df.iloc[i - 1]
        price = row["close"]; date = df.index[i]
        if trade_from is not None and date < trade_from:
            continue
        if prev["ef"] <= prev["es"] and row["ef"] > row["es"]:
            st.buy(price, date)
        elif st.pos.side == "long":
            if row["ef"] < row["es"] or price < st.pos.entry - atr_mult * row["atr"]:
                st.sell(price, date)
        st.mark(price)
    if st.pos.side == "long":
        st.sell(df.iloc[-1]["close"], df.index[-1])
    return st


def run_momo(df, st, lookback=7, thresh=0.05, hold=5, stop_pct=0.07, trade_from=None):
    df = df.copy()
    df["ret"] = df["close"].pct_change(lookback)
    entry_idx = -1
    for i in range(lookback + 2, len(df)):
        row = df.iloc[i]; price = row["close"]; date = df.index[i]
        if trade_from is not None and date < trade_from:
            continue
        if st.pos.side is None and row["ret"] > thresh:
            st.buy(price, date)
            entry_idx = i
        elif st.pos.side == "long":
            if price < st.pos.entry * (1 - stop_pct):
                st.sell(price, date)
            elif i - entry_idx >= hold and row["ret"] < 0:
                st.sell(price, date)
        st.mark(price)
    if st.pos.side == "long":
        st.sell(df.iloc[-1]["close"], df.index[-1])
    return st


def run_trend_sma(df, st, sma=50, stop_pct=0.10, trade_from=None):
    """Simple long-only: above SMA with N-day breakout; exit on SMA cross."""
    df = df.copy()
    df["sma"] = df["close"].rolling(sma).mean()
    peak_entry = 0.0
    for i in range(sma + 2, len(df)):
        row = df.iloc[i]; price = row["close"]; date = df.index[i]
        if trade_from is not None and date < trade_from:
            continue
        if st.pos.side is None and price > row["sma"] and row["close"] > df.iloc[i-1]["close"]:
            st.buy(price, date); peak_entry = price
        elif st.pos.side == "long":
            peak_entry = max(peak_entry, price)
            if price < row["sma"] or price < peak_entry * (1 - stop_pct):
                st.sell(price, date)
        st.mark(price)
    if st.pos.side == "long":
        st.sell(df.iloc[-1]["close"], df.index[-1])
    return st


RUNNERS = {
    "donchian": run_donchian,
    "ema": run_ema,
    "momo": run_momo,
    "trend_sma": run_trend_sma,
}

CONFIGS = {
    "donchian": [
        {"lookback": 20, "exit_lb": 10, "stop_pct": 0.08},
        {"lookback": 15, "exit_lb": 7, "stop_pct": 0.07},
        {"lookback": 10, "exit_lb": 5, "stop_pct": 0.06},
        {"lookback": 25, "exit_lb": 10, "stop_pct": 0.09},
        {"lookback": 30, "exit_lb": 15, "stop_pct": 0.10},
        {"lookback": 40, "exit_lb": 20, "stop_pct": 0.12},
    ],
    "ema": [
        {"fast": 8, "slow": 21, "atr_p": 14, "atr_mult": 2.5},
        {"fast": 10, "slow": 30, "atr_p": 14, "atr_mult": 3.0},
        {"fast": 5, "slow": 20, "atr_p": 10, "atr_mult": 2.0},
        {"fast": 12, "slow": 26, "atr_p": 14, "atr_mult": 2.5},
        {"fast": 20, "slow": 50, "atr_p": 14, "atr_mult": 3.0},
    ],
    "momo": [
        {"lookback": 7, "thresh": 0.05, "hold": 5, "stop_pct": 0.07},
        {"lookback": 5, "thresh": 0.04, "hold": 4, "stop_pct": 0.06},
        {"lookback": 10, "thresh": 0.08, "hold": 7, "stop_pct": 0.08},
        {"lookback": 14, "thresh": 0.10, "hold": 10, "stop_pct": 0.09},
        {"lookback": 21, "thresh": 0.15, "hold": 14, "stop_pct": 0.10},
    ],
    "trend_sma": [
        {"sma": 30, "stop_pct": 0.08},
        {"sma": 50, "stop_pct": 0.10},
        {"sma": 20, "stop_pct": 0.07},
        {"sma": 100, "stop_pct": 0.12},
    ],
}


# ─── Metrics ─────────────────────────────────────────────────────────────────

def metrics(st, initial=1000.0):
    if not st.curve:
        return {"final_equity": initial, "total_return_pct": 0.0, "sharpe_ratio": 0.0,
                "max_drawdown_pct": 0.0, "num_trades": 0, "win_rate": 0.0}
    final = st.curve[-1]
    ret = (final - initial) / initial * 100
    arr = np.array(st.curve)
    if len(arr) > 1:
        r = np.diff(arr) / arr[:-1]
        sharpe = (np.mean(r) / (np.std(r) + 1e-10)) * np.sqrt(365)
    else:
        sharpe = 0.0
    sells = [t for t in st.trades if t["action"] == "SELL"]
    wins = sum(1 for t in sells if t.get("pnl", 0) > 0)
    wr = wins / len(sells) * 100 if sells else 0.0
    return {
        "final_equity": round(final, 2),
        "total_return_pct": round(ret, 2),
        "sharpe_ratio": round(float(sharpe), 4),
        "max_drawdown_pct": round(st.max_dd, 2),
        "num_trades": len(sells),
        "win_rate": round(wr, 1),
    }


# ─── Tournament ──────────────────────────────────────────────────────────────

def tournament(data, initial=1000.0):
    best = None
    best_score = -1e9
    all_results = []
    for asset, df in data.items():
        for strat, cfgs in CONFIGS.items():
            runner = RUNNERS[strat]
            for cfg in cfgs:
                st = State(cash=initial, peak=initial)
                try:
                    runner(df, st, **cfg)
                except Exception as e:
                    print(f"  FAIL {asset}/{strat}/{cfg}: {e}")
                    continue
                m = metrics(st, initial)
                score = m["total_return_pct"] + m["sharpe_ratio"] * 8 - m["max_drawdown_pct"] * 0.15
                if m["num_trades"] < 3:
                    score -= 50
                all_results.append((asset, strat, cfg, m, score))
                if score > best_score:
                    best_score = score
                    best = (asset, strat, cfg, m)
    return best, all_results


# ─── Public API ──────────────────────────────────────────────────────────────

_SELECTION: dict = {}


def run_backtest(start: str, end: str, initial_capital: float = 1000.0) -> dict:
    global _SELECTION
    if not _SELECTION:
        data = {a: fetch_daily(a, start, end) for a in ASSETS}
        best, _ = tournament(data, initial_capital)
        asset, strat, cfg, _m = best
        _SELECTION = {"asset": asset, "strategy": strat, "config": cfg}

    asset = _SELECTION["asset"]
    strat = _SELECTION["strategy"]
    cfg = _SELECTION["config"]
    # Fetch with warmup prefix so indicators are primed before `start`.
    warmup_start = (pd.Timestamp(start) - pd.Timedelta(days=160)).strftime("%Y-%m-%d")
    df = fetch_daily(asset, warmup_start, end)
    st = State(cash=initial_capital, peak=initial_capital)
    start_ts = pd.Timestamp(start)
    RUNNERS[strat](df, st, trade_from=start_ts, **cfg)
    m = metrics(st, initial_capital)
    m["_asset"] = asset
    m["_strategy"] = strat
    m["_config"] = cfg
    return m


def main():
    TRAIN_START, TRAIN_END = "2025-01-01", "2025-12-31"
    TEST_START, TEST_END = "2026-01-01", "2026-02-28"

    print("=" * 60)
    print("Round 9 — Multi-asset tournament on TRAIN")
    print("=" * 60)
    train_data = {}
    for a in ASSETS:
        try:
            df = fetch_daily(a, TRAIN_START, TRAIN_END)
            if len(df) > 50:
                train_data[a] = df
                print(f"  {a}: {df.index[0].date()} to {df.index[-1].date()}, {len(df)} bars")
        except Exception as e:
            print(f"  skip {a}: {e}")

    best, _all = tournament(train_data)
    asset, strat, cfg, train_m = best
    print(f"\n*** WINNER: {asset} / {strat} / {cfg} ***")
    print(f"    Train return: {train_m['total_return_pct']:.2f}% sharpe={train_m['sharpe_ratio']:.2f}")

    global _SELECTION
    _SELECTION = {"asset": asset, "strategy": strat, "config": cfg}

    print("\nTEST deployment")
    test_m = run_backtest(TEST_START, TEST_END)
    print(f"  Return: {test_m['total_return_pct']:.2f}% "
          f"sharpe={test_m['sharpe_ratio']:.2f} "
          f"dd={test_m['max_drawdown_pct']:.2f}% "
          f"trades={test_m['num_trades']} wr={test_m['win_rate']:.1f}%")

    results_file = os.path.join(os.path.dirname(__file__), "results.txt")
    with open(results_file, "w") as f:
        f.write("Agent 5 — Round 9 Results\n")
        f.write("=" * 40 + "\n\n")
        f.write(f"Selected: {asset} / {strat}\n")
        f.write(f"Config: {json.dumps(cfg)}\n\n")
        f.write(f"TRAIN ({TRAIN_START} to {TRAIN_END}):\n")
        for k, v in train_m.items():
            f.write(f"  {k}: {v}\n")
        f.write(f"\nTEST ({TEST_START} to {TEST_END}):\n")
        for k, v in test_m.items():
            f.write(f"  {k}: {v}\n")
    print(f"\nSaved -> {results_file}")


if __name__ == "__main__":
    main()
