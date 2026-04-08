"""
Agent 5 - Hybrid Strategist - Round 2
Long-only momentum with wide trailing stops and trend persistence.

Design philosophy: In crypto bull markets, the biggest mistake is cutting winners
short. Use wide initial stops and only trail after significant profit.

- Chandelier exit style: trail from highest high, not close
- Only begin trailing after 1.5x ATR profit
- Trail at 3x ATR from highest high
- Generous initial stop: 3.5x ATR
- No take profit ceiling -- let trends run
- Re-enter after stops if signal persists
"""

import time
from datetime import datetime, timedelta
from typing import Any

import numpy as np
import requests


def fetch_klines(symbol: str, interval: str, start: str, end: str) -> np.ndarray:
    url = "https://api.binance.com/api/v3/klines"
    start_ms = int(datetime.strptime(start, "%Y-%m-%d").timestamp() * 1000)
    end_ms = int(datetime.strptime(end, "%Y-%m-%d").timestamp() * 1000)
    all_k: list[list[float]] = []
    cur = start_ms
    while cur < end_ms:
        params = {"symbol": symbol, "interval": interval, "startTime": cur, "endTime": end_ms, "limit": 1000}
        r = requests.get(url, params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
        if not data:
            break
        for k in data:
            all_k.append([k[0], float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])])
        cur = data[-1][0] + 1
        time.sleep(0.1)
    return np.array(all_k) if all_k else np.empty((0, 6))


def sma(data, p):
    out = np.full(len(data), np.nan)
    for i in range(p-1, len(data)):
        out[i] = np.mean(data[i-p+1:i+1])
    return out

def ema(data, p):
    out = np.full(len(data), np.nan)
    if len(data) < p: return out
    k = 2.0/(p+1)
    out[p-1] = np.mean(data[:p])
    for i in range(p, len(data)):
        out[i] = data[i]*k + out[i-1]*(1-k)
    return out

def calc_atr(h, l, c, p=14):
    n = len(c); tr = np.zeros(n)
    tr[0] = h[0]-l[0]
    for i in range(1,n):
        tr[i] = max(h[i]-l[i], abs(h[i]-c[i-1]), abs(l[i]-c[i-1]))
    return sma(tr, p)

def calc_rsi(c, p=14):
    n = len(c); out = np.full(n, np.nan)
    d = np.diff(c)
    if len(d) < p: return out
    g = np.where(d>0, d, 0.0); lo = np.where(d<0, -d, 0.0)
    ag, al = np.mean(g[:p]), np.mean(lo[:p])
    out[p] = 100 - 100/(1+ag/max(al,1e-10))
    for i in range(p, len(d)):
        ag = (ag*(p-1)+g[i])/p; al = (al*(p-1)+lo[i])/p
        out[i+1] = 100 if al==0 else 100-100/(1+ag/al)
    return out

def calc_macd(c, fast=12, slow=26, sig=9):
    ef, es = ema(c, fast), ema(c, slow)
    ml = ef - es
    sl = np.full(len(c), np.nan)
    vs = slow-1
    if vs+sig <= len(c):
        sl[vs:] = ema(ml[vs:], sig)
    return ml, sl, ml-sl


def score_bar(i, c, ema20, ema50, ema200, rsi_v, mhist):
    """Bullish score 0-5. No negative scores since we're long-only."""
    s = 0.0
    # EMA alignment (0-2)
    if not any(np.isnan([ema20[i], ema50[i], ema200[i]])):
        if ema20[i] > ema50[i] > ema200[i]:
            s += 2.0
        elif ema20[i] > ema50[i]:
            s += 1.0
        elif c[i] > ema200[i]:
            s += 0.5
    # RSI momentum (0-1)
    if not np.isnan(rsi_v[i]):
        if 40 < rsi_v[i] < 70:
            s += 1.0  # Healthy momentum
        elif rsi_v[i] < 35:
            s += 0.5  # Oversold bounce potential
    # MACD (0-1.5)
    if i > 0 and not any(np.isnan([mhist[i], mhist[i-1]])):
        if mhist[i] > 0 and mhist[i] > mhist[i-1]:
            s += 1.5  # Accelerating bullish
        elif mhist[i] > 0:
            s += 0.7  # Bullish but decelerating
    # Price above EMA200 (0-0.5)
    if not np.isnan(ema200[i]) and c[i] > ema200[i]:
        s += 0.5
    return s

