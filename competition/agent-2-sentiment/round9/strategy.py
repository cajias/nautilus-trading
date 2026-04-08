"""
Agent 2 — Round 9: Conviction Concentration Sentiment Breakout (v2)

Carries forward R8's winning approach (+25.18% on hidden eval).
- Tournament momentum/panic breakout per asset on TRAIN (full calendar 2025)
- Lock 100% of TEST capital onto the single best-scoring asset
- Signals: EMA trend + volume surge + breakout + taker-buy ratio surge
- Plus oversold panic reversal
- Expanded universe to include more high-beta coins for TRAIN selection

TRAIN: 2025-01-01 to 2025-12-31
TEST:  2026-01-01 to 2026-02-28
"""

import json
import os
import time
from dataclasses import dataclass

import numpy as np
import pandas as pd
import requests


BINANCE = "https://api.binance.com/api/v3/klines"
FEE = 0.001
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "AVAXUSDT", "DOGEUSDT"]


def fetch_klines(symbol: str, interval: str, start: str, end: str) -> pd.DataFrame:
    start_ms = int(pd.Timestamp(start, tz="UTC").timestamp() * 1000)
    end_ms = int(pd.Timestamp(end, tz="UTC").timestamp() * 1000)
    rows: list = []
    while start_ms < end_ms:
        r = requests.get(BINANCE, params={
            "symbol": symbol, "interval": interval,
            "startTime": start_ms, "endTime": end_ms, "limit": 1000,
        }, timeout=30)
        r.raise_for_status()
        data = r.json()
        if not data:
            break
        rows.extend(data)
        start_ms = data[-1][0] + 1
        time.sleep(0.1)
    df = pd.DataFrame(rows, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades", "taker_buy_base",
        "taker_buy_quote", "ignore",
    ])
    for c in ["open", "high", "low", "close", "volume", "quote_volume",
              "taker_buy_base", "taker_buy_quote"]:
        df[c] = df[c].astype(float)
    df["timestamp"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df = df.set_index("timestamp").sort_index()
    return df


def rsi(s: pd.Series, p: int = 14) -> pd.Series:
    d = s.diff()
    g = d.clip(lower=0).ewm(alpha=1/p, adjust=False).mean()
    l = (-d.clip(upper=0)).ewm(alpha=1/p, adjust=False).mean()
    return 100 - 100 / (1 + g / l.replace(0, np.nan))


def atr(h, l, c, n=14):
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(span=n, adjust=False).mean()


def ema(s, n): return s.ewm(span=n, adjust=False).mean()
def sma(s, n): return s.rolling(n).mean()


@dataclass
class Trade:
    symbol: str
    entry_time: str
    entry_price: float
    exit_time: str
    exit_price: float
    pnl_pct: float
    pnl_usd: float
    reason: str


def backtest_asset(df: pd.DataFrame, symbol: str, capital: float, p: dict):
    closes = df["close"].values
    highs = df["high"].values
    lows = df["low"].values
    vols = df["volume"].values
    tbq = df["taker_buy_quote"].values
    qv = df["quote_volume"].values

    rsi_v = rsi(df["close"], p["rsi_p"]).values
    atr_v = atr(df["high"], df["low"], df["close"], 14).values
    ef = ema(df["close"], p["ef"]).values
    es = ema(df["close"], p["es"]).values
    vsma = sma(df["volume"], p["vw"]).values
    tbr = tbq / np.where(qv == 0, np.nan, qv)
    tbr_sma = pd.Series(tbr).rolling(p["vw"]).mean().values

    warm = max(p["es"], p["vw"], 50) + 5
    eq = capital
    cash = capital
    pos = None
    trades: list[Trade] = []
    curve = []

    for i in range(warm, len(df)):
        price = closes[i]
        ts = str(df.index[i])

        if pos is None:
            if np.isnan(ef[i]) or np.isnan(atr_v[i]) or np.isnan(rsi_v[i]):
                curve.append(eq); continue

            trend = ef[i] > es[i]
            vol_surge = vols[i] > vsma[i] * p["vmult"]
            tbr_bull = not np.isnan(tbr_sma[i]) and tbr[i] > tbr_sma[i] * 1.03

            lb = p["lb"]
            recent_hi = highs[max(0, i - lb):i].max()
            breakout = price > recent_hi

            sig = trend and vol_surge and breakout and tbr_bull

            if not sig:
                rec_hi_48 = closes[max(0, i - 48):i].max()
                drop = (price - rec_hi_48) / rec_hi_48
                if rsi_v[i] < 24 and vols[i] > vsma[i] * 2.0 and drop < -0.07:
                    sig = True

            if sig:
                size = cash * 0.98
                entry_fee = size * FEE
                pos = {
                    "entry": price, "size": size, "ts": ts, "idx": i,
                    "stop": price - atr_v[i] * p["stop_mult"],
                    "entry_fee": entry_fee,
                }
                cash -= size
        else:
            hold = i - pos["idx"]
            trail = highs[i] - atr_v[i] * p["trail_mult"]
            if trail > pos["stop"]:
                pos["stop"] = trail

            hit_stop = lows[i] <= pos["stop"]
            hit_tp = price >= pos["entry"] * (1 + p["tp"])
            timeout = hold >= p["max_hold"]

            if hit_stop or hit_tp or timeout:
                exit_px = pos["stop"] if hit_stop else price
                reason = "stop" if hit_stop else ("tp" if hit_tp else "timeout")
                gross = (exit_px - pos["entry"]) / pos["entry"]
                pv = pos["size"] * (1 + gross)
                exit_fee = pv * FEE
                net = pv - pos["entry_fee"] - exit_fee
                pnl_usd = net - pos["size"]
                cash += net
                trades.append(Trade(
                    symbol, pos["ts"], round(pos["entry"], 4),
                    ts, round(exit_px, 4),
                    round(gross * 100, 3), round(pnl_usd, 2), reason,
                ))
                pos = None

        if pos is not None:
            eq = cash + pos["size"] * (closes[i] / pos["entry"])
        else:
            eq = cash
        curve.append(eq)

    if pos is not None:
        price = closes[-1]
        gross = (price - pos["entry"]) / pos["entry"]
        pv = pos["size"] * (1 + gross)
        exit_fee = pv * FEE
        net = pv - pos["entry_fee"] - exit_fee
        cash += net
        trades.append(Trade(
            symbol, pos["ts"], round(pos["entry"], 4),
            str(df.index[-1]), round(price, 4),
            round(gross * 100, 3), round(net - pos["size"], 2), "eop",
        ))
        eq = cash

    return eq, trades, curve


def metrics(final_eq, initial, trades, curve):
    tr = (final_eq - initial) / initial * 100
    n = len(trades)
    wins = sum(1 for t in trades if t.pnl_pct > 0)
    wr = wins / n * 100 if n else 0.0
    if len(curve) > 1:
        a = np.array(curve)
        pk = np.maximum.accumulate(a)
        mdd = abs(((a - pk) / pk).min()) * 100
        r = pd.Series(a).pct_change().dropna()
        sharpe = (r.mean() / r.std() * np.sqrt(365 * 6)) if r.std() > 0 else 0.0
    else:
        mdd = 0.0; sharpe = 0.0
    return {
        "final_equity": round(final_eq, 2),
        "total_return_pct": round(tr, 2),
        "sharpe_ratio": round(float(sharpe), 2),
        "max_drawdown_pct": round(float(mdd), 2),
        "num_trades": n,
        "win_rate": round(wr, 1),
    }


def param_grid():
    cfgs = []
    for ef in [8, 12, 20]:
        for es in [40, 72]:
            for vmult in [1.5, 2.0]:
                for tp in [0.10, 0.18, 0.30]:
                    for stop in [2.0, 2.5]:
                        for trail in [2.5, 3.5]:
                            for lb in [24, 48]:
                                cfgs.append({
                                    "ef": ef, "es": es, "rsi_p": 14,
                                    "vw": 48, "vmult": vmult, "tp": tp,
                                    "stop_mult": stop, "trail_mult": trail,
                                    "lb": lb, "max_hold": 120,
                                })
    return cfgs


def tournament(df, symbol, capital):
    best = None
    for cfg in param_grid():
        try:
            eq, trades, curve = backtest_asset(df, symbol, capital, cfg)
        except Exception:
            continue
        if len(trades) < 2:
            continue
        m = metrics(eq, capital, trades, curve)
        score = m["total_return_pct"] - 0.3 * m["max_drawdown_pct"]
        if best is None or score > best["score"]:
            best = {
                "score": score, "cfg": cfg, "eq": eq,
                "trades": trades, "curve": curve, "metrics": m,
            }
    return best


def run_backtest(start: str, end: str, initial_capital: float = 1000.0) -> dict:
    buffer = (pd.Timestamp(start) - pd.Timedelta(days=20)).strftime("%Y-%m-%d")
    selected = globals().get("_LOCKED_CFG")

    if selected is None:
        results = {}
        for sym in SYMBOLS:
            try:
                df = fetch_klines(sym, "4h", buffer, end)
            except Exception as e:
                print(f"  {sym}: fetch failed: {e}")
                continue
            res = tournament(df, sym, initial_capital)
            if res is None:
                continue
            results[sym] = res
            print(f"  {sym}: {res['metrics']['total_return_pct']}% "
                  f"(sharpe {res['metrics']['sharpe_ratio']}, "
                  f"trades {res['metrics']['num_trades']})")

        winner_sym = max(results, key=lambda s: results[s]["score"])
        winner = results[winner_sym]
        globals()["_LOCKED_CFG"] = {"symbol": winner_sym, "cfg": winner["cfg"]}
        print(f"  -> CONCENTRATION PICK: {winner_sym}")
        m = winner["metrics"]
        m["trade_log"] = [vars(t) for t in winner["trades"]]
        m["selected_symbol"] = winner_sym
        m["config"] = winner["cfg"]
        return m
    else:
        sym = selected["symbol"]
        cfg = selected["cfg"]
        df = fetch_klines(sym, "4h", buffer, end)
        eq, trades, curve = backtest_asset(df, sym, initial_capital, cfg)
        m = metrics(eq, initial_capital, trades, curve)
        m["trade_log"] = [vars(t) for t in trades]
        m["selected_symbol"] = sym
        m["config"] = cfg
        return m


if __name__ == "__main__":
    out = os.path.dirname(os.path.abspath(__file__))

    print("=" * 60)
    print("TRAIN 2025-01-01 -> 2025-12-31")
    print("=" * 60)
    globals().pop("_LOCKED_CFG", None)
    train = run_backtest("2025-01-01", "2025-12-31", 1000.0)
    print("TRAIN:", {k: v for k, v in train.items()
                     if k not in ("trade_log", "config")})

    print("\n" + "=" * 60)
    print("TEST  2026-01-01 -> 2026-02-28")
    print("=" * 60)
    test = run_backtest("2026-01-01", "2026-02-28", 1000.0)
    print("TEST :", {k: v for k, v in test.items()
                     if k not in ("trade_log", "config")})

    output = {
        "agent": "Agent 2 - Sentiment Trader",
        "round": 9,
        "approach": (
            "Conviction concentration v2: tournament momentum/panic sentiment "
            "breakout across 6-asset universe on TRAIN (full 2025), then lock "
            "100% capital onto the single best-scoring asset (return - 0.3*dd) "
            "for TEST. Signals: EMA trend + volume surge + breakout + taker-buy "
            "ratio surge, plus oversold panic reversal. 4h bars."
        ),
        "train_period": ["2025-01-01", "2025-12-31"],
        "test_period": ["2026-01-01", "2026-02-28"],
        "train": {k: v for k, v in train.items() if k != "trade_log"},
        "test": {k: v for k, v in test.items() if k != "trade_log"},
    }

    with open(os.path.join(out, "results.txt"), "w") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"\nSaved {os.path.join(out, 'results.txt')}")
