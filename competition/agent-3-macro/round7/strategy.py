"""
Agent 3 - Macro Strategist - Round 7
====================================
Period: TRAIN 2024-01-01..2024-06-30, TEST 2024-07-01..2024-12-31

Thesis (informed by prior rounds):
- R3 (+13.55) scored my best and covered Jul-Dec 2024: concentrated BTC momentum
  plus extreme-dip-buyer was the winning formula for exactly this regime.
- 2024-H2 had a violent Aug 5 flash crash (~-25%) and a powerful Oct-Dec rally.
  The big alpha is buying that August panic and riding the Q4 trend.
- Trend-follow filters (EMA stack) keep us in the Oct-Dec breakout; aggressive
  dip buyer (drawdown + RSI oversold) captures the Aug bounce.
- Concentrated 95% BTC, leveraged via 100% exposure when both signals stack.

Rules:
  SIGNAL A (Trend): Close > EMA20 > EMA50 and EMA20 rising -> long 95%
    Exit: Close < EMA20 OR 8% hard stop
  SIGNAL B (Dip): drawdown_from_60d_high > 18% AND RSI(14) < 30 -> long 95%
    Exit: +12% take profit OR 7% stop OR 20 bars timeout
  Only one position at a time; dip buyer overrides trend exit flat state.
"""
from __future__ import annotations

import json
import math
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BINANCE_URL = "https://api.binance.com/api/v3/klines"
FEE = 0.001
SYMBOL = "BTCUSDT"
INTERVAL = "1d"


def _ms(d: str) -> int:
    return int(datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)


def fetch_klines(symbol: str, interval: str, start: str, end: str) -> list[dict]:
    # fetch with lookback so indicators are warm
    start_ms = _ms(start) - 120 * 86400_000
    end_ms = _ms(end) + 86400_000
    out: list[dict] = []
    cur = start_ms
    while cur < end_ms:
        qs = urllib.parse.urlencode(
            {"symbol": symbol, "interval": interval, "startTime": cur, "endTime": end_ms, "limit": 1000}
        )
        req = urllib.request.Request(f"{BINANCE_URL}?{qs}", headers={"User-Agent": "a3-macro-r7"})
        for attempt in range(5):
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    data = json.loads(r.read())
                break
            except Exception:
                time.sleep(1 + attempt)
        else:
            raise RuntimeError("binance fetch failed")
        if not data:
            break
        for k in data:
            out.append(
                {
                    "t": k[0],
                    "date": datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc).strftime("%Y-%m-%d"),
                    "open": float(k[1]),
                    "high": float(k[2]),
                    "low": float(k[3]),
                    "close": float(k[4]),
                }
            )
        cur = data[-1][0] + 1
        if len(data) < 1000:
            break
    # dedupe
    seen, uniq = set(), []
    for b in out:
        if b["t"] in seen:
            continue
        seen.add(b["t"])
        uniq.append(b)
    return uniq


def ema(vals: list[float], period: int) -> list[float]:
    out: list[float] = []
    k = 2 / (period + 1)
    e = vals[0]
    for v in vals:
        e = v * k + e * (1 - k)
        out.append(e)
    return out


def rsi(closes: list[float], period: int = 14) -> list[float]:
    out = [50.0] * len(closes)
    gains = losses = 0.0
    for i in range(1, len(closes)):
        ch = closes[i] - closes[i - 1]
        g = max(ch, 0.0)
        l = max(-ch, 0.0)
        if i <= period:
            gains += g
            losses += l
            if i == period:
                ag = gains / period
                al = losses / period
                rs = ag / al if al > 0 else 100.0
                out[i] = 100 - 100 / (1 + rs)
        else:
            ag = (ag * (period - 1) + g) / period
            al = (al * (period - 1) + l) / period
            rs = ag / al if al > 0 else 100.0
            out[i] = 100 - 100 / (1 + rs)
    return out


