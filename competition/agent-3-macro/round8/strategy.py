"""
Agent 3 - Macro Strategist - Round 8
====================================
TRAIN: 2024-07-01..2025-06-30   TEST: 2025-07-01..2025-09-30

Thesis (home-run mode; 3 rounds remain, winner = highest single-round return):
- Scoreboard leader is A4 +48.47 (R1). To beat it we need asymmetric upside.
- R7 base worked: trend + extreme dip on BTC daily (+63% on 2024-H2 hidden).
- Add SOL rotation: when SOL shows strong relative momentum vs BTC AND trend
  stack is up, route capital to SOL (higher beta; bigger payoffs in bull legs).
  Q3 2025 has historically been alt-volatile; SOL can 2x BTC's return.
- Keep the aggressive dip-buyer (drawdown>18% + RSI<30) as asymmetric bet.
- Full allocation (98%) per position; one position at a time.

Rules:
  SIGNAL A (Trend): Close>EMA20>EMA50 and EMA20 rising. Instrument selection:
      if SOL 20d-return > BTC 20d-return * 1.5 AND SOL trend stack up -> SOL,
      else -> BTC. Exit when close < EMA20 of held asset or -8% stop.
  SIGNAL B (Dip, BTC only): drawdown60d>18% and RSI14<30 -> BTC long.
      Exit: +12% TP / -7% stop / 20-bar timeout / graduate into trend.
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
INTERVAL = "1d"


def _ms(d: str) -> int:
    return int(datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)


def fetch_klines(symbol: str, interval: str, start: str, end: str) -> list[dict]:
    start_ms = _ms(start) - 120 * 86400_000
    end_ms = _ms(end) + 86400_000
    out: list[dict] = []
    cur = start_ms
    while cur < end_ms:
        qs = urllib.parse.urlencode(
            {"symbol": symbol, "interval": interval, "startTime": cur, "endTime": end_ms, "limit": 1000}
        )
        req = urllib.request.Request(f"{BINANCE_URL}?{qs}", headers={"User-Agent": "a3-macro-r8"})
        for attempt in range(5):
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    data = json.loads(r.read())
                break
            except Exception:
                time.sleep(1 + attempt)
        else:
            raise RuntimeError(f"binance fetch failed: {symbol}")
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
    ag = al = 0.0
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


def _align(bars_a: list[dict], bars_b: list[dict]) -> tuple[list[dict], list[dict]]:
    idx_b = {b["t"]: b for b in bars_b}
    out_a, out_b = [], []
    for a in bars_a:
        if a["t"] in idx_b:
            out_a.append(a)
            out_b.append(idx_b[a["t"]])
    return out_a, out_b


def run_backtest(start: str, end: str, initial_capital: float = 1000.0) -> dict:
    btc = fetch_klines("BTCUSDT", INTERVAL, start, end)
    sol = fetch_klines("SOLUSDT", INTERVAL, start, end)
    if not btc or not sol:
        return {
            "final_equity": initial_capital,
            "total_return_pct": 0.0,
            "sharpe_ratio": 0.0,
            "max_drawdown_pct": 0.0,
            "num_trades": 0,
            "win_rate": 0.0,
        }
    btc, sol = _align(btc, sol)
    closes_b = [b["close"] for b in btc]
    closes_s = [b["close"] for b in sol]
    highs_b = [b["high"] for b in btc]
    e20_b = ema(closes_b, 20)
    e50_b = ema(closes_b, 50)
    e20_s = ema(closes_s, 20)
    e50_s = ema(closes_s, 50)
    rs14_b = rsi(closes_b, 14)

    start_ms = _ms(start)
    end_ms = _ms(end)

    cash = initial_capital
    units = 0.0
    held_sym = None  # "BTC" | "SOL"
    entry_price = 0.0
    entry_bar = -1
    mode: str | None = None  # "trend" | "dip"
    equity_curve: list[float] = []
    trades: list[dict] = []
    wins = 0
    realized_trades = 0

    def px(i: int) -> float:
        return closes_s[i] if held_sym == "SOL" else closes_b[i]

    for i, b in enumerate(btc):
        if b["t"] < start_ms or b["t"] > end_ms:
            eq = cash + (units * px(i) if units > 0 else 0.0)
            equity_curve.append(eq)
            continue
        if i < 60:
            equity_curve.append(cash)
            continue

        p_b = closes_b[i]
        p_s = closes_s[i]

        hi60 = max(highs_b[i - 60 : i + 1])
        dd = (hi60 - p_b) / hi60 if hi60 > 0 else 0.0
        trend_btc = p_b > e20_b[i] > e50_b[i] and e20_b[i] > e20_b[i - 1]
        trend_sol = p_s > e20_s[i] > e50_s[i] and e20_s[i] > e20_s[i - 1]

        # Relative 20d momentum
        ret20_b = (closes_b[i] / closes_b[i - 20] - 1) if i >= 20 else 0.0
        ret20_s = (closes_s[i] / closes_s[i - 20] - 1) if i >= 20 else 0.0

        # Manage open position
        if units > 0:
            price = px(i)
            held_bars = i - entry_bar
            ret = (price - entry_price) / entry_price
            e20_held = e20_s[i] if held_sym == "SOL" else e20_b[i]
            exit_reason = None
            if mode == "trend":
                if price < e20_held:
                    exit_reason = "trend_break"
                elif ret <= -0.08:
                    exit_reason = "trend_stop"
            elif mode == "dip":
                if ret >= 0.12:
                    exit_reason = "dip_tp"
                elif ret <= -0.07:
                    exit_reason = "dip_stop"
                elif held_bars >= 20:
                    exit_reason = "dip_timeout"
                elif trend_btc and ret > 0.03:
                    mode = "trend"
            if exit_reason:
                proceeds = units * price * (1 - FEE)
                cash += proceeds
                pnl = proceeds - entry_price * units * (1 + FEE)
                if pnl > 0:
                    wins += 1
                realized_trades += 1
                trades.append(
                    {"date": b["date"], "sym": held_sym, "side": "SELL",
                     "price": price, "pnl": pnl, "reason": exit_reason}
                )
                units = 0.0
                entry_price = 0.0
                mode = None
                held_sym = None

        # Entries
        if units == 0:
            signal = None
            sym = None
            if dd > 0.18 and rs14_b[i] < 30:
                signal = "dip"
                sym = "BTC"
            else:
                # Prefer SOL if it's clearly outperforming and trend-up
                if trend_sol and ret20_s > max(ret20_b * 1.5, 0.05):
                    signal = "trend"
                    sym = "SOL"
                elif trend_btc:
                    signal = "trend"
                    sym = "BTC"
            if signal:
                price = p_s if sym == "SOL" else p_b
                alloc = cash * 0.98
                units = alloc / price * (1 - FEE)
                entry_price = price
                entry_bar = i
                mode = signal
                held_sym = sym
                cash -= alloc
                trades.append(
                    {"date": b["date"], "sym": sym, "side": "BUY",
                     "price": price, "pnl": 0.0, "reason": f"{signal}_entry"}
                )

        equity_curve.append(cash + units * px(i))

    # Close at end
    if units > 0:
        last_p = px(len(btc) - 1)
        proceeds = units * last_p * (1 - FEE)
        cash += proceeds
        pnl = proceeds - entry_price * units * (1 + FEE)
        if pnl > 0:
            wins += 1
        realized_trades += 1
        trades.append(
            {"date": btc[-1]["date"], "sym": held_sym, "side": "SELL",
             "price": last_p, "pnl": pnl, "reason": "eod_close"}
        )
        units = 0.0
        equity_curve[-1] = cash

    final_equity = cash
    total_return_pct = (final_equity / initial_capital - 1) * 100

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
        d = (peak - eq) / peak if peak > 0 else 0.0
        max_dd = max(max_dd, d)

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
        f"AGENT 3 - MACRO STRATEGIST - ROUND 8 - {label}",
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
        lines.append(
            f"  {t['date']}  {t['sym']:4s}  {t['side']:4s}  ${t['price']:>10.2f}  "
            f"pnl=${t['pnl']:>8.2f}  {t['reason']}"
        )
    lines.append("=" * 60)
    return "\n".join(lines)


if __name__ == "__main__":
    train = run_backtest("2024-07-01", "2025-06-30")
    test = run_backtest("2025-07-01", "2025-09-30")
    out = (
        _fmt("TRAIN", train, "2024-07-01 to 2025-06-30")
        + "\n\n"
        + _fmt("TEST", test, "2025-07-01 to 2025-09-30")
    )
    print(out)
    Path(__file__).parent.joinpath("results.txt").write_text(out)
