"""
Agent 1 — Quantitative Trader | Round 7
========================================
Strategy: RSI Dip-Buy Tournament with ATR Trailing Stop (Daily)

Rationale from prior rounds:
- R4 win (+15.39% eval): simple RSI dip-buy on BTC daily — our best single-round result.
- R6 blowup (-46%): over-fitted 4h multi-variant tournament. Lesson: simpler wins.
- R5 loss: too few trades, bad regime on 1 symbol.
- R3 modest win: regime-adaptive 4h worked OK but complex.

R7 plan:
- Daily bars, long-only, one asset at a time (full capital).
- Small parameter grid {rsi_period, rsi_buy, rsi_sell, atr_mult} across BTC/ETH/SOL.
- Select best by TRAIN Sharpe (tie-break return), then evaluate on TEST.
- ATR trailing stop protects against R6-style blowups.
- run_backtest(start, end) returns metrics for the *final chosen* strategy.
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

TRAIN_START, TRAIN_END = "2024-01-01", "2024-06-30"
TEST_START, TEST_END = "2024-07-01", "2024-12-31"


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


# -------------------- backtest core --------------------
@dataclass
class Params:
    rsi_period: int
    rsi_buy: float
    rsi_sell: float
    atr_mult: float


def simulate(bars: list[dict], p: Params, capital: float = 1000.0) -> dict:
    closes = [b["close"] for b in bars]
    rs = rsi(closes, p.rsi_period)
    at = atr(bars, 14)

    cash = capital
    pos = 0.0
    entry = 0.0
    trail_stop = 0.0
    equity_curve = [capital]
    trades: list[dict] = []

    for i in range(1, len(bars)):
        price = closes[i]
        r_prev = rs[i - 1]
        a_prev = at[i - 1]

        # mark equity
        eq = cash + pos * price
        equity_curve.append(eq)

        if pos > 0:
            # update trailing stop on new highs
            new_stop = price - p.atr_mult * a_prev if not math.isnan(a_prev) else trail_stop
            if new_stop > trail_stop:
                trail_stop = new_stop
            exit_sig = False
            reason = ""
            if price <= trail_stop:
                exit_sig = True
                reason = "trail"
            elif not math.isnan(r_prev) and r_prev >= p.rsi_sell:
                exit_sig = True
                reason = "rsi_sell"
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
            if not math.isnan(r_prev) and r_prev <= p.rsi_buy and not math.isnan(a_prev):
                pos = (cash * (1 - FEE)) / price
                entry = price
                cash = 0.0
                trail_stop = price - p.atr_mult * a_prev

    # close at end
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
    # metrics
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


# -------------------- tournament / selection --------------------
PARAM_GRID: list[Params] = [
    Params(rp, rb, rs_, am)
    for rp in (10, 14)
    for rb in (25, 30, 35)
    for rs_ in (60, 65, 70)
    for am in (2.5, 3.5)
]


def _load(symbol: str, start: str, end: str) -> list[dict]:
    # warmup buffer
    import datetime as dt
    s = dt.datetime.strptime(start, "%Y-%m-%d") - dt.timedelta(days=60)
    return fetch_klines(symbol, s.strftime("%Y-%m-%d"), end)


def _slice(bars: list[dict], start: str, end: str) -> list[dict]:
    s = _to_ms(start)
    e = _to_ms(end) + 24 * 3600 * 1000 - 1
    return [b for b in bars if s <= b["t"] <= e]


_SELECTION_CACHE: dict[str, Any] = {}


def select_best() -> dict:
    """Run tournament over TRAIN period, return best (symbol, params)."""
    if "best" in _SELECTION_CACHE:
        return _SELECTION_CACHE["best"]
    best = None
    leaderboard = []
    for sym in SYMBOLS:
        full = _load(sym, TRAIN_START, TRAIN_END)
        train_bars_full = full  # includes warmup
        # but simulate needs warmup too so indicators are ready; slice to train range at signal level
        for p in PARAM_GRID:
            res = simulate(_slice_with_warmup(full, TRAIN_START, TRAIN_END), p)
            score = res["sharpe_ratio"] * 0.6 + res["total_return_pct"] * 0.4
            entry = {"symbol": sym, "params": p, "score": score, "res": res}
            leaderboard.append(entry)
            if best is None or score > best["score"]:
                best = entry
    _SELECTION_CACHE["best"] = best
    _SELECTION_CACHE["leaderboard"] = leaderboard
    return best


def _slice_with_warmup(bars: list[dict], start: str, end: str) -> list[dict]:
    # keep 40 warmup bars before start
    s = _to_ms(start)
    e = _to_ms(end) + 24 * 3600 * 1000 - 1
    idx = next((i for i, b in enumerate(bars) if b["t"] >= s), 0)
    idx = max(0, idx - 40)
    return [b for b in bars[idx:] if b["t"] <= e]


# -------------------- public API --------------------
def run_backtest(start: str, end: str, initial_capital: float = 1000.0) -> dict:
    best = select_best()
    sym = best["symbol"]
    p: Params = best["params"]
    full = _load(sym, start, end)
    bars = _slice_with_warmup(full, start, end)
    res = simulate(bars, p, capital=initial_capital)
    return {
        "final_equity": res["final_equity"],
        "total_return_pct": res["total_return_pct"],
        "sharpe_ratio": res["sharpe_ratio"],
        "max_drawdown_pct": res["max_drawdown_pct"],
        "num_trades": res["num_trades"],
        "win_rate": res["win_rate"],
        "strategy_name": f"{sym}:RSI_Dip({p.rsi_period},{p.rsi_buy}/{p.rsi_sell}),ATR{p.atr_mult}",
        "trades": res["trades"],
    }


def main() -> None:
    print("=" * 60)
    print("Agent 1 — R7: RSI Dip-Buy Tournament (Daily)")
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

    out = Path(__file__).parent / "results.txt"
    lines = []
    lines.append("Agent 1 — Quantitative Trader | Round 7")
    lines.append("Strategy: RSI Dip-Buy Tournament + ATR Trailing Stop (Daily)")
    lines.append(f"Selected: {train['strategy_name']}")
    lines.append(f"Symbols tested: {','.join(SYMBOLS)} | Fee: {FEE*100}%")
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
    lines.append("TEST trade log:")
    for t in test["trades"]:
        lines.append(f"  entry={t['entry']:.2f} exit={t['exit']:.2f} "
                     f"pnl={t['pnl_pct']:.2f}% reason={t['reason']}")
    out.write_text("\n".join(lines))
    print(f"\nWrote {out}")


if __name__ == "__main__":
    main()