def is_uptrend(c, ema50, ema200, i):
    if np.isnan(ema50[i]) or np.isnan(ema200[i]):
        return False
    return ema50[i] > ema200[i] or c[i] > ema200[i]


def backtest_symbol(klines, symbol, capital, fee=0.001, start_ms=0):
    if len(klines) < 205:
        return capital, [], [capital]

    c, h, l, ts = klines[:,4], klines[:,2], klines[:,3], klines[:,0]
    ema20 = ema(c,20); ema50 = ema(c,50); ema200 = ema(c,200)
    rsi_v = calc_rsi(c,14)
    _, _, mhist = calc_macd(c)
    atr_v = calc_atr(h, l, c, 14)

    warmup = 200
    si = warmup
    if start_ms > 0:
        for j in range(len(ts)):
            if ts[j] >= start_ms:
                si = max(j, warmup); break

    eq = capital
    pos = 0.0
    entry_p = 0.0
    stop_p = 0.0
    highest_h = 0.0  # Highest HIGH since entry
    entry_atr = 0.0  # ATR at entry time
    trades: list[dict] = []
    eq_curve: list[float] = []
    cooldown = 0  # Bars to wait after a stop-out

    for i in range(si, len(c)):
        unreal = pos * (c[i] - entry_p) if pos > 0 else 0.0
        cur_eq = eq + unreal
        eq_curve.append(cur_eq)

        if np.isnan(atr_v[i]) or atr_v[i] <= 0:
            continue

        dt = datetime.fromtimestamp(ts[i]/1000).strftime("%Y-%m-%d")
        sc = score_bar(i, c, ema20, ema50, ema200, rsi_v, mhist)
        uptrend = is_uptrend(c, ema50, ema200, i)

        if cooldown > 0:
            cooldown -= 1

        # --- Manage position ---
        if pos > 0:
            # Update highest high
            if h[i] > highest_h:
                highest_h = h[i]

            # Trailing: only engage after price moved 1.5x ATR above entry
            profit_dist = highest_h - entry_p
            if profit_dist > entry_atr * 1.5:
                # Chandelier: trail from highest high at 3x current ATR
                trail = highest_h - atr_v[i] * 3.0
                if trail > stop_p:
                    stop_p = trail

            hit_stop = l[i] <= stop_p
            if hit_stop:
                exit_p = max(stop_p, l[i])
                pnl = pos * (exit_p - entry_p) - pos * exit_p * fee
                eq += pos * (exit_p - entry_p) - pos * exit_p * fee
                trades.append({"symbol": symbol, "side": "close_long", "date": dt,
                               "entry": round(entry_p, 2), "exit": round(exit_p, 2),
                               "pnl": round(pnl, 2), "reason": "stop",
                               "bars_held": i - trades[-1].get("_bar", i)})
                pos = 0.0
                cooldown = 2  # Wait 2 bars before re-entry
                continue

        # --- Entry ---
        if pos == 0 and cooldown == 0 and sc >= 3.0 and uptrend:
            stop_dist = atr_v[i] * 3.5
            risk = cur_eq * 0.04  # 4% risk per trade
            size = min(risk / stop_dist, cur_eq * 0.50 / c[i])
            if size * c[i] < 10:
                continue
            entry_p = c[i]
            pos = size
            stop_p = entry_p - stop_dist
            highest_h = h[i]
            entry_atr = atr_v[i]
            eq -= size * entry_p * fee
            trades.append({"symbol": symbol, "side": "long", "date": dt,
                           "entry": round(entry_p, 2), "score": round(sc, 2),
                           "size_usd": round(size*entry_p, 2),
                           "stop": round(stop_p, 2), "_bar": i})

    if pos > 0:
        exit_p = c[-1]
        dt = datetime.fromtimestamp(ts[-1]/1000).strftime("%Y-%m-%d")
        pnl = pos * (exit_p - entry_p) - pos * exit_p * fee
        eq += pnl
        trades.append({"symbol": symbol, "side": "close_eod", "date": dt,
                       "entry": round(entry_p, 2), "exit": round(exit_p, 2), "pnl": round(pnl, 2)})

    # Clean internal fields from trade log
    for t in trades:
        t.pop("_bar", None)

    return eq, trades, eq_curve


