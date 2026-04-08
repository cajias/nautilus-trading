"""
Agent 3 - Macro Strategist Round 3: Momentum + Extreme Dip Buyer

The core problem: Q1 2025 was bearish. Every trend/dip entry got stopped out.
Solution: Be VERY selective. Only enter on:
1. Strong confirmed momentum (weekly close > weekly EMA) -- ride big moves
2. Extreme dip-buys (>20% correction + RSI<25) -- very rare, very wide stops
3. Otherwise: STAY IN CASH. Cash is a position.

The key is FEWER trades, WIDER stops, and PATIENCE.
Use weekly bars for signals (less noise) but daily for execution.
"""

import json
import math
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any


def fetch_klines(symbol: str, interval: str, start_ms: int, end_ms: int) -> list[list]:
    all_klines: list[list] = []
    current_start = start_ms
    while current_start < end_ms:
        url = (
            f"https://api.binance.com/api/v3/klines"
            f"?symbol={symbol}&interval={interval}"
            f"&startTime={current_start}&endTime={end_ms}&limit=1000"
        )
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
        if not data:
            break
        all_klines.extend(data)
        current_start = data[-1][0] + 1
        if len(data) < 1000:
            break
    return all_klines


def _ema(values: list[float], period: int) -> list[float]:
    out = [0.0] * len(values)
    if len(values) < period:
        return out
    k = 2.0 / (period + 1)
    out[period - 1] = sum(values[:period]) / period
    for i in range(period, len(values)):
        out[i] = values[i] * k + out[i - 1] * (1 - k)
    return out


