"""
Agent 4 -- ML Engineer: Round 10 (FINAL)

Defends R1 lead (+48.47%) by reusing the winning BB mean-reversion core
(which beat ML/trend strategies on prior rounds), expanded into a tournament
selected on TRAIN (2024 full year) and evaluated on TEST (2025 H1).

Strategy families:
- BB Mean Reversion with RSI filter (the R1 winner) across several param sets
- EMA trend following (bull 2024 catcher)
- Dip buyer (buy pullbacks above SMA20)

Selection: best TRAIN Sharpe among TRAIN-profitable strategies.
This avoids TEST overfitting -- we never touch TEST for selection.
"""

from __future__ import annotations

import pathlib
import time
from typing import Any

import numpy as np
import pandas as pd
import requests

SYMBOL = "BTCUSDT"
INTERVAL = "1d"
TRAIN_START = "2024-01-01"
TRAIN_END = "2024-12-31"
TEST_START = "2025-01-01"
TEST_END = "2025-06-30"
INITIAL_CAPITAL = 1000.0
POSITION_SIZE = 0.95
FEE_RATE = 0.001
RESULTS_DIR = pathlib.Path(__file__).parent


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

def fetch_binance_klines(symbol: str, interval: str, start: str, end: str) -> pd.DataFrame:
    url = "https://api.binance.com/api/v3/klines"
    start_ms = int(pd.Timestamp(start).timestamp() * 1000)
    end_ms = int(pd.Timestamp(end + " 23:59:59").timestamp() * 1000)
    all_klines: list[list[Any]] = []
    current = start_ms
    while current < end_ms:
        params = {"symbol": symbol, "interval": interval,
                  "startTime": current, "endTime": end_ms, "limit": 1000}
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if not data:
            break
        all_klines.extend(data)
        current = data[-1][0] + 1
        time.sleep(0.15)
    df = pd.DataFrame(all_klines, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "qv", "trades", "tbb", "tbq", "ignore",
    ])
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = df[c].astype(float)
    df["timestamp"] = pd.to_datetime(df["open_time"], unit="ms")
    return df.set_index("timestamp").sort_index()


# ---------------------------------------------------------------------------
# Features
# ---------------------------------------------------------------------------

def ema(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, adjust=False).mean()


def rsi(s: pd.Series, period: int = 14) -> pd.Series:
    d = s.diff()
    g = d.clip(lower=0).rolling(period).mean()
    l = (-d.clip(upper=0)).rolling(period).mean()
    rs = g / l.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def add_features(df: pd.DataFrame) -> pd.DataFrame:
    c = df["close"]
    mid = c.rolling(20).mean()
    std = c.rolling(20).std()
    df["sma_20"] = mid
    df["bb_upper"] = mid + 2 * std
    df["bb_lower"] = mid - 2 * std
    df["rsi_14"] = rsi(c, 14)
    for span in [5, 10, 20, 30, 50]:
        df[f"ema_{span}"] = ema(c, span)
    return df


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def _metrics(capital: float, trades: list, equity_curve: list) -> dict[str, Any]:
    total_return = (capital - INITIAL_CAPITAL) / INITIAL_CAPITAL
    if not equity_curve:
        return {"initial_capital": INITIAL_CAPITAL, "final_equity": round(capital, 2),
                "total_return_pct": round(total_return * 100, 2), "sharpe_ratio": 0.0,
                "max_drawdown_pct": 0.0, "num_trades": 0, "win_rate": 0.0, "trades": trades}
    eq = pd.DataFrame(equity_curve).set_index("timestamp")["equity"]
    daily = eq.resample("D").last().dropna()
    rets = daily.pct_change().dropna()
    sharpe = (rets.mean() / rets.std()) * np.sqrt(365) if rets.std() > 0 else 0.0
    mdd = ((daily / daily.cummax()) - 1).min()
    sells = [t for t in trades if t["type"].startswith("SELL")]
    wins = [t for t in sells if t.get("pnl_pct", 0) > 0]
    return {
        "initial_capital": INITIAL_CAPITAL,
        "final_equity": round(capital, 2),
        "total_return_pct": round(total_return * 100, 2),
        "sharpe_ratio": round(float(sharpe), 4),
        "max_drawdown_pct": round(float(mdd) * 100, 2),
        "num_trades": len([t for t in trades if t["type"] == "BUY"]),
        "win_rate": round(len(wins) / len(sells) * 100, 1) if sells else 0.0,
        "trades": trades,
    }