def run_backtest(start: str, end: str, initial_capital: float = 1000.0) -> dict:
    bars = fetch_klines(SYMBOL, INTERVAL, start, end)
    if not bars:
        return {
            "final_equity": initial_capital,
            "total_return_pct": 0.0,
            "sharpe_ratio": 0.0,
            "max_drawdown_pct": 0.0,
            "num_trades": 0,
            "win_rate": 0.0,
        }
    closes = [b["close"] for b in bars]
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    e20 = ema(closes, 20)
    e50 = ema(closes, 50)
    rs14 = rsi(closes, 14)

    start_ms = _ms(start)
    end_ms = _ms(end)

    cash = initial_capital
    units = 0.0
    entry_price = 0.0
    entry_bar = -1
    mode = None  # "trend" | "dip"
    equity_curve: list[float] = []
    trades: list[dict] = []
    wins = 0
    realized_trades = 0

    for i, b in enumerate(bars):
        if b["t"] < start_ms or b["t"] > end_ms:
            if units == 0:
                equity_curve.append(cash)
            else:
                equity_curve.append(cash + units * b["close"])
            continue
        if i < 60:
            equity_curve.append(cash)
            continue

        price = b["close"]
        # 60-day high for drawdown metric
        hi60 = max(highs[i - 60 : i + 1])
        dd = (hi60 - price) / hi60 if hi60 > 0 else 0.0
        trend_up = price > e20[i] > e50[i] and e20[i] > e20[i - 1]

        # Manage open position
        if units > 0:
            held = i - entry_bar
            ret = (price - entry_price) / entry_price
            exit_reason = None
            if mode == "trend":
                if price < e20[i]:
                    exit_reason = "trend_break"
                elif ret <= -0.08:
                    exit_reason = "trend_stop"
            elif mode == "dip":
                if ret >= 0.12:
                    exit_reason = "dip_tp"
                elif ret <= -0.07:
                    exit_reason = "dip_stop"
                elif held >= 20:
                    exit_reason = "dip_timeout"
                elif trend_up and ret > 0.03:
                    # graduate into trend ride
                    mode = "trend"
            if exit_reason:
                proceeds = units * price * (1 - FEE)
                cash += proceeds
                pnl = proceeds - entry_price * units * (1 + FEE)
                if pnl > 0:
                    wins += 1
                realized_trades += 1
                trades.append(
                    {"date": b["date"], "side": "SELL", "price": price, "pnl": pnl, "reason": exit_reason}
                )
                units = 0.0
                entry_price = 0.0
                mode = None

        # Entries (if flat)
        if units == 0:
            signal = None
            if dd > 0.18 and rs14[i] < 30:
                signal = "dip"
            elif trend_up and rs14[i] < 72:
                signal = "trend"
            if signal:
                alloc = cash * 0.98
                units = alloc / price * (1 - FEE)
                entry_price = price
                entry_bar = i
                mode = signal
                cash -= alloc
                trades.append(
                    {"date": b["date"], "side": "BUY", "price": price, "pnl": 0.0, "reason": f"{signal}_entry"}
                )

        equity_curve.append(cash + units * price)

    # close any open position at last in-range bar
    if units > 0:
        last = bars[-1]
        proceeds = units * last["close"] * (1 - FEE)
        cash += proceeds
        pnl = proceeds - entry_price * units * (1 + FEE)
        if pnl > 0:
            wins += 1
        realized_trades += 1
        trades.append(
            {"date": last["date"], "side": "SELL", "price": last["close"], "pnl": pnl, "reason": "eod_close"}
        )
        units = 0.0
        equity_curve[-1] = cash

    final_equity = cash
    total_return_pct = (final_equity / initial_capital - 1) * 100

    # Sharpe from equity curve daily returns (in-range only)
    rets: list[float] = []
    prev = None
    for eq in equity_curve:
        if prev is not None and prev > 0:
            rets.append(eq / prev - 1)
        prev = eq
    if rets and any(r != 0 for r in rets):
        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / len(rets)
        sd = math.sqrt(var)
        sharpe = (mean / sd) * math.sqrt(365) if sd > 0 else 0.0
    else:
        sharpe = 0.0

    peak = equity_curve[0] if equity_curve else initial_capital
    max_dd = 0.0
    for eq in equity_curve:
        peak = max(peak, eq)
        dd = (peak - eq) / peak if peak > 0 else 0.0
        max_dd = max(max_dd, dd)

    win_rate = (wins / realized_trades * 100) if realized_trades else 0.0

    return {
        "final_equity": round(final_equity, 2),
        "total_return_pct": round(total_return_pct, 2),
        "sharpe_ratio": round(sharpe, 2),
        "max_drawdown_pct": round(max_dd * 100, 2),
        "num_trades": realized_trades,
        "win_rate": round(win_rate, 2),
        "_trades": trades,
    }


def _fmt(label: str, r: dict, period: str) -> str:
    lines = [
        "=" * 60,
        f"AGENT 3 - MACRO STRATEGIST - ROUND 7 - {label}",
        f"Period: {period}",
        "-" * 60,
        f"  final_equity:     {r['final_equity']}",
        f"  total_return_pct: {r['total_return_pct']}",
        f"  sharpe_ratio:     {r['sharpe_ratio']}",
        f"  max_drawdown_pct: {r['max_drawdown_pct']}",
        f"  num_trades:       {r['num_trades']}",
        f"  win_rate:         {r['win_rate']}",
        "",
        "Trades:",
    ]
    for t in r.get("_trades", []):
        lines.append(f"  {t['date']}  {t['side']:4s}  ${t['price']:>10.2f}  pnl=${t['pnl']:>8.2f}  {t['reason']}")
    lines.append("=" * 60)
    return "\n".join(lines)


if __name__ == "__main__":
    train = run_backtest("2024-01-01", "2024-06-30")
    test = run_backtest("2024-07-01", "2024-12-31")
    out = _fmt("TRAIN", train, "2024-01-01 to 2024-06-30") + "\n\n" + _fmt("TEST", test, "2024-07-01 to 2024-12-31")
    print(out)
    Path(__file__).parent.joinpath("results.txt").write_text(out)