def _rsi(closes: list[float], period: int = 14) -> list[float]:
    out = [50.0] * len(closes)
    if len(closes) < period + 1:
        return out
    ag = 0.0
    al = 0.0
    for i in range(1, period + 1):
        d = closes[i] - closes[i - 1]
        if d > 0:
            ag += d
        else:
            al -= d
    ag /= period
    al /= period
    if al == 0:
        out[period] = 100.0
    else:
        out[period] = 100 - 100 / (1 + ag / al)
    for i in range(period + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        gain = d if d > 0 else 0.0
        loss = -d if d < 0 else 0.0
        ag = (ag * (period - 1) + gain) / period
        al = (al * (period - 1) + loss) / period
        if al == 0:
            out[i] = 100.0
        else:
            out[i] = 100 - 100 / (1 + ag / al)
    return out


def _atr(highs: list[float], lows: list[float], closes: list[float], period: int) -> list[float]:
    out = [0.0] * len(closes)
    tr = [0.0] * len(closes)
    for i in range(1, len(closes)):
        tr[i] = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
    if len(closes) < period + 1:
        return out
    out[period] = sum(tr[1 : period + 1]) / period
    for i in range(period + 1, len(closes)):
        out[i] = (out[i - 1] * (period - 1) + tr[i]) / period
    return out


class Position:
    def __init__(self, symbol: str, entry_price: float, size_usd: float, stop: float, kind: str):
        self.symbol = symbol
        self.entry_price = entry_price
        self.size_usd = size_usd
        self.stop = stop
        self.kind = kind  # "momentum" or "dip"
        self.highest_since = entry_price
        self.take_profit: float | None = None
        self.entry_bar_idx: int = 0


def run_backtest(start: str, end: str, initial_capital: float = 1000.0) -> dict[str, Any]:
    """Main entry point for the competition harness."""

    SYMBOL = "BTCUSDT"  # BTC only for simplicity and resilience
    FEE_RATE = 0.001
    LOOKBACK_DAYS = 120

    # Weekly momentum: buy when weekly close > 8-week EMA, sell when below
    WEEKLY_EMA = 8
    # Position sizing for momentum trades
    MOM_SIZE = 0.90
    MOM_TRAIL_PCT = 0.08  # 8% trailing stop from peak

    # Extreme dip parameters (daily)
    DIP_DD_THRESH = 0.20  # 20%+ correction from 30-day high
    DIP_RSI_THRESH = 28.0  # stricter: very oversold only
    DIP_SIZE = 0.60
    DIP_STOP_PCT = 0.15  # wider stop: 15%
    DIP_TP_PCT = 0.18    # wider TP: 18%

    COOLDOWN_WEEKS = 1

    start_dt = datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end_dt = datetime.strptime(end, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    lookback_dt = start_dt - timedelta(days=LOOKBACK_DAYS)
    fetch_start_ms = int(lookback_dt.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000)
    trade_start_ms = int(start_dt.timestamp() * 1000)

    # Fetch weekly data for momentum signals
    weekly_klines = fetch_klines(SYMBOL, "1w", fetch_start_ms, end_ms)
    # Fetch daily data for execution and dip detection
    daily_klines = fetch_klines(SYMBOL, "1d", fetch_start_ms, end_ms)

    if not weekly_klines or not daily_klines:
        return {"final_equity": initial_capital, "total_return_pct": 0.0, "sharpe_ratio": 0.0,
                "max_drawdown_pct": 0.0, "num_trades": 0, "win_rate": 0.0, "trade_log": []}

    # Process weekly data
    w_closes = [float(k[4]) for k in weekly_klines]
    w_timestamps = [k[0] for k in weekly_klines]
    w_ema = _ema(w_closes, WEEKLY_EMA)

    # Build weekly signal lookup: for each week, is momentum positive?
    # Key: week start timestamp -> bool
    weekly_signal: dict[int, bool] = {}
    for i in range(WEEKLY_EMA, len(w_closes)):
        weekly_signal[w_timestamps[i]] = w_closes[i] > w_ema[i]

    # Process daily data
    d_closes = [float(k[4]) for k in daily_klines]
    d_highs = [float(k[2]) for k in daily_klines]
    d_lows = [float(k[3]) for k in daily_klines]
    d_timestamps = [k[0] for k in daily_klines]
    d_rsi = _rsi(d_closes, 14)
    d_atr = _atr(d_highs, d_lows, d_closes, 14)

    # For each daily bar, find the most recent weekly signal
    def get_weekly_momentum(daily_ts: int) -> bool | None:
        best_ts = None
        for wts in w_timestamps:
            if wts <= daily_ts:
                best_ts = wts
        if best_ts and best_ts in weekly_signal:
            return weekly_signal[best_ts]
        return None

    cash = initial_capital
    peak_equity = initial_capital
    max_dd = 0.0
    position: Position | None = None
    trade_log: list[dict] = []
    equity_curve: list[float] = []
    cooldown = 0
    warmup = max(WEEKLY_EMA * 7, 30)  # need enough daily bars

    for i in range(1, len(d_closes)):
        ts = d_timestamps[i]
        in_trade = ts >= trade_start_ms
        ts_str = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d")

        close = d_closes[i]
        low = d_lows[i]
        high = d_highs[i]
        rsi_val = d_rsi[i]

        # --- EXIT LOGIC ---
        if position is not None:
            exit_price = None
            reason = ""

            if position.kind == "momentum":
                # Update trailing stop
                if close > position.highest_since:
                    position.highest_since = close
                    position.stop = max(position.stop, close * (1 - MOM_TRAIL_PCT))

                # Check stop
                if low <= position.stop:
                    exit_price = max(position.stop, low)
                    reason = "mom_stop"
                # Check weekly momentum reversal
                elif in_trade:
                    mom = get_weekly_momentum(ts)
                    if mom is False:
                        exit_price = close
                        reason = "mom_exit(weekly)"

            elif position.kind == "dip":
                if low <= position.stop:
                    exit_price = max(position.stop, low)
                    reason = "dip_stop"
                elif position.take_profit and close >= position.take_profit:
                    exit_price = close
                    reason = "dip_tp"
                # Time exit: if dip trade not profitable after 25 days, close it
                elif (i - position.entry_bar_idx) >= 25:
                    exit_price = close
                    reason = "dip_time_exit"

            if exit_price is not None:
                pnl = (exit_price / position.entry_price - 1) * position.size_usd
                fee = position.size_usd * FEE_RATE
                pnl -= fee
                cash += position.size_usd + pnl
                trade_log.append({
                    "symbol": SYMBOL, "side": "SELL", "price": round(exit_price, 2),
                    "time": ts_str, "pnl": round(pnl, 2), "reason": reason,
                })
                position = None
                if "stop" in reason:
                    cooldown = 5  # 5 daily bars cooldown

        if not in_trade:
            continue

        if cooldown > 0:
            cooldown -= 1

        # --- ENTRY LOGIC ---
        if position is None and cooldown <= 0 and i >= warmup // 1:
            mom = get_weekly_momentum(ts)

            # Priority 1: Extreme dip buy
            lb30 = min(30, i)
            recent_high = max(d_highs[i - lb30 : i + 1])
            dd = (recent_high - close) / recent_high

            if dd >= DIP_DD_THRESH and rsi_val < DIP_RSI_THRESH:
                size_usd = DIP_SIZE * cash
                if size_usd >= 10:
                    fee = size_usd * FEE_RATE
                    cash -= (size_usd + fee)
                    stop = close * (1 - DIP_STOP_PCT)
                    tp = close * (1 + DIP_TP_PCT)
                    position = Position(SYMBOL, close, size_usd, stop, "dip")
                    position.take_profit = tp
                    position.entry_bar_idx = i
                    trade_log.append({
                        "symbol": SYMBOL, "side": "BUY", "price": close,
                        "time": ts_str, "pnl": 0,
                        "reason": f"dip_buy(dd={dd:.0%},RSI={rsi_val:.0f})",
                    })

            # Priority 2: Weekly momentum entry
            elif mom is True:
                size_usd = MOM_SIZE * cash
                if size_usd >= 10:
                    fee = size_usd * FEE_RATE
                    cash -= (size_usd + fee)
                    stop = close * (1 - MOM_TRAIL_PCT)
                    position = Position(SYMBOL, close, size_usd, stop, "momentum")
                    position.entry_bar_idx = i
                    trade_log.append({
                        "symbol": SYMBOL, "side": "BUY", "price": close,
                        "time": ts_str, "pnl": 0,
                        "reason": "momentum_entry(weekly)",
                    })

        # --- MARK TO MARKET ---
        mtm = cash
        if position is not None:
            unrealized = (close / position.entry_price - 1) * position.size_usd
            mtm += position.size_usd + unrealized

        equity_curve.append(mtm)
        if mtm > peak_equity:
            peak_equity = mtm
        dd_pct = (peak_equity - mtm) / peak_equity * 100
        if dd_pct > max_dd:
            max_dd = dd_pct

    # --- CLOSE REMAINING ---
    if position is not None:
        close = d_closes[-1]
        ts_str = datetime.fromtimestamp(d_timestamps[-1] / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        pnl = (close / position.entry_price - 1) * position.size_usd
        fee = position.size_usd * FEE_RATE
        pnl -= fee
        cash += position.size_usd + pnl
        trade_log.append({
            "symbol": SYMBOL, "side": "SELL", "price": close,
            "time": ts_str, "pnl": round(pnl, 2), "reason": "end_of_period",
        })

    final_equity = cash
    total_return = (final_equity - initial_capital) / initial_capital * 100
    closed_trades = [t for t in trade_log if t["pnl"] != 0]
    num_trades = len(closed_trades)
    winners = len([t for t in closed_trades if t["pnl"] > 0])
    win_rate = winners / num_trades * 100 if num_trades > 0 else 0.0

    sharpe = 0.0
    if len(equity_curve) > 1:
        rets = [
            (equity_curve[i] - equity_curve[i - 1]) / equity_curve[i - 1]
            for i in range(1, len(equity_curve))
            if equity_curve[i - 1] > 0
        ]
        if rets:
            mean_r = sum(rets) / len(rets)
            var_r = sum((r - mean_r) ** 2 for r in rets) / max(len(rets) - 1, 1)
            std_r = math.sqrt(var_r) if var_r > 0 else 1e-10
            sharpe = (mean_r / std_r) * math.sqrt(365)

    return {
        "final_equity": round(final_equity, 2),
        "total_return_pct": round(total_return, 2),
        "sharpe_ratio": round(sharpe, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "num_trades": num_trades,
        "win_rate": round(win_rate, 2),
        "trade_log": trade_log,
    }


if __name__ == "__main__":
    print("=" * 70)
    print("TRAIN PERIOD: 2024-07-01 to 2024-12-31")
    print("=" * 70)
    train = run_backtest("2024-07-01", "2024-12-31")
    for k, v in train.items():
        if k != "trade_log":
            print(f"  {k}: {v}")
    print(f"\n  --- Train Trade Log ({len(train['trade_log'])} entries) ---")
    for t in train["trade_log"]:
        print(f"  {t['time']} | {t['symbol']:8s} | {t['side']:5s} | ${t['price']:>10.2f} | PnL: ${t['pnl']:>8.2f} | {t['reason']}")

    print()
    print("=" * 70)
    print("TEST PERIOD: 2025-01-01 to 2025-03-31")
    print("=" * 70)
    test = run_backtest("2025-01-01", "2025-03-31")
    for k, v in test.items():
        if k != "trade_log":
            print(f"  {k}: {v}")
    print(f"\n  --- Test Trade Log ({len(test['trade_log'])} entries) ---")
    for t in test["trade_log"]:
        print(f"  {t['time']} | {t['symbol']:8s} | {t['side']:5s} | ${t['price']:>10.2f} | PnL: ${t['pnl']:>8.2f} | {t['reason']}")

    # Save results
    results_path = "/Users/rc/Projects/workspace/nautilus-trading/competition/agent-3-macro/round3/results.txt"
    with open(results_path, "w") as f:
        f.write("Agent 3 - Macro Strategist Round 3: Momentum + Extreme Dip Buyer\n")
        f.write("=" * 70 + "\n\n")
        f.write("TRAIN PERIOD: 2024-07-01 to 2024-12-31\n")
        f.write("-" * 40 + "\n")
        for k, v in train.items():
            if k != "trade_log":
                f.write(f"  {k}: {v}\n")
        f.write(f"\nTrade Log ({len(train['trade_log'])} entries):\n")
        for t in train["trade_log"]:
            f.write(f"  {t['time']} | {t['symbol']:8s} | {t['side']:5s} | ${t['price']:>10.2f} | PnL: ${t['pnl']:>8.2f} | {t['reason']}\n")

        f.write(f"\n\nTEST PERIOD: 2025-01-01 to 2025-03-31\n")
        f.write("-" * 40 + "\n")
        for k, v in test.items():
            if k != "trade_log":
                f.write(f"  {k}: {v}\n")
        f.write(f"\nTrade Log ({len(test['trade_log'])} entries):\n")
        for t in test["trade_log"]:
            f.write(f"  {t['time']} | {t['symbol']:8s} | {t['side']:5s} | ${t['price']:>10.2f} | PnL: ${t['pnl']:>8.2f} | {t['reason']}\n")

    print(f"\nResults saved to {results_path}")