def run_backtest(start: str, end: str, initial_capital: float = 1000.0) -> dict[str, Any]:
    start_dt = datetime.strptime(start, "%Y-%m-%d")
    lb = (start_dt - timedelta(days=250)).strftime("%Y-%m-%d")
    start_ms = int(start_dt.timestamp() * 1000)

    symbols = ["BTCUSDT", "ETHUSDT"]
    allocs = {"BTCUSDT": 0.60, "ETHUSDT": 0.40}

    print(f"Fetching data {lb} to {end}...")
    km: dict[str, np.ndarray] = {}
    for s in symbols:
        print(f"  {s}...", end=" ")
        km[s] = fetch_klines(s, "1d", lb, end)
        print(f"{len(km[s])} bars")

    total_eq = 0.0
    all_trades: list[dict] = []
    longest: list[float] = []

    for s in symbols:
        a = allocs[s] * initial_capital
        if len(km[s]) == 0:
            total_eq += a; continue
        eq, tr, curve = backtest_symbol(km[s], s, a, start_ms=start_ms)
        total_eq += eq
        all_trades.extend(tr)
        if len(curve) > len(longest): longest = curve

    ret = (total_eq - initial_capital) / initial_capital * 100
    closed = [t for t in all_trades if t.get("side","").startswith("close")]
    nt = len(closed)
    wins = sum(1 for t in closed if t.get("pnl", 0) > 0)
    wr = wins/nt*100 if nt > 0 else 0

    mdd = 0.0
    if longest:
        pk = longest[0]
        for v in longest:
            if v > pk: pk = v
            dd = (pk-v)/pk*100
            if dd > mdd: mdd = dd

    sharpe = 0.0
    if longest and len(longest) > 1:
        r = np.diff(longest)/np.array(longest[:-1])
        if np.std(r) > 0:
            sharpe = (np.mean(r)/np.std(r))*np.sqrt(365)

    return {
        "final_equity": round(total_eq, 2),
        "total_return_pct": round(ret, 2),
        "sharpe_ratio": round(sharpe, 2),
        "max_drawdown_pct": round(mdd, 2),
        "num_trades": nt,
        "win_rate": round(wr, 1),
        "trade_log": all_trades,
    }


if __name__ == "__main__":
    print("="*60)
    print("Agent 5 - Hybrid Strategist - Round 2")
    print("="*60)

    print("\n--- TRAIN: 2024-04-01 to 2024-09-30 ---")
    train = run_backtest("2024-04-01", "2024-09-30", 1000.0)
    for k in ["final_equity","total_return_pct","sharpe_ratio","max_drawdown_pct","num_trades","win_rate"]:
        print(f"  {k}: {train[k]}")

    print("\n--- TEST: 2024-10-01 to 2024-12-31 ---")
    test = run_backtest("2024-10-01", "2024-12-31", 1000.0)
    for k in ["final_equity","total_return_pct","sharpe_ratio","max_drawdown_pct","num_trades","win_rate"]:
        print(f"  {k}: {test[k]}")

    out = "/Users/rc/Projects/workspace/nautilus-trading/competition/agent-5-hybrid/round2/results.txt"
    with open(out, "w") as f:
        f.write("Agent 5 - Hybrid Strategist - Round 2 Results\n")
        f.write("="*60 + "\n\n")
        f.write("Strategy: Long-only momentum with wide chandelier trailing stops\n")
        f.write("Signals: EMA stack + RSI momentum + MACD histogram\n")
        f.write("Timeframe: Daily | Assets: BTCUSDT (60%) + ETHUSDT (40%)\n")
        f.write("Entry: Bullish score >= 3.0 in uptrend\n")
        f.write("Initial stop: 3.5x ATR | Trail: 3x ATR from highest high (after 1.5x ATR profit)\n")
        f.write("Risk: 4% per trade | Max pos: 50% equity\n\n")

        for label, res in [("TRAIN (2024-04-01 to 2024-09-30)", train),
                           ("TEST (2024-10-01 to 2024-12-31)", test)]:
            f.write(f"{label}\n")
            f.write(f"  Final Equity:  ${res['final_equity']}\n")
            f.write(f"  Return:        {res['total_return_pct']}%\n")
            f.write(f"  Sharpe Ratio:  {res['sharpe_ratio']}\n")
            f.write(f"  Max Drawdown:  {res['max_drawdown_pct']}%\n")
            f.write(f"  Trades:        {res['num_trades']}\n")
            f.write(f"  Win Rate:      {res['win_rate']}%\n\n")
            f.write("  Trade Log:\n")
            for t in res['trade_log']:
                f.write(f"    {t}\n")
            f.write("\n")

    print(f"\nResults saved to {out}")
