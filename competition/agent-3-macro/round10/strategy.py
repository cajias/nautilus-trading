"""
Agent 3 - Macro Strategist - Round 10 (FINAL)

Bold macro-momentum strategy:
- Universe: BTC, ETH, SOL (Binance daily klines, USDT pairs)
- Regime filter: BTC above its 50d SMA AND 50d SMA rising => RISK ON
- Signal: Donchian 20-day breakout + positive 20d momentum
- Sizing: 3x notional leverage split across active signals (simulated margin,
  liquidation if equity <= 0). Fees 0.075% per side. Funding 0.01%/day on open notional.
- Exits: Chandelier stop (3 * ATR14 from highest close since entry) OR regime flip.
- Pyramiding: add a second unit if price breaks 10-day high after +5% unrealized.

Self-contained. Uses only stdlib + urllib for Binance public klines.
"""
from __future__ import annotations

import json
import math
import time
import urllib.request
from datetime import datetime, timezone

BINANCE = "https://api.binance.com/api/v3/klines"
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
FEE = 0.00075
FUNDING = 0.0001  # per day on notional
LEVERAGE = 3.0


def _to_ms(date_str: str) -> int:
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def fetch_klines(symbol: str, start: str, end: str) -> list[dict]:
    start_ms = _to_ms(start)
    end_ms = _to_ms(end) + 86_400_000
    out: list[dict] = []
    cur = start_ms
    while cur < end_ms:
        url = f"{BINANCE}?symbol={symbol}&interval=1d&startTime={cur}&endTime={end_ms}&limit=1000"
        for attempt in range(3):
            try:
                with urllib.request.urlopen(url, timeout=30) as resp:
                    data = json.loads(resp.read())
                break
            except Exception:
                if attempt == 2:
                    raise
                time.sleep(1.5)
        if not data:
            break
        for k in data:
            out.append({
                "t": k[0],
                "date": datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc).strftime("%Y-%m-%d"),
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
            })
        last_t = data[-1][0]
        if last_t <= cur:
            break
        cur = last_t + 86_400_000
    # de-dup
    seen = set()
    uniq = []
    for b in out:
        if b["t"] in seen:
            continue
        seen.add(b["t"])
        uniq.append(b)
    return uniq


def sma(xs: list[float], n: int, i: int) -> float | None:
    if i + 1 < n:
        return None
    return sum(xs[i - n + 1 : i + 1]) / n


def atr(bars: list[dict], n: int, i: int) -> float | None:
    if i < n:
        return None
    trs = []
    for j in range(i - n + 1, i + 1):
        h, l, pc = bars[j]["high"], bars[j]["low"], bars[j - 1]["close"] if j > 0 else bars[j]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs) / n


