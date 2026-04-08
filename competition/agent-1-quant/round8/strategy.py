"""
Agent 1 — Quantitative Trader | Round 8
========================================
Strategy: Multi-Signal Tournament (RSI Dip + Donchian Breakout + EMA Trend)
           Daily bars, long-only, single asset, ATR trailing stop.

Rationale:
- R4 won with simple RSI dip (+15.39%). R7 also used RSI dip but chose SOL —
  hidden eval was -29%. Lesson: dip-buying alone is regime sensitive.
- R8 adds momentum/breakout signals so the tournament can pick a trend-follower
  when the TRAIN regime favors it (2024-H2..2025-H1 had big BTC/SOL trends).
- Select best by composite (0.5*Sharpe + 0.5*return/10) on TRAIN, ATR trailing
  stop caps any single-trade blowup at ~ATR_mult * ATR.
- 3 rounds left — we need a big single-round win, so we bias toward higher
  absolute return (tie-breaker: return).

Deliverable:
    run_backtest(start, end, initial_capital=1000.0) -> dict
"""
from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

BINANCE_URL = "https://api.binance.com/api/v3/klines"
FEE = 0.001
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
INTERVAL = "1d"

TRAIN_START, TRAIN_END = "2024-07-01", "2025-06-30"
TEST_START, TEST_END = "2025-07-01", "2025-09-30"


# -------------------- data --------------------
def _to_ms(d: str) -> int:
    import datetime as dt
    return int(dt.datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp() * 1000)


def fetch_klines(symbol: str, start: str, end: str, interval: str = INTERVAL) -> list[dict]:
    start_ms = _to_ms(start)
    end_ms = _to_ms(end) + 24 * 3600 * 1000 - 1
    out: list[dict] = []
    cur = start_ms
    while cur < end_ms:
        url = (
            f"{BINANCE_URL}?symbol={symbol}&interval={interval}"
            f"&startTime={cur}&endTime={end_ms}&limit=1000"
        )
        req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
        for attempt in range(3):
            try:
                with urlopen(req, timeout=30) as r:
                    data = json.loads(r.read())
                break
            except Exception:
                if attempt == 2:
                    raise
                time.sleep(1.5)
        if not data:
            break
        for k in data:
            out.append({
                "t": int(k[0]),
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
            })
        last = int(data[-1][0])
        if last <= cur:
            break
        cur = last + 1
        if len(data) < 1000:
            break
    return out


# -------------------- indicators --------------------
def rsi(closes: list[float], period: int) -> list[float]:
    out = [float("nan")] * len(closes)
    if len(closes) <= period:
        return out
    gains = 0.0
    losses = 0.0
    for i in range(1, period + 1):
        ch = closes[i] - closes[i - 1]
        if ch >= 0:
            gains += ch
        else:
            losses -= ch
    avg_g = gains / period
    avg_l = losses / period
    out[period] = 100 - 100 / (1 + (avg_g / avg_l if avg_l > 0 else 1e9))
    for i in range(period + 1, len(closes)):
        ch = closes[i] - closes[i - 1]
        g = max(ch, 0.0)
        l = max(-ch, 0.0)
        avg_g = (avg_g * (period - 1) + g) / period
        avg_l = (avg_l * (period - 1) + l) / period
        out[i] = 100 - 100 / (1 + (avg_g / avg_l if avg_l > 0 else 1e9))
    return out


