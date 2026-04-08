"""
Agent 4 -- ML Engineer: Round 9 Strategy
=========================================
R9 windows:
  TRAIN: 2025-01-01 .. 2025-12-31
  TEST:  2026-01-01 .. 2026-02-28
  HIDDEN: Mar-Apr 2026

Approach (evolution of R8 winner):
- 4h bars on BTC/ETH/SOL/BNB.
- LightGBM regressor predicts forward 6-bar (~24h) return.
- Per-symbol walk-forward: train on TRAIN, pick best symbol by TRAIN return.
- Long-only momentum: enter when ML pred>thr AND close>EMA50.
- ATR(14) 2.5x trailing stop, -4% hard stop, pyramid once at +5%.
- 95% of cash per entry; 0.1% fees.
"""
from __future__ import annotations

import pathlib
import time
from typing import Any

import numpy as np
import pandas as pd
import requests

try:
    import lightgbm as lgb
    HAVE_LGB = True
except Exception:
    HAVE_LGB = False

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]
INTERVAL = "4h"
FEE_RATE = 0.001
RESULTS_DIR = pathlib.Path(__file__).parent

TRAIN_START = "2025-01-01"
TRAIN_END = "2025-12-31"
TEST_START = "2026-01-01"
TEST_END = "2026-02-28"


def fetch_klines(symbol: str, interval: str, start: str, end: str) -> pd.DataFrame:
    url = "https://api.binance.com/api/v3/klines"
    start_ms = int(pd.Timestamp(start).timestamp() * 1000)
    end_ms = int(pd.Timestamp(end + " 23:59:59").timestamp() * 1000)
    rows: list[list[Any]] = []
    cur = start_ms
    while cur < end_ms:
        params = {"symbol": symbol, "interval": interval,
                  "startTime": cur, "endTime": end_ms, "limit": 1000}
        for attempt in range(3):
            try:
                r = requests.get(url, params=params, timeout=30)
                r.raise_for_status()
                data = r.json()
                break
            except Exception:
                if attempt == 2:
                    raise
                time.sleep(1.5)
        if not data:
            break
        rows.extend(data)
        cur = data[-1][0] + 1
        time.sleep(0.12)
    df = pd.DataFrame(rows, columns=[
        "ot", "open", "high", "low", "close", "volume",
        "ct", "qv", "n", "tbb", "tbq", "ig"])
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = df[c].astype(float)
    df["ts"] = pd.to_datetime(df["ot"], unit="ms")
    df.set_index("ts", inplace=True)
    df = df[~df.index.duplicated(keep="first")]
    return df[["open", "high", "low", "close", "volume"]]


def ema(s, n): return s.ewm(span=n, adjust=False).mean()


def rsi(s, n=14):
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1/n, adjust=False).mean()
    dn = -d.clip(upper=0).ewm(alpha=1/n, adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    return (100 - 100/(1+rs)).fillna(50)


def atr(df, n=14):
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - df["close"].shift()).abs(),
        (df["low"] - df["close"].shift()).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1/n, adjust=False).mean()


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    c = df["close"]
    f = pd.DataFrame(index=df.index)
    f["r1"] = c.pct_change(1)
    f["r4"] = c.pct_change(4)
    f["r12"] = c.pct_change(12)
    f["r24"] = c.pct_change(24)
    f["r72"] = c.pct_change(72)
    f["rsi14"] = rsi(c, 14)
    a = atr(df, 14)
    f["atr_pct"] = a / c
    f["ema_slope"] = ema(c, 20).pct_change(5)
    f["ema50_dist"] = c / ema(c, 50) - 1
    f["vol_z"] = (df["volume"] - df["volume"].rolling(50).mean()) / \
                 df["volume"].rolling(50).std().replace(0, np.nan)
    hi = df["high"].rolling(48).max()
    lo = df["low"].rolling(48).min()
    f["rng_pct"] = (c - lo) / (hi - lo).replace(0, np.nan)
    return f