def run_backtest(start: str, end: str, initial_capital: float = 1000.0) -> dict:
    data = {s: fetch_klines(s, start, end) for s in SYMBOLS}
    # Build date index using BTC as master
    btc = data["BTCUSDT"]
    if not btc:
        return {"final_equity": initial_capital, "total_return_pct": 0.0,
                "sharpe_ratio": 0.0, "max_drawdown_pct": 0.0, "num_trades": 0,
                "win_rate": 0.0, "trades": []}
    dates = [b["date"] for b in btc]
    idx_by_sym = {s: {b["date"]: k for k, b in enumerate(data[s])} for s in SYMBOLS}
    closes_btc = [b["close"] for b in btc]

    cash = initial_capital
    positions: dict[str, dict] = {}  # sym -> {units, entry, highest, stop, pyramided}
    trades: list[dict] = []
    equity_curve: list[float] = []
    returns: list[float] = []

    def mark_equity(i: int) -> float:
        eq = cash
        for s, p in positions.items():
            j = idx_by_sym[s].get(dates[i])
            if j is None:
                # use last known
                bars = data[s]
                prev_date = None
                for d in dates[: i + 1][::-1]:
                    if d in idx_by_sym[s]:
                        prev_date = d
                        break
                if prev_date is None:
                    continue
                j = idx_by_sym[s][prev_date]
            price = data[s][j]["close"]
            eq += p["units"] * (price - p["entry"])
        return eq

    for i in range(len(btc)):
        date = dates[i]
        # Regime
        sma50 = sma(closes_btc, 50, i)
        sma50_prev = sma(closes_btc, 50, i - 5) if i >= 5 else None
        risk_on = (
            sma50 is not None
            and sma50_prev is not None
            and closes_btc[i] > sma50
            and sma50 > sma50_prev
        )

        # Apply funding on open positions (per day)
        for s, p in positions.items():
            notional = p["units"] * p["entry"]
            cash -= abs(notional) * FUNDING

        # Manage existing positions
        to_close = []
        for s, p in list(positions.items()):
            j = idx_by_sym[s].get(date)
            if j is None or j < 1:
                continue
            bar = data[s][j]
            # Update highest close and chandelier stop
            if bar["close"] > p["highest"]:
                p["highest"] = bar["close"]
                a = atr(data[s], 14, j) or 0.0
                p["stop"] = max(p["stop"], p["highest"] - 3.0 * a)
            # Pyramid: break 10d high & > +5% unrealized, not yet pyramided
            if not p["pyramided"] and j >= 10:
                hi10 = max(data[s][k]["high"] for k in range(j - 10, j))
                if bar["close"] > hi10 and bar["close"] > p["entry"] * 1.05 and risk_on:
                    # add half-unit
                    add_units = p["units"] * 0.5
                    cost = add_units * bar["close"]
                    fee = cost * FEE
                    # no cash movement beyond fees (leverage sim)
                    cash -= fee
                    # blended entry
                    new_units = p["units"] + add_units
                    p["entry"] = (p["entry"] * p["units"] + bar["close"] * add_units) / new_units
                    p["units"] = new_units
                    p["pyramided"] = True
                    trades.append({"date": date, "symbol": s, "side": "ADD",
                                   "price": bar["close"], "pnl": 0.0, "reason": "pyramid"})
            # Exit conditions
            exit_reason = None
            if bar["close"] < p["stop"]:
                exit_reason = "chandelier"
            elif not risk_on:
                exit_reason = "regime_off"
            if exit_reason:
                pnl = p["units"] * (bar["close"] - p["entry"])
                fee = abs(p["units"] * bar["close"]) * FEE
                cash += pnl - fee
                trades.append({"date": date, "symbol": s, "side": "SELL",
                               "price": bar["close"], "pnl": pnl - fee, "reason": exit_reason})
                to_close.append(s)
        for s in to_close:
            del positions[s]

        # Entries: Donchian 20 breakout + 20d momentum positive, only if RISK ON
        if risk_on:
            # target per-position notional = equity * leverage / max_positions
            current_eq = mark_equity(i)
            if current_eq <= 0:
                # liquidation
                positions.clear()
                cash = 0.0
                equity_curve.append(0.0)
                if equity_curve and len(equity_curve) > 1:
                    returns.append(-1.0)
                break
            max_positions = len(SYMBOLS)
            target_notional = (current_eq * LEVERAGE) / max_positions
            for s in SYMBOLS:
                if s in positions:
                    continue
                j = idx_by_sym[s].get(date)
                if j is None or j < 25:
                    continue
                bars_s = data[s]
                hi20 = max(bars_s[k]["high"] for k in range(j - 20, j))
                mom20 = bars_s[j]["close"] / bars_s[j - 20]["close"] - 1.0
                if bars_s[j]["close"] > hi20 and mom20 > 0.02:
                    price = bars_s[j]["close"]
                    units = target_notional / price
                    fee = units * price * FEE
                    cash -= fee
                    a = atr(bars_s, 14, j) or price * 0.03
                    positions[s] = {
                        "units": units,
                        "entry": price,
                        "highest": price,
                        "stop": price - 3.0 * a,
                        "pyramided": False,
                    }
                    trades.append({"date": date, "symbol": s, "side": "BUY",
                                   "price": price, "pnl": 0.0, "reason": "breakout"})

        eq = mark_equity(i)
        if equity_curve:
            prev = equity_curve[-1]
            if prev > 0:
                returns.append(eq / prev - 1.0)
        equity_curve.append(eq)

    # Close any remaining
    if positions:
        last_i = len(btc) - 1
        date = dates[last_i]
        for s, p in positions.items():
            j = idx_by_sym[s].get(date, None)
            if j is None:
                continue
            price = data[s][j]["close"]
            pnl = p["units"] * (price - p["entry"])
            fee = abs(p["units"] * price) * FEE
            cash += pnl - fee
            trades.append({"date": date, "symbol": s, "side": "SELL",
                           "price": price, "pnl": pnl - fee, "reason": "eod"})
        positions.clear()
        equity_curve[-1] = cash

    final_eq = equity_curve[-1] if equity_curve else initial_capital
    total_return_pct = (final_eq / initial_capital - 1.0) * 100.0
    # Sharpe
    if len(returns) > 1:
        mean = sum(returns) / len(returns)
        var = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
        sd = math.sqrt(var) if var > 0 else 0.0
        sharpe = (mean / sd * math.sqrt(365)) if sd > 0 else 0.0
    else:
        sharpe = 0.0
    # Max DD
    peak = -1e18
    mdd = 0.0
    for v in equity_curve:
        if v > peak:
            peak = v
        if peak > 0:
            dd = (peak - v) / peak
            if dd > mdd:
                mdd = dd
    closed = [t for t in trades if t["side"] == "SELL"]
    wins = sum(1 for t in closed if t["pnl"] > 0)
    win_rate = (wins / len(closed) * 100.0) if closed else 0.0

    return {
        "final_equity": round(final_eq, 2),
        "total_return_pct": round(total_return_pct, 2),
        "sharpe_ratio": round(sharpe, 3),
        "max_drawdown_pct": round(mdd * 100, 2),
        "num_trades": len(closed),
        "win_rate": round(win_rate, 2),
        "trades": trades,
    }


if __name__ == "__main__":
    import sys
    from pathlib import Path

    out_dir = Path(__file__).parent
    lines: list[str] = []

    for label, start, end in [
        ("TRAIN", "2024-01-01", "2024-12-31"),
        ("TEST", "2025-01-01", "2025-06-30"),
    ]:
        print(f"Running {label} {start} -> {end}...", file=sys.stderr)
        r = run_backtest(start, end, 1000.0)
        lines.append("=" * 60)
        lines.append(f"AGENT 3 - MACRO STRATEGIST - ROUND 10 - {label}")
        lines.append(f"Period: {start} to {end}")
        lines.append("-" * 60)
        for k in ("final_equity", "total_return_pct", "sharpe_ratio",
                  "max_drawdown_pct", "num_trades", "win_rate"):
            lines.append(f"  {k}: {r[k]}")
        lines.append("")
        lines.append("Trades:")
        for t in r["trades"][:80]:
            lines.append(
                f"  {t['date']}  {t['symbol']:8s} {t['side']:4s} "
                f"${t['price']:12.2f}  pnl=${t['pnl']:8.2f}  {t['reason']}"
            )
        lines.append("")

    (out_dir / "results.txt").write_text("\n".join(lines))
    print("\n".join(lines))