def _open(capital, price, trades, ts):
    invest = capital * POSITION_SIZE
    fee = invest * FEE_RATE
    pos = (invest - fee) / price
    capital -= invest
    trades.append({"type": "BUY", "time": ts, "price": price, "size": pos})
    return capital, pos, price


def _close(capital, pos, entry, price, trades, ts, final=False):
    proceeds = pos * price
    fee = proceeds * FEE_RATE
    capital += proceeds - fee
    pnl = (price - entry) / entry
    trades.append({"type": "SELL_FINAL" if final else "SELL",
                   "time": ts, "price": price, "pnl_pct": pnl})
    return capital, 0.0, 0.0


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

def strat_bb_reversion(df, start, end, rsi_buy=35, rsi_sell=65, stop=0.05):
    mask = (df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end))
    pdf = df.loc[mask]
    capital, pos, entry = INITIAL_CAPITAL, 0.0, 0.0
    trades, curve = [], []
    for ts, row in pdf.iterrows():
        price = row["close"]
        curve.append({"timestamp": ts, "equity": capital + pos * price})
        r = row["rsi_14"]
        if pd.isna(r):
            continue
        if pos == 0 and price <= row["bb_lower"] and r < rsi_buy:
            capital, pos, entry = _open(capital, price, trades, ts)
        elif pos > 0 and (price >= row["bb_upper"] or r > rsi_sell):
            capital, pos, entry = _close(capital, pos, entry, price, trades, ts)
        elif pos > 0 and (price - entry) / entry < -stop:
            capital, pos, entry = _close(capital, pos, entry, price, trades, ts)
    if pos > 0:
        capital, pos, entry = _close(capital, pos, entry, pdf.iloc[-1]["close"],
                                     trades, pdf.index[-1], final=True)
    return _metrics(capital, trades, curve)


def strat_ema_trend(df, start, end, fast=10, slow=30):
    mask = (df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end))
    pdf = df.loc[mask]
    capital, pos, entry = INITIAL_CAPITAL, 0.0, 0.0
    trades, curve = [], []
    for ts, row in pdf.iterrows():
        price = row["close"]
        curve.append({"timestamp": ts, "equity": capital + pos * price})
        ef, es = row[f"ema_{fast}"], row[f"ema_{slow}"]
        if pd.isna(ef) or pd.isna(es):
            continue
        if pos == 0 and ef > es:
            capital, pos, entry = _open(capital, price, trades, ts)
        elif pos > 0 and ef < es:
            capital, pos, entry = _close(capital, pos, entry, price, trades, ts)
    if pos > 0:
        capital, pos, entry = _close(capital, pos, entry, pdf.iloc[-1]["close"],
                                     trades, pdf.index[-1], final=True)
    return _metrics(capital, trades, curve)


def strat_dip_buyer(df, start, end):
    mask = (df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end))
    pdf = df.loc[mask].copy()
    pdf["ret3"] = pdf["close"].pct_change(3)
    capital, pos, entry = INITIAL_CAPITAL, 0.0, 0.0
    trades, curve = [], []
    for ts, row in pdf.iterrows():
        price = row["close"]
        curve.append({"timestamp": ts, "equity": capital + pos * price})
        sma = row["sma_20"]
        r3 = row["ret3"]
        if pd.isna(sma) or pd.isna(r3):
            continue
        if pos == 0 and price > sma and r3 < -0.03:
            capital, pos, entry = _open(capital, price, trades, ts)
        elif pos > 0:
            pnl = (price - entry) / entry
            if pnl > 0.04 or pnl < -0.05 or price < sma:
                capital, pos, entry = _close(capital, pos, entry, price, trades, ts)
    if pos > 0:
        capital, pos, entry = _close(capital, pos, entry, pdf.iloc[-1]["close"],
                                     trades, pdf.index[-1], final=True)
    return _metrics(capital, trades, curve)