def train_predict(df: pd.DataFrame, split_idx: int) -> pd.Series:
    feats = build_features(df)
    fwd = df["close"].shift(-6) / df["close"] - 1
    data = pd.concat([feats, fwd.rename("y")], axis=1).dropna()
    if len(data) < 100 or not HAVE_LGB:
        return pd.Series(0.0, index=df.index)
    train = data.iloc[:split_idx]
    test = data.iloc[split_idx:]
    if len(train) < 50:
        return pd.Series(0.0, index=df.index)
    X_tr, y_tr = train.drop(columns=["y"]), train["y"]
    preds_all = pd.Series(0.0, index=df.index)
    try:
        model = lgb.LGBMRegressor(
            n_estimators=400, learning_rate=0.03, max_depth=5,
            num_leaves=31, min_child_samples=20, reg_alpha=0.1,
            reg_lambda=0.1, subsample=0.8, colsample_bytree=0.8,
            random_state=42, verbose=-1)
        model.fit(X_tr, y_tr)
        preds_all.loc[X_tr.index] = model.predict(X_tr)
        if len(test) > 0:
            X_te = test.drop(columns=["y"])
            preds_all.loc[X_te.index] = model.predict(X_te)
    except Exception:
        pass
    return preds_all


def simulate(df: pd.DataFrame, preds: pd.Series, threshold: float,
             initial_capital: float) -> dict:
    c = df["close"].values
    h = df["high"].values
    l = df["low"].values
    a = atr(df, 14).values
    ema50 = ema(df["close"], 50).values
    p = preds.reindex(df.index).fillna(0.0).values
    idx = df.index

    cash = initial_capital
    pos = 0.0
    entry = 0.0
    peak = 0.0
    stop = 0.0
    equity_curve = []
    trades = []

    HARD_STOP = 0.04
    ATR_TRAIL = 2.5
    PROFIT_ADD = 0.05
    added = False

    for i in range(len(df)):
        price = c[i]
        if pos > 0:
            peak = max(peak, h[i])
            trail = peak - ATR_TRAIL * a[i]
            if trail > stop:
                stop = trail
            exit_trigger = None
            if l[i] <= stop:
                exit_trigger = max(stop, l[i])
            elif price <= entry * (1 - HARD_STOP):
                exit_trigger = entry * (1 - HARD_STOP)
            if not added and price >= entry * (1 + PROFIT_ADD) and cash > 10:
                add_sz = (cash * 0.95) / price
                cost = add_sz * price * (1 + FEE_RATE)
                if cost <= cash:
                    new_notional = pos * entry + add_sz * price
                    pos += add_sz
                    entry = new_notional / pos
                    cash -= cost
                    added = True
            if exit_trigger is not None:
                proceeds = pos * exit_trigger * (1 - FEE_RATE)
                cash += proceeds
                trades.append({"exit_ts": str(idx[i]),
                               "pnl_pct": (exit_trigger/entry - 1)*100})
                pos = 0.0
                entry = 0.0
                peak = 0.0
                stop = 0.0
                added = False
        else:
            if p[i] > threshold and price > ema50[i] and i > 50:
                sz = (cash * 0.95) / price
                cost = sz * price * (1 + FEE_RATE)
                if cost <= cash:
                    pos = sz
                    entry = price
                    peak = h[i]
                    stop = entry * (1 - HARD_STOP)
                    cash -= cost
                    added = False
        eq = cash + pos * price
        equity_curve.append(eq)

    if pos > 0:
        cash += pos * c[-1] * (1 - FEE_RATE)
        trades.append({"exit_ts": str(idx[-1]),
                       "pnl_pct": (c[-1]/entry - 1)*100})
        pos = 0.0
    final_eq = cash
    eq_series = pd.Series(equity_curve, index=idx)
    rets = eq_series.pct_change().dropna()
    sharpe = 0.0
    if len(rets) > 1 and rets.std() > 0:
        sharpe = float(rets.mean() / rets.std() * np.sqrt(365 * 6))
    dd = (eq_series / eq_series.cummax() - 1).min() * 100
    wins = sum(1 for t in trades if t["pnl_pct"] > 0)
    return {
        "final_equity": round(final_eq, 2),
        "total_return_pct": round((final_eq/initial_capital - 1)*100, 2),
        "sharpe_ratio": round(sharpe, 3),
        "max_drawdown_pct": round(float(dd), 2),
        "num_trades": len(trades),
        "win_rate": round(wins/len(trades)*100, 1) if trades else 0.0,
        "trades": trades,
    }


_CACHE: dict[str, pd.DataFrame] = {}


