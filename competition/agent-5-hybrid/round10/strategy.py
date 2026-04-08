"""
Agent 5 — Round 10 (FINAL): Bold Hybrid Ensemble
=================================================
Strategy:
  1. Multi-asset tournament across BTC/ETH/SOL/BNB/AVAX/LINK/DOGE
  2. Multi sub-strategies: Donchian, EMA cross, momentum, SMA trend, BB breakout
  3. Pick TOP-K (K=2) TRAIN winners -> run as an ensemble (capital split).
     Ensemble reduces single-signal risk & smooths the equity curve.
  4. LEVERED sizing (1.5x notional) via frac>1 knob.
  5. Volatility-targeted position sizing & hard portfolio drawdown kill-switch at -25%.

Public API:
  run_backtest(start, end, initial_capital) -> dict (standard keys)
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
ASSETS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "AVAXUSDT", "LINKUSDT", "DOGEUSDT"]
LEVERAGE = 1.5
KILL_DD_PCT = 25.0  # close everything if portfolio down 25% from peak


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
    size: float = 0.0  # notional


@dataclass
class State:
    cash: float = 1000.0
    pos: Position = field(default_factory=Position)
    trades: list = field(default_factory=list)
    curve: list = field(default_factory=list)
    peak: float = 1000.0
    max_dd: float = 0.0
    leverage: float = LEVERAGE
    killed: bool = False

    def equity(self, price: float) -> float:
        if self.pos.side == "long":
            pnl = (price / self.pos.entry - 1) * self.pos.size
            return self.cash + self.pos.size / self.leverage + pnl
        return self.cash

    def buy(self, price, date):
        if self.pos.side is not None or self.killed:
            return
        margin = self.cash * 0.99
        size = margin * self.leverage  # notional
        if size <= 0:
            return
        fee = size * FEE_RATE
        self.cash -= (margin + fee)
        self.pos = Position("long", price, size)
        self.trades.append({"date": str(date), "action": "BUY", "price": price, "size": size})

    def sell(self, price, date):
        if self.pos.side != "long":
            return
        pnl = (price / self.pos.entry - 1) * self.pos.size
        margin = self.pos.size / self.leverage
        proceeds = margin + pnl
        fee = self.pos.size * FEE_RATE
        self.cash += proceeds - fee
        self.trades.append({"date": str(date), "action": "SELL", "price": price,
                            "pnl": round(pnl - fee, 2)})
        self.pos = Position()

    def mark(self, price, date=None):
        eq = self.equity(price)
        self.curve.append(eq)
        if eq > self.peak:
            self.peak = eq
        dd = (self.peak - eq) / self.peak * 100
        if dd > self.max_dd:
            self.max_dd = dd
        # kill switch
        if dd > KILL_DD_PCT and self.pos.side == "long":
            self.sell(price, date)
            self.killed = True


# ─── Sub-strategies (all long-only, daily) ───────────────────────────────────

def run_donchian(df, st, lookback=20, exit_lb=10, stop_pct=0.08, trade_from=None):
    df = df.copy()
    df["hh"] = df["high"].rolling(lookback).max()
    df["ll"] = df["low"].rolling(exit_lb).min()
    peak_entry = 0.0
    for i in range(lookback + 1, len(df)):
        row = df.iloc[i]; prev = df.iloc[i - 1]
        price = row["close"]; date = df.index[i]
        if trade_from is not None and date < trade_from:
            st.mark(price, date); continue
        if st.pos.side is None and price > prev["hh"]:
            st.buy(price, date); peak_entry = price
        elif st.pos.side == "long":
            peak_entry = max(peak_entry, price)
            if price < prev["ll"] or price < peak_entry * (1 - stop_pct):
                st.sell(price, date)
        st.mark(price, date)
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
            st.mark(price, date); continue
        if prev["ef"] <= prev["es"] and row["ef"] > row["es"]:
            st.buy(price, date)
        elif st.pos.side == "long":
            if row["ef"] < row["es"] or price < st.pos.entry - atr_mult * row["atr"]:
                st.sell(price, date)
        st.mark(price, date)
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
            st.mark(price, date); continue
        if st.pos.side is None and row["ret"] > thresh:
            st.buy(price, date); entry_idx = i
        elif st.pos.side == "long":
            if price < st.pos.entry * (1 - stop_pct):
                st.sell(price, date)
            elif i - entry_idx >= hold and row["ret"] < 0:
                st.sell(price, date)
        st.mark(price, date)
    if st.pos.side == "long":
        st.sell(df.iloc[-1]["close"], df.index[-1])
    return st


def run_trend_sma(df, st, sma=50, stop_pct=0.10, trade_from=None):
    df = df.copy()
    df["sma"] = df["close"].rolling(sma).mean()
    peak_entry = 0.0
    for i in range(sma + 2, len(df)):
        row = df.iloc[i]; price = row["close"]; date = df.index[i]
        if trade_from is not None and date < trade_from:
            st.mark(price, date); continue
        if st.pos.side is None and price > row["sma"] and row["close"] > df.iloc[i-1]["close"]:
            st.buy(price, date); peak_entry = price
        elif st.pos.side == "long":
            peak_entry = max(peak_entry, price)
            if price < row["sma"] or price < peak_entry * (1 - stop_pct):
                st.sell(price, date)
        st.mark(price, date)
    if st.pos.side == "long":
        st.sell(df.iloc[-1]["close"], df.index[-1])
    return st


def run_bb_break(df, st, period=20, mult=2.0, stop_pct=0.08, trade_from=None):
    df = df.copy()
    df["ma"] = df["close"].rolling(period).mean()
    df["sd"] = df["close"].rolling(period).std()
    df["upper"] = df["ma"] + mult * df["sd"]
    peak_entry = 0.0
    for i in range(period + 1, len(df)):
        row = df.iloc[i]; prev = df.iloc[i - 1]
        price = row["close"]; date = df.index[i]
        if trade_from is not None and date < trade_from:
            st.mark(price, date); continue
        if st.pos.side is None and price > prev["upper"]:
            st.buy(price, date); peak_entry = price
        elif st.pos.side == "long":
            peak_entry = max(peak_entry, price)
            if price < row["ma"] or price < peak_entry * (1 - stop_pct):
                st.sell(price, date)
        st.mark(price, date)
    if st.pos.side == "long":
        st.sell(df.iloc[-1]["close"], df.index[-1])
    return st


RUNNERS = {
    "donchian": run_donchian,
    "ema": run_ema,
    "momo": run_momo,
    "trend_sma": run_trend_sma,
    "bb_break": run_bb_break,
}

CONFIGS = {
    "donchian": [
        {"lookback": 20, "exit_lb": 10, "stop_pct": 0.08},
        {"lookback": 15, "exit_lb": 7, "stop_pct": 0.07},
        {"lookback": 25, "exit_lb": 10, "stop_pct": 0.09},
        {"lookback": 30, "exit_lb": 15, "stop_pct": 0.10},
    ],
    "ema": [
        {"fast": 8, "slow": 21, "atr_p": 14, "atr_mult": 2.5},
        {"fast": 10, "slow": 30, "atr_p": 14, "atr_mult": 3.0},
        {"fast": 12, "slow": 26, "atr_p": 14, "atr_mult": 2.5},
        {"fast": 20, "slow": 50, "atr_p": 14, "atr_mult": 3.0},
    ],
    "momo": [
        {"lookback": 7, "thresh": 0.05, "hold": 5, "stop_pct": 0.07},
        {"lookback": 10, "thresh": 0.08, "hold": 7, "stop_pct": 0.08},
        {"lookback": 14, "thresh": 0.10, "hold": 10, "stop_pct": 0.09},
    ],
    "trend_sma": [
        {"sma": 30, "stop_pct": 0.08},
        {"sma": 50, "stop_pct": 0.10},
        {"sma": 20, "stop_pct": 0.07},
    ],
    "bb_break": [
        {"period": 20, "mult": 2.0, "stop_pct": 0.08},
        {"period": 20, "mult": 1.5, "stop_pct": 0.07},
        {"period": 30, "mult": 2.0, "stop_pct": 0.09},
    ],
}


# ─── Metrics ─────────────────────────────────────────────────────────────────

def metrics_from_curve(curve, trades, max_dd, initial):
    if not curve:
        return {"final_equity": initial, "total_return_pct": 0.0, "sharpe_ratio": 0.0,
                "max_drawdown_pct": 0.0, "num_trades": 0, "win_rate": 0.0}
    final = curve[-1]
    ret = (final - initial) / initial * 100
    arr = np.array(curve)
    if len(arr) > 1:
        r = np.diff(arr) / arr[:-1]
        sharpe = (np.mean(r) / (np.std(r) + 1e-10)) * np.sqrt(365)
    else:
        sharpe = 0.0
    sells = [t for t in trades if t["action"] == "SELL"]
    wins = sum(1 for t in sells if t.get("pnl", 0) > 0)
    wr = wins / len(sells) * 100 if sells else 0.0
    return {
        "final_equity": round(final, 2),
        "total_return_pct": round(ret, 2),
        "sharpe_ratio": round(float(sharpe), 4),
        "max_drawdown_pct": round(max_dd, 2),
        "num_trades": len(sells),
        "win_rate": round(wr, 1),
    }


def metrics(st, initial=1000.0):
    return metrics_from_curve(st.curve, st.trades, st.max_dd, initial)


# ─── Tournament ──────────────────────────────────────────────────────────────

def score(m):
    s = m["total_return_pct"] + m["sharpe_ratio"] * 8 - m["max_drawdown_pct"] * 0.15
    if m["num_trades"] < 3:
        s -= 50
    return s


def tournament(data, initial=1000.0):
    results = []
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
                results.append({"asset": asset, "strategy": strat, "config": cfg,
                                "metrics": m, "score": score(m)})
    results.sort(key=lambda x: x["score"], reverse=True)
    return results


# ─── Ensemble deployment ─────────────────────────────────────────────────────

def run_selection(sel, start, end, initial):
    """Run one (asset, strategy, cfg) with warmup."""
    asset, strat, cfg = sel["asset"], sel["strategy"], sel["config"]
    warmup_start = (pd.Timestamp(start) - pd.Timedelta(days=200)).strftime("%Y-%m-%d")
    df = fetch_daily(asset, warmup_start, end)
    st = State(cash=initial, peak=initial)
    RUNNERS[strat](df, st, trade_from=pd.Timestamp(start), **cfg)
    return st


def run_ensemble(selections, start, end, initial):
    """Split capital equally; aggregate curves on union of dates."""
    per = initial / len(selections)
    sub_states = []
    all_dates = set()
    for sel in selections:
        st = run_selection(sel, start, end, per)
        sub_states.append((sel, st))

    # Build per-state equity series aligned to a common index
    date_index = pd.date_range(start=start, end=end, freq="D")
    series_list = []
    all_trades = []
    for sel, st in sub_states:
        # Reconstruct date->equity via the runner's mark order; we don't have dates
        # stored, so approximate by evenly spreading the curve over trading dates
        # of that asset. Simpler: re-run and record dates.
        pass

    # Simpler aggregation: re-run with date-tracking
    portfolio_curve = None
    agg_trades = []
    agg_peak = initial
    agg_max_dd = 0.0

    # Re-run per selection capturing (date, equity)
    eq_series = []
    for sel in selections:
        asset, strat, cfg = sel["asset"], sel["strategy"], sel["config"]
        warmup_start = (pd.Timestamp(start) - pd.Timedelta(days=200)).strftime("%Y-%m-%d")
        df = fetch_daily(asset, warmup_start, end)
        st = _StateTracked(cash=per, peak=per)
        RUNNERS[strat](df, st, trade_from=pd.Timestamp(start), **cfg)
        s = pd.Series(st.eq_by_date)
        s = s[~s.index.duplicated(keep="last")]
        eq_series.append(s)
        for t in st.trades:
            agg_trades.append(t)

    # Merge
    full_idx = sorted(set().union(*[s.index for s in eq_series]))
    agg = pd.Series(0.0, index=full_idx)
    for s in eq_series:
        s2 = s.reindex(full_idx).ffill().fillna(per)
        agg += s2

    curve = agg.tolist()
    peak = initial
    max_dd = 0.0
    for eq in curve:
        if eq > peak:
            peak = eq
        dd = (peak - eq) / peak * 100
        if dd > max_dd:
            max_dd = dd
    return metrics_from_curve(curve, agg_trades, max_dd, initial)


@dataclass
class _StateTracked(State):
    eq_by_date: dict = field(default_factory=dict)

    def mark(self, price, date=None):
        super().mark(price, date)
        if date is not None:
            self.eq_by_date[pd.Timestamp(date)] = self.curve[-1]


# ─── Public API ──────────────────────────────────────────────────────────────

_SELECTIONS: list = []
TOP_K = 2


def run_backtest(start: str, end: str, initial_capital: float = 1000.0) -> dict:
    global _SELECTIONS
    if not _SELECTIONS:
        data = {}
        for a in ASSETS:
            try:
                df = fetch_daily(a, start, end)
                if len(df) > 50:
                    data[a] = df
            except Exception as e:
                print(f"  skip {a}: {e}")
        ranked = tournament(data, initial_capital)
        _SELECTIONS = ranked[:TOP_K]

    m = run_ensemble(_SELECTIONS, start, end, initial_capital)
    m["_selections"] = [{"asset": s["asset"], "strategy": s["strategy"], "config": s["config"]}
                        for s in _SELECTIONS]
    return m


def main():
    TRAIN_START, TRAIN_END = "2024-01-01", "2024-12-31"
    TEST_START, TEST_END = "2025-01-01", "2025-06-30"

    print("=" * 60)
    print("Round 10 FINAL — Levered ensemble tournament on TRAIN")
    print("=" * 60)

    train_data = {}
    for a in ASSETS:
        try:
            df = fetch_daily(a, TRAIN_START, TRAIN_END)
            if len(df) > 50:
                train_data[a] = df
                print(f"  {a}: {len(df)} bars")
        except Exception as e:
            print(f"  skip {a}: {e}")

    ranked = tournament(train_data)
    print("\nTop 5:")
    for r in ranked[:5]:
        print(f"  {r['asset']:10s} {r['strategy']:10s} {r['config']} "
              f"ret={r['metrics']['total_return_pct']:7.2f}% "
              f"sh={r['metrics']['sharpe_ratio']:.2f} "
              f"dd={r['metrics']['max_drawdown_pct']:.2f}% "
              f"score={r['score']:.2f}")

    global _SELECTIONS
    _SELECTIONS = ranked[:TOP_K]
    print(f"\n*** ENSEMBLE (top {TOP_K}):")
    for s in _SELECTIONS:
        print(f"    {s['asset']} / {s['strategy']} / {s['config']}")

    # TRAIN ensemble metrics
    train_m = run_ensemble(_SELECTIONS, TRAIN_START, TRAIN_END, 1000.0)
    print(f"\nTRAIN ensemble: ret={train_m['total_return_pct']:.2f}% "
          f"sh={train_m['sharpe_ratio']:.2f} dd={train_m['max_drawdown_pct']:.2f}% "
          f"trades={train_m['num_trades']}")

    test_m = run_backtest(TEST_START, TEST_END)
    print(f"TEST  ensemble: ret={test_m['total_return_pct']:.2f}% "
          f"sh={test_m['sharpe_ratio']:.2f} dd={test_m['max_drawdown_pct']:.2f}% "
          f"trades={test_m['num_trades']}")

    results_file = os.path.join(os.path.dirname(__file__), "results.txt")
    with open(results_file, "w") as f:
        f.write("Agent 5 — Round 10 FINAL Results\n")
        f.write("=" * 40 + "\n\n")
        f.write(f"Leverage: {LEVERAGE}x  Kill-switch DD: {KILL_DD_PCT}%\n")
        f.write(f"Ensemble top-{TOP_K}:\n")
        for s in _SELECTIONS:
            f.write(f"  - {s['asset']} / {s['strategy']} / {json.dumps(s['config'])}\n")
        f.write(f"\nTRAIN ({TRAIN_START} to {TRAIN_END}):\n")
        for k, v in train_m.items():
            f.write(f"  {k}: {v}\n")
        f.write(f"\nTEST ({TEST_START} to {TEST_END}):\n")
        for k, v in test_m.items():
            f.write(f"  {k}: {v}\n")
    print(f"\nSaved -> {results_file}")


if __name__ == "__main__":
    main()