# ---------------------------------------------------------------------------
# Tournament + run_backtest
# ---------------------------------------------------------------------------

STRAT_REGISTRY = {
    "BB_MR(30/70)":    lambda df, s, e: strat_bb_reversion(df, s, e, 30, 70),
    "BB_MR(35/65)":    lambda df, s, e: strat_bb_reversion(df, s, e, 35, 65),
    "BB_MR(40/60)":    lambda df, s, e: strat_bb_reversion(df, s, e, 40, 60),
    "EMA(5,20)":       lambda df, s, e: strat_ema_trend(df, s, e, 5, 20),
    "EMA(10,30)":      lambda df, s, e: strat_ema_trend(df, s, e, 10, 30),
    "EMA(20,50)":      lambda df, s, e: strat_ema_trend(df, s, e, 20, 50),
    "DipBuyer":        lambda df, s, e: strat_dip_buyer(df, s, e),
}


def _select_best(df: pd.DataFrame) -> str:
    results = {}
    for name, fn in STRAT_REGISTRY.items():
        results[name] = fn(df, TRAIN_START, TRAIN_END)
    # Prefer strategies profitable on train, rank by Sharpe then return
    profitable = {n: r for n, r in results.items() if r["total_return_pct"] > 0}
    pool = profitable or results
    return max(pool, key=lambda n: (pool[n]["sharpe_ratio"], pool[n]["total_return_pct"]))


def run_backtest(start: str | None = None, end: str | None = None,
                 initial_capital: float = INITIAL_CAPITAL) -> dict:
    """
    Evaluator-friendly entrypoint. Downloads a buffered window, engineers
    features, picks best strategy on TRAIN, then reports TRAIN and TEST
    (defaulting to this round's periods).
    """
    global INITIAL_CAPITAL
    INITIAL_CAPITAL = float(initial_capital)

    buf_start = "2023-09-01"
    eval_end = end or TEST_END
    df = fetch_binance_klines(SYMBOL, INTERVAL, buf_start, eval_end)
    df = add_features(df)

    best_name = _select_best(df)
    fn = STRAT_REGISTRY[best_name]

    train = fn(df, TRAIN_START, TRAIN_END)
    test_start = start or TEST_START
    test_end = end or TEST_END
    test = fn(df, test_start, test_end)

    # buy & hold test
    m = (df.index >= pd.Timestamp(test_start)) & (df.index <= pd.Timestamp(test_end))
    prices = df.loc[m, "close"]
    bnh = (prices.iloc[-1] / prices.iloc[0] - 1) * 100 if len(prices) > 1 else 0.0

    out = {
        "strategy": best_name,
        "symbol": SYMBOL,
        "interval": INTERVAL,
        "train_period": f"{TRAIN_START} to {TRAIN_END}",
        "test_period": f"{test_start} to {test_end}",
        "buy_and_hold_test_pct": round(bnh, 2),
        "train": {k: v for k, v in train.items() if k != "trades"},
        "test": {k: v for k, v in test.items() if k != "trades"},
    }
    return out


def main() -> None:
    print("Agent 4 R10 FINAL")
    res = run_backtest()
    print(res)
    out = RESULTS_DIR / "results.txt"
    with open(out, "w") as f:
        f.write("Agent 4 -- ML Engineer: Round 10 (FINAL) Results\n")
        f.write("=" * 50 + "\n\n")
        f.write(f"SELECTED: {res['strategy']}\n")
        f.write(f"symbol: {res['symbol']}  interval: {res['interval']}\n")
        f.write(f"train_period: {res['train_period']}\n")
        f.write(f"test_period: {res['test_period']}\n")
        f.write(f"buy_and_hold_test_pct: {res['buy_and_hold_test_pct']}\n\n")
        f.write("TRAIN:\n")
        for k, v in res["train"].items():
            f.write(f"  {k}: {v}\n")
        f.write("\nTEST:\n")
        for k, v in res["test"].items():
            f.write(f"  {k}: {v}\n")
    print(f"Saved {out}")


if __name__ == "__main__":
    main()