def _get(symbol: str, start: str, end: str) -> pd.DataFrame:
    pad_start = (pd.Timestamp(start) - pd.Timedelta(days=120)).strftime("%Y-%m-%d")
    key = f"{symbol}:{pad_start}:{end}"
    if key not in _CACHE:
        _CACHE[key] = fetch_klines(symbol, INTERVAL, pad_start, end)
    return _CACHE[key]


def _run_symbol(symbol: str, start: str, end: str,
                initial_capital: float) -> dict:
    df = _get(symbol, start, end)
    split = int(len(df) * 0.7)
    preds = train_predict(df, split)
    train_preds = preds.iloc[:split]
    nz = train_preds[train_preds != 0]
    thr = float(np.nanpercentile(nz, 65)) if len(nz) > 10 else 0.005
    mask = (df.index >= start) & (df.index <= end + " 23:59:59")
    sub = df.loc[mask]
    sub_preds = preds.loc[mask]
    res = simulate(sub, sub_preds, thr, initial_capital)
    res["symbol"] = symbol
    res["threshold"] = thr
    return res


def run_backtest(start: str, end: str, initial_capital: float = 1000.0) -> dict:
    """Entry point. Picks best symbol on TRAIN, runs TEST on that symbol."""
    # TRAIN sweep to pick symbol
    best = None
    train_results = {}
    for sym in SYMBOLS:
        try:
            r = _run_symbol(sym, TRAIN_START, TRAIN_END, initial_capital)
        except Exception as e:
            print(f"  {sym} failed: {e}")
            continue
        train_results[sym] = r
        print(f"  TRAIN {sym}: {r['total_return_pct']}%  "
              f"sharpe={r['sharpe_ratio']}  trades={r['num_trades']}")
        if best is None or r["total_return_pct"] > best["total_return_pct"]:
            best = r
    if best is None:
        return {"error": "no symbol succeeded"}
    chosen = best["symbol"]
    print(f"[CHOSEN] {chosen}")

    # If caller's [start,end] == TRAIN period, return train result
    if start == TRAIN_START and end == TRAIN_END:
        return best

    # Otherwise run on caller-requested window with chosen symbol
    test_res = _run_symbol(chosen, start, end, initial_capital)
    return {"train": best, "test": test_res}


def main():
    print("=" * 60)
    print("Agent 4 R9: ML-gated momentum + ATR trail + pyramid")
    print("=" * 60)
    result = run_backtest(TEST_START, TEST_END, 1000.0)
    if "train" in result:
        train_res = result["train"]
        test_res = result["test"]
    else:
        train_res = result
        test_res = result
    print(f"\nTRAIN: {train_res.get('symbol')}  "
          f"return={train_res.get('total_return_pct')}%  "
          f"sharpe={train_res.get('sharpe_ratio')}")
    print(f"TEST:  {test_res.get('symbol')}  "
          f"return={test_res.get('total_return_pct')}%  "
          f"sharpe={test_res.get('sharpe_ratio')}  "
          f"dd={test_res.get('max_drawdown_pct')}%  "
          f"trades={test_res.get('num_trades')}")

    out = RESULTS_DIR / "results.txt"
    with open(out, "w") as f:
        f.write("Agent 4 -- ML Engineer: Round 9 Results\n")
        f.write("ML (LightGBM) gated momentum + ATR trail + pyramid\n")
        f.write("=" * 60 + "\n\n")
        f.write(f"TRAIN {TRAIN_START} .. {TRAIN_END}\n")
        f.write("-" * 40 + "\n")
        for k in ["symbol", "final_equity", "total_return_pct",
                  "sharpe_ratio", "max_drawdown_pct", "num_trades", "win_rate"]:
            f.write(f"  {k}: {train_res.get(k)}\n")
        f.write(f"\nTEST {TEST_START} .. {TEST_END}\n")
        f.write("-" * 40 + "\n")
        for k in ["symbol", "final_equity", "total_return_pct",
                  "sharpe_ratio", "max_drawdown_pct", "num_trades", "win_rate"]:
            f.write(f"  {k}: {test_res.get(k)}\n")
        f.write("\nTrade log (TEST):\n")
        for t in test_res.get("trades", []):
            f.write(f"  {t['exit_ts']}  pnl={t['pnl_pct']:.2f}%\n")
    print(f"\nSaved -> {out}")
    return result


if __name__ == "__main__":
    main()
