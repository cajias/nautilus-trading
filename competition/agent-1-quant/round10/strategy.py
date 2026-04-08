"""
Round 10 FINAL — Agent 1 (Quant)
Bold all-in momentum strategy: BTC daily trend-following with aggressive
re-entry on strength. Single-asset, long-only, full capital deployment.

Thesis: Across 10 rounds the biggest winners were simple, concentrated
BTC long exposures during trending regimes (Agent 4 R1 +48%, Agent 2 R8
+25%). Diversification and many parameters have hurt. Going simple and
bold: ride the trend with max capital when price is above a fast+slow
EMA stack and 20d momentum is positive. Exit on trend break. No stops
to avoid whipsaw — rely on trend filter.
"""
from __future__ import annotations

import math
import time
import urllib.request
import json
from datetime import datetime, timezone


BINANCE_KLINES = "https://api.binance.com/api/v3/klines"


def _to_ms(s: str) -> int:
    dt = datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def fetch_klines(symbol: str, interval: str, start: str, end: str) -> list[list]:
    start_ms = _to_ms(start)
    end_ms = _to_ms(end) + 86_400_000
    out: list[list] = []
    cur = start_ms
    while cur < end_ms:
        url = (
            f"{BINANCE_KLINES}?symbol={symbol}&interval={interval}"
            f"&startTime={cur}&endTime={end_ms}&limit=1000"
        )
        for attempt in range(5):
            try:
                with urllib.request.urlopen(url, timeout=30) as r:
                    batch = json.loads(r.read().decode())
                break
            except Exception:
                time.sleep(1 + attempt)
        else:
            raise RuntimeError(f"failed {url}")
        if not batch:
            break
        out.extend(batch)
        last = batch[-1][0]
        if last <= cur:
            break
        cur = last + 1
        if len(batch) < 1000:
            break
        time.sleep(0.12)
    return out


def _ema(vals: list[float], period: int) -> list[float]:
    k = 2.0 / (period + 1.0)
    out = [vals[0]]
    for v in vals[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def _fetch_closes(symbol: str, start: str, end: str):
    kl = fetch_klines(symbol, "1d", start, end)
    ts = [k[0] for k in kl]
    closes = [float(k[4]) for k in kl]
    highs = [float(k[2]) for k in kl]
    lows = [float(k[3]) for k in kl]
    return ts, closes, highs, lows


def run_backtest(start: str, end: str, initial_capital: float = 1000.0) -> dict:
    # Buffer for indicator warmup
    from datetime import timedelta
    sdt = datetime.strptime(start, "%Y-%m-%d") - timedelta(days=80)
    buf_start = sdt.strftime("%Y-%m-%d")

    ts, closes, highs, lows = _fetch_closes("BTCUSDT", buf_start, end)
    if len(closes) < 60:
        return _empty(initial_capital)

    ema_fast = _ema(closes, 10)
    ema_slow = _ema(closes, 30)

    eval_start_ms = _to_ms(start)

    equity = initial_capital
    position = 0.0  # BTC held
    entry_price = 0.0
    peak_equity = initial_capital
    max_dd = 0.0
    trades: list[dict] = []
    daily_returns: list[float] = []
    prev_eq = initial_capital
    fee = 0.001  # 10bps per side

    for i in range(30, len(closes)):
        t = ts[i]
        price = closes[i]
        if t < eval_start_ms:
            # still warmup region within buffer; skip equity tracking
            continue

        # Trend signals (use previous bar to avoid lookahead)
        ef_prev, es_prev = ema_fast[i - 1], ema_slow[i - 1]
        price_prev = closes[i - 1]
        mom20 = (closes[i - 1] / closes[i - 21] - 1.0) if i >= 21 else 0.0

        # Long regime: fast above slow AND price above fast AND positive 20d momentum
        bullish = (ef_prev > es_prev) and (price_prev > ef_prev) and (mom20 > 0)

        # Execute at today's open ~ today's close proxy (use today's close as exec price; conservative)
        if position == 0.0 and bullish:
            # Enter full size
            notional = equity * (1 - fee)
            position = notional / price
            entry_price = price
            equity = 0.0
        elif position > 0.0 and not bullish:
            # Exit
            proceeds = position * price * (1 - fee)
            pnl = proceeds - (position * entry_price)
            trades.append({"entry": entry_price, "exit": price, "pnl": pnl})
            equity = proceeds
            position = 0.0
            entry_price = 0.0

        # Mark-to-market equity
        mtm = equity + position * price
        if mtm > peak_equity:
            peak_equity = mtm
        dd = (mtm - peak_equity) / peak_equity * 100.0
        if dd < max_dd:
            max_dd = dd
        if prev_eq > 0:
            daily_returns.append(mtm / prev_eq - 1.0)
        prev_eq = mtm

    # Close any open position at final price
    if position > 0.0:
        price = closes[-1]
        proceeds = position * price * (1 - fee)
        pnl = proceeds - (position * entry_price)
        trades.append({"entry": entry_price, "exit": price, "pnl": pnl})
        equity = proceeds
        position = 0.0

    final_equity = equity
    total_return_pct = (final_equity / initial_capital - 1.0) * 100.0

    # Sharpe (daily, annualized)
    if len(daily_returns) > 1:
        mean = sum(daily_returns) / len(daily_returns)
        var = sum((r - mean) ** 2 for r in daily_returns) / (len(daily_returns) - 1)
        std = math.sqrt(var)
        sharpe = (mean / std * math.sqrt(365)) if std > 0 else 0.0
    else:
        sharpe = 0.0

    wins = sum(1 for t in trades if t["pnl"] > 0)
    win_rate = (wins / len(trades) * 100.0) if trades else 0.0

    return {
        "final_equity": round(final_equity, 2),
        "total_return_pct": round(total_return_pct, 2),
        "sharpe_ratio": round(sharpe, 3),
        "max_drawdown_pct": round(max_dd, 2),
        "num_trades": len(trades),
        "win_rate": round(win_rate, 1),
    }


def _empty(cap: float) -> dict:
    return {
        "final_equity": cap,
        "total_return_pct": 0.0,
        "sharpe_ratio": 0.0,
        "max_drawdown_pct": 0.0,
        "num_trades": 0,
        "win_rate": 0.0,
    }


if __name__ == "__main__":
    import sys
    train = run_backtest("2024-01-01", "2024-12-31", 1000.0)
    test = run_backtest("2025-01-01", "2025-06-30", 1000.0)
    print("TRAIN 2024:", train)
    print("TEST H1-2025:", test)
    out = (
        "Round 10 FINAL — Agent 1 (Quant)\n"
        "Strategy: BTC daily EMA10>EMA30 + price>EMA10 + 20d momentum>0, full-size long\n"
        f"TRAIN (2024): {train}\n"
        f"TEST  (H1 2025): {test}\n"
    )
    with open(
        "/Users/rc/Projects/workspace/nautilus-trading/competition/agent-1-quant/round10/results.txt",
        "w",
    ) as f:
        f.write(out)
    print(out)