def atr(bars: list[dict], period: int = 14) -> list[float]:
    out = [float("nan")] * len(bars)
    if len(bars) <= period:
        return out
    trs = [0.0]
    for i in range(1, len(bars)):
        h, l, pc = bars[i]["high"], bars[i]["low"], bars[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    a = sum(trs[1:period + 1]) / period
    out[period] = a
    for i in range(period + 1, len(bars)):
        a = (a * (period - 1) + trs[i]) / period
        out[i] = a
    return out


def ema(values: list[float], period: int) -> list[float]:
    out = [float("nan")] * len(values)
    if len(values) < period:
        return out
    k = 2 / (period + 1)
    s = sum(values[:period]) / period
    out[period - 1] = s
    for i in range(period, len(values)):
        s = values[i] * k + s * (1 - k)
        out[i] = s
    return out


def donchian(bars: list[dict], period: int) -> tuple[list[float], list[float]]:
    n = len(bars)
    up = [float("nan")] * n
    dn = [float("nan")] * n
    for i in range(period, n):
        window = bars[i - period:i]
        up[i] = max(b["high"] for b in window)
        dn[i] = min(b["low"] for b in window)
    return up, dn


# -------------------- strategy kernels --------------------
@dataclass
class Params:
    kind: str            # "rsi" | "donch" | "ema"
    a: int               # period1 / rsi_period / donch_period / ema_fast
    b: float             # rsi_buy / (unused) / ema_slow
    c: float             # rsi_sell / (unused) / (unused)
    atr_mult: float


def simulate(bars: list[dict], p: Params, capital: float = 1000.0) -> dict:
    closes = [b["close"] for b in bars]
    at = atr(bars, 14)

    # precompute per-kind
    rs = rsi(closes, int(p.a)) if p.kind == "rsi" else None
    don_up, don_dn = (donchian(bars, int(p.a)) if p.kind == "donch" else (None, None))
    ef = ema(closes, int(p.a)) if p.kind == "ema" else None
    es = ema(closes, int(p.b)) if p.kind == "ema" else None

    cash = capital
    pos = 0.0
    entry = 0.0
    trail_stop = 0.0
    equity_curve = [capital]
    trades: list[dict] = []

    for i in range(1, len(bars)):
        price = closes[i]
        a_prev = at[i - 1]
        eq = cash + pos * price
        equity_curve.append(eq)

        if pos > 0:
            new_stop = price - p.atr_mult * a_prev if not math.isnan(a_prev) else trail_stop
            if new_stop > trail_stop:
                trail_stop = new_stop
            exit_sig = False
            reason = ""
            if price <= trail_stop:
                exit_sig = True
                reason = "trail"
            else:
                if p.kind == "rsi":
                    r_prev = rs[i - 1]
                    if not math.isnan(r_prev) and r_prev >= p.c:
                        exit_sig = True
                        reason = "rsi_sell"
                elif p.kind == "donch":
                    # exit on break of lower band (prev close below donch_dn)
                    d = don_dn[i - 1]
                    if not math.isnan(d) and closes[i - 1] <= d:
                        exit_sig = True
                        reason = "donch_low"
                elif p.kind == "ema":
                    f = ef[i - 1]
                    s = es[i - 1]
                    if not math.isnan(f) and not math.isnan(s) and f < s:
                        exit_sig = True
                        reason = "ema_cross_dn"
            if exit_sig:
                cash = pos * price * (1 - FEE)
                trades.append({
                    "entry": entry, "exit": price,
                    "pnl_pct": (price / entry - 1) * 100 - 2 * FEE * 100,
                    "reason": reason,
                })
                pos = 0.0
                entry = 0.0
                trail_stop = 0.0
        else:
            enter = False
            if math.isnan(a_prev):
                pass
            elif p.kind == "rsi":
                r_prev = rs[i - 1]
                if not math.isnan(r_prev) and r_prev <= p.b:
                    enter = True
            elif p.kind == "donch":
                d = don_up[i - 1]
                if not math.isnan(d) and closes[i - 1] >= d:
                    enter = True
            elif p.kind == "ema":
                f = ef[i - 1]
                s = es[i - 1]
                f2 = ef[i - 2] if i >= 2 else float("nan")
                s2 = es[i - 2] if i >= 2 else float("nan")
                if (not math.isnan(f) and not math.isnan(s)
                        and not math.isnan(f2) and not math.isnan(s2)
                        and f > s and f2 <= s2):
                    enter = True
            if enter:
                pos = (cash * (1 - FEE)) / price
                entry = price
                cash = 0.0
                trail_stop = price - p.atr_mult * a_prev

    if pos > 0:
        price = closes[-1]
        cash = pos * price * (1 - FEE)
        trades.append({
            "entry": entry, "exit": price,
            "pnl_pct": (price / entry - 1) * 100 - 2 * FEE * 100,
            "reason": "eod",
        })
        pos = 0.0

    final = cash
    rets = []
    for i in range(1, len(equity_curve)):
        if equity_curve[i - 1] > 0:
            rets.append(equity_curve[i] / equity_curve[i - 1] - 1)
    if rets:
        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / max(1, len(rets) - 1)
        sd = math.sqrt(var)
        sharpe = (mean / sd * math.sqrt(365)) if sd > 0 else 0.0
    else:
        sharpe = 0.0
    peak = equity_curve[0]
    mdd = 0.0
    for e in equity_curve:
        if e > peak:
            peak = e
        dd = (e - peak) / peak * 100
        if dd < mdd:
            mdd = dd
    wins = sum(1 for t in trades if t["pnl_pct"] > 0)
    wr = wins / len(trades) * 100 if trades else 0.0
    return {
        "final_equity": round(final, 2),
        "total_return_pct": round((final / capital - 1) * 100, 2),
        "sharpe_ratio": round(sharpe, 4),
        "max_drawdown_pct": round(mdd, 2),
        "num_trades": len(trades),
        "win_rate": round(wr, 2),
        "trades": trades,
    }


# -------------------- tournament --------------------
def build_grid() -> list[Params]:
    grid: list[Params] = []
    # RSI dip variants
    for rp in (10, 14):
        for rb in (25, 30, 35):
            for rs_ in (60, 65, 70):
                for am in (2.5, 3.5):
                    grid.append(Params("rsi", rp, rb, rs_, am))
    # Donchian breakout variants
    for dp in (20, 30, 40, 55):
        for am in (2.5, 3.5, 5.0):
            grid.append(Params("donch", dp, 0, 0, am))
    # EMA cross variants
    for f, s in [(10, 30), (12, 26), (20, 50), (9, 21)]:
        for am in (2.5, 3.5, 5.0):
            grid.append(Params("ema", f, s, 0, am))
    return grid


def _load(symbol: str, start: str, end: str) -> list[dict]:
    import datetime as dt
    s = dt.datetime.strptime(start, "%Y-%m-%d") - dt.timedelta(days=120)
    return fetch_klines(symbol, s.strftime("%Y-%m-%d"), end)


def _slice_with_warmup(bars: list[dict], start: str, end: str, warmup: int = 90) -> list[dict]:
    s = _to_ms(start)
    e = _to_ms(end) + 24 * 3600 * 1000 - 1
    idx = next((i for i, b in enumerate(bars) if b["t"] >= s), 0)
    idx = max(0, idx - warmup)
    return [b for b in bars[idx:] if b["t"] <= e]


_SELECTION: dict[str, Any] = {}


def select_best() -> dict:
    if "best" in _SELECTION:
        return _SELECTION["best"]
    grid = build_grid()
    best = None
    leaderboard = []
    for sym in SYMBOLS:
        full = _load(sym, TRAIN_START, TRAIN_END)
        train_bars = _slice_with_warmup(full, TRAIN_START, TRAIN_END)
        for p in grid:
            res = simulate(train_bars, p)
            # Composite: return-weighted, sharpe as tiebreaker. Require >=2 trades.
            if res["num_trades"] < 2:
                continue
            score = res["total_return_pct"] * 0.6 + res["sharpe_ratio"] * 10 * 0.4
            # Penalize very deep DDs
            if res["max_drawdown_pct"] < -40:
                score -= 20
            entry = {"symbol": sym, "params": p, "score": score, "res": res}
            leaderboard.append(entry)
            if best is None or score > best["score"]:
                best = entry
    leaderboard.sort(key=lambda x: x["score"], reverse=True)
    _SELECTION["best"] = best
    _SELECTION["leaderboard"] = leaderboard[:10]
    return best


# -------------------- public API --------------------
def run_backtest(start: str, end: str, initial_capital: float = 1000.0) -> dict:
    best = select_best()
    sym = best["symbol"]
    p: Params = best["params"]
    full = _load(sym, start, end)
    bars = _slice_with_warmup(full, start, end)
    res = simulate(bars, p, capital=initial_capital)
    name = f"{sym}:{p.kind}({p.a},{p.b},{p.c}),ATR{p.atr_mult}"
    return {
        "final_equity": res["final_equity"],
        "total_return_pct": res["total_return_pct"],
        "sharpe_ratio": res["sharpe_ratio"],
        "max_drawdown_pct": res["max_drawdown_pct"],
        "num_trades": res["num_trades"],
        "win_rate": res["win_rate"],
        "strategy_name": name,
        "trades": res["trades"],
    }


def main() -> None:
    print("=" * 60)
    print("Agent 1 — R8: Multi-Signal Tournament (Daily)")
    print("=" * 60)

    train = run_backtest(TRAIN_START, TRAIN_END)
    print(f"\nTRAIN {TRAIN_START}..{TRAIN_END}  [{train['strategy_name']}]")
    for k in ("final_equity", "total_return_pct", "sharpe_ratio",
              "max_drawdown_pct", "num_trades", "win_rate"):
        print(f"  {k}: {train[k]}")

    test = run_backtest(TEST_START, TEST_END)
    print(f"\nTEST  {TEST_START}..{TEST_END}  [{test['strategy_name']}]")
    for k in ("final_equity", "total_return_pct", "sharpe_ratio",
              "max_drawdown_pct", "num_trades", "win_rate"):
        print(f"  {k}: {test[k]}")

    lb = _SELECTION.get("leaderboard", [])
    out = Path(__file__).parent / "results.txt"
    lines = []
    lines.append("Agent 1 — Quantitative Trader | Round 8")
    lines.append("Strategy: Multi-Signal Tournament (RSI/Donchian/EMA) + ATR Trail")
    lines.append(f"Selected: {train['strategy_name']}")
    lines.append(f"Symbols: {','.join(SYMBOLS)} | Interval: {INTERVAL} | Fee: {FEE*100}%")
    lines.append("")
    lines.append(f"TRAIN {TRAIN_START}..{TRAIN_END}")
    for k in ("final_equity", "total_return_pct", "sharpe_ratio",
              "max_drawdown_pct", "num_trades", "win_rate"):
        lines.append(f"  {k}: {train[k]}")
    lines.append("")
    lines.append(f"TEST  {TEST_START}..{TEST_END}")
    for k in ("final_equity", "total_return_pct", "sharpe_ratio",
              "max_drawdown_pct", "num_trades", "win_rate"):
        lines.append(f"  {k}: {test[k]}")
    lines.append("")
    lines.append("Top 10 TRAIN leaderboard:")
    for e in lb:
        p = e["params"]
        r = e["res"]
        lines.append(
            f"  {e['symbol']}:{p.kind}(a={p.a},b={p.b},c={p.c}),ATR{p.atr_mult}  "
            f"ret={r['total_return_pct']}%  sh={r['sharpe_ratio']}  "
            f"mdd={r['max_drawdown_pct']}%  n={r['num_trades']}"
        )
    lines.append("")
    lines.append("TEST trade log:")
    for t in test["trades"]:
        lines.append(f"  entry={t['entry']:.2f} exit={t['exit']:.2f} "
                     f"pnl={t['pnl_pct']:.2f}% reason={t['reason']}")
    out.write_text("\n".join(lines))
    print(f"\nWrote {out}")


if __name__ == "__main__":
    main()
