"""
Agent 1 — Quantitative Trader | Round 2
Long-Only Trend Following with Donchian Breakout + Momentum

Key design:
- LONG ONLY: crypto has structural upward drift; shorts killed Round 1 performance
- Donchian 20-day breakout entry, with momentum confirmation (close > close 10 days ago)
- Wide ATR trailing stop (4x) to ride big moves without getting shaken out
- 10-day Donchian low as secondary exit
- Multi-asset: BTC 50%, ETH 30%, SOL 20% for diversification + beta capture
- Position sizing: 3% risk per trade, volatility-scaled
- Re-entry allowed after pullbacks (no "one and done" per trend)
"""

import json
import math
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any


def fetch_klines(
    symbol: str,
    interval: str = "1d",
    start_ms: int = 0,
    end_ms: int = 0,
    limit: int = 1000,
) -> list[dict]:
    """Fetch klines from Binance public API."""
    all_klines = []
    current_start = start_ms

    while current_start < end_ms:
        url = (
            f"https://api.binance.com/api/v3/klines"
            f"?symbol={symbol}&interval={interval}"
            f"&startTime={current_start}&endTime={end_ms}&limit={limit}"
        )
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode())

        if not data:
            break

        for k in data:
            all_klines.append(
                {
                    "open_time": k[0],
                    "open": float(k[1]),
                    "high": float(k[2]),
                    "low": float(k[3]),
                    "close": float(k[4]),
                    "volume": float(k[5]),
                    "close_time": k[6],
                }
            )

        current_start = data[-1][6] + 1
        if len(data) < limit:
            break

    return all_klines


def ema(values: list[float], period: int) -> list[float]:
    """Exponential moving average."""
    result = [0.0] * len(values)
    if len(values) < period:
        return result
    k = 2.0 / (period + 1)
    result[period - 1] = sum(values[:period]) / period
    for i in range(period, len(values)):
        result[i] = values[i] * k + result[i - 1] * (1 - k)
    return result


def atr(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> list[float]:
    """Average True Range using Wilder's smoothing."""
    n = len(closes)
    tr = [0.0] * n
    tr[0] = highs[0] - lows[0]
    for i in range(1, n):
        tr[i] = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
    result = [0.0] * n
    if n < period:
        return result
    result[period - 1] = sum(tr[:period]) / period
    for i in range(period, n):
        result[i] = (result[i - 1] * (period - 1) + tr[i]) / period
    return result


def donchian_high(highs: list[float], period: int) -> list[float]:
    """Highest high over period."""
    n = len(highs)
    result = [0.0] * n
    for i in range(period - 1, n):
        result[i] = max(highs[i - period + 1 : i + 1])
    return result


def donchian_low(lows: list[float], period: int) -> list[float]:
    """Lowest low over period."""
    n = len(lows)
    result = [0.0] * n
    for i in range(period - 1, n):
        result[i] = min(lows[i - period + 1 : i + 1])
    return result


class Position:
    def __init__(self, symbol: str, entry_price: float, size: float, entry_time: int):
        self.symbol = symbol
        self.entry_price = entry_price
        self.size = size
        self.entry_time = entry_time
        self.trailing_stop: float = 0.0
        self.highest_since_entry: float = entry_price


def run_backtest(
    start: str,
    end: str,
    initial_capital: float = 1000.0,
) -> dict[str, Any]:
    """
    Long-only trend following with Donchian breakout + momentum filter.

    Daily candles on BTC, ETH, SOL.
    Entry: Close > 20-day Donchian high AND close > close[10] (momentum)
           AND close > 50-EMA (trend filter)
    Exit: 4x ATR trailing stop OR close < 10-day Donchian low
    """
    start_dt = datetime.strptime(start, "%Y-%m-%d")
    end_dt = datetime.strptime(end, "%Y-%m-%d")

    lookback_days = 80
    fetch_start = start_dt - timedelta(days=lookback_days)

    start_ms = int(fetch_start.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000)

    symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    all_data: dict[str, list[dict]] = {}

    for sym in symbols:
        print(f"Fetching {sym} daily klines...")
        all_data[sym] = fetch_klines(sym, "1d", start_ms, end_ms)
        print(f"  Got {len(all_data[sym])} candles")

    # Parameters
    entry_channel = 20        # Donchian breakout period
    exit_channel = 10         # Donchian exit period
    trend_ema_period = 50     # Trend filter EMA
    momentum_lookback = 10    # Momentum confirmation (close > close N days ago)
    atr_period = 20
    trailing_atr_mult = 4.0   # Wide trailing stop — let winners run
    risk_per_trade = 0.03     # 3% equity risk per trade
    fee_rate = 0.001          # 0.1%
    max_position_pct = 0.45   # Max 45% equity per position

    # Allocation weights
    weights = {"BTCUSDT": 0.50, "ETHUSDT": 0.30, "SOLUSDT": 0.20}

    equity = initial_capital
    cash = initial_capital  # Track cash separately for position management
    peak_equity = initial_capital
    max_drawdown_pct = 0.0
    trade_log: list[dict] = []
    positions: dict[str, Position | None] = {s: None for s in symbols}

    # Pre-compute indicators
    indicators: dict[str, dict] = {}
    actual_start_ms = int(start_dt.timestamp() * 1000)

    for sym in symbols:
        klines = all_data[sym]
        closes = [k["close"] for k in klines]
        highs = [k["high"] for k in klines]
        lows = [k["low"] for k in klines]

        indicators[sym] = {
            "klines": klines,
            "closes": closes,
            "highs": highs,
            "lows": lows,
            "ema50": ema(closes, trend_ema_period),
            "donch_high": donchian_high(highs, entry_channel),
            "donch_low": donchian_low(lows, entry_channel),
            "exit_donch_low": donchian_low(lows, exit_channel),
            "atr": atr(highs, lows, closes, atr_period),
        }

    # Use BTCUSDT as time reference
    ref_klines = all_data["BTCUSDT"]
    warmup = max(entry_channel, trend_ema_period, atr_period, momentum_lookback) + 5

    for i in range(warmup, len(ref_klines)):
        bar_time = ref_klines[i]["open_time"]
        if bar_time < actual_start_ms:
            continue

        for sym in symbols:
            ind = indicators[sym]
            if i >= len(ind["klines"]):
                continue

            k = ind["klines"][i]
            close = ind["closes"][i]
            high = ind["highs"][i]
            low = ind["lows"][i]
            atr_val = ind["atr"][i]
            ema50 = ind["ema50"][i]

            # Previous bar's channels (no lookahead)
            prev_donch_high = ind["donch_high"][i - 1]
            prev_exit_donch_low = ind["exit_donch_low"][i - 1]

            # Momentum: close vs N bars ago
            momentum_close = ind["closes"][i - momentum_lookback] if i >= momentum_lookback else close

            pos = positions[sym]

            # --- EXIT LOGIC ---
            if pos is not None:
                pos.highest_since_entry = max(pos.highest_since_entry, high)
                atr_stop = pos.highest_since_entry - trailing_atr_mult * atr_val
                pos.trailing_stop = max(pos.trailing_stop, atr_stop)

                # Use the higher of trailing stop and exit channel
                effective_stop = max(pos.trailing_stop, prev_exit_donch_low)

                exit_price = None
                exit_reason = ""

                if low <= effective_stop:
                    exit_price = max(effective_stop, low)
                    exit_reason = "trailing_stop" if effective_stop == pos.trailing_stop else "channel_exit"

                if exit_price is not None:
                    pnl_gross = (exit_price - pos.entry_price) * pos.size
                    fees = fee_rate * (exit_price * pos.size + pos.entry_price * pos.size)
                    pnl_net = pnl_gross - fees

                    trade_log.append(
                        {
                            "symbol": sym,
                            "side": "long",
                            "entry_price": round(pos.entry_price, 2),
                            "exit_price": round(exit_price, 2),
                            "size": round(pos.size, 6),
                            "pnl": round(pnl_net, 2),
                            "return_pct": round(pnl_net / (pos.entry_price * pos.size) * 100, 2),
                            "entry_time": datetime.fromtimestamp(pos.entry_time / 1000, tz=timezone.utc).strftime("%Y-%m-%d"),
                            "exit_time": datetime.fromtimestamp(k["open_time"] / 1000, tz=timezone.utc).strftime("%Y-%m-%d"),
                            "exit_reason": exit_reason,
                            "hold_days": round((k["open_time"] - pos.entry_time) / 86400000, 1),
                        }
                    )
                    # Return capital + PnL to cash
                    cash += pos.entry_price * pos.size + pnl_net
                    positions[sym] = None

            # --- ENTRY LOGIC ---
            pos = positions[sym]
            if pos is not None:
                continue
            if atr_val <= 0:
                continue

            # Calculate current total equity for sizing
            total_equity = cash
            for s2 in symbols:
                p2 = positions[s2]
                if p2 is not None:
                    idx2 = min(i, len(indicators[s2]["closes"]) - 1)
                    c2 = indicators[s2]["closes"][idx2]
                    total_equity += c2 * p2.size  # Mark to market

            alloc_equity = total_equity * weights[sym]
            stop_distance = trailing_atr_mult * atr_val
            risk_amount = total_equity * risk_per_trade
            size = risk_amount / stop_distance
            max_size = min(alloc_equity * max_position_pct, cash * 0.9) / close
            size = min(size, max_size)

            if size * close < 10 or size * close > cash * 0.95:
                continue

            # LONG: breakout + momentum + trend
            if (
                close > prev_donch_high  # Breakout above 20-day channel
                and close > momentum_close  # Momentum confirmation
                and close > ema50  # Above trend
            ):
                cost = close * size
                entry_fee = fee_rate * cost
                cash -= (cost + entry_fee)

                positions[sym] = Position(
                    symbol=sym,
                    entry_price=close,
                    size=size,
                    entry_time=k["open_time"],
                )
                positions[sym].trailing_stop = close - stop_distance

        # --- DRAWDOWN TRACKING ---
        total_equity = cash
        for s2 in symbols:
            p2 = positions[s2]
            if p2 is not None:
                idx2 = min(i, len(indicators[s2]["closes"]) - 1)
                c2 = indicators[s2]["closes"][idx2]
                total_equity += c2 * p2.size

        if total_equity > peak_equity:
            peak_equity = total_equity
        dd = (peak_equity - total_equity) / peak_equity * 100
        if dd > max_drawdown_pct:
            max_drawdown_pct = dd

    # Close any open positions at end
    for sym in symbols:
        pos = positions[sym]
        if pos is not None:
            ind = indicators[sym]
            final_close = ind["closes"][-1]
            pnl_gross = (final_close - pos.entry_price) * pos.size
            fees = fee_rate * (final_close * pos.size + pos.entry_price * pos.size)
            pnl_net = pnl_gross - fees
            cash += pos.entry_price * pos.size + pnl_net

            trade_log.append(
                {
                    "symbol": sym,
                    "side": "long",
                    "entry_price": round(pos.entry_price, 2),
                    "exit_price": round(final_close, 2),
                    "size": round(pos.size, 6),
                    "pnl": round(pnl_net, 2),
                    "return_pct": round(pnl_net / (pos.entry_price * pos.size) * 100, 2),
                    "entry_time": datetime.fromtimestamp(pos.entry_time / 1000, tz=timezone.utc).strftime("%Y-%m-%d"),
                    "exit_time": "end_of_period",
                    "exit_reason": "period_end",
                    "hold_days": round((int(end_dt.timestamp() * 1000) - pos.entry_time) / 86400000, 1),
                }
            )
            positions[sym] = None

    # Final equity
    equity = cash
    total_return_pct = (equity - initial_capital) / initial_capital * 100
    num_trades = len(trade_log)
    wins = [t for t in trade_log if t["pnl"] > 0]
    win_rate = len(wins) / num_trades * 100 if num_trades > 0 else 0

    # Sharpe ratio
    if num_trades > 1:
        returns = [t["return_pct"] / 100 for t in trade_log]
        mean_ret = sum(returns) / len(returns)
        var_ret = sum((r - mean_ret) ** 2 for r in returns) / (len(returns) - 1)
        std_ret = math.sqrt(var_ret) if var_ret > 0 else 1e-10
        days = (end_dt - start_dt).days
        trades_per_year = num_trades / (days / 365.25)
        sharpe_ratio = (mean_ret / std_ret) * math.sqrt(trades_per_year)
    else:
        sharpe_ratio = 0.0

    if equity > peak_equity:
        peak_equity = equity
    dd = (peak_equity - equity) / peak_equity * 100
    if dd > max_drawdown_pct:
        max_drawdown_pct = dd

    return {
        "final_equity": round(equity, 2),
        "total_return_pct": round(total_return_pct, 2),
        "sharpe_ratio": round(sharpe_ratio, 2),
        "max_drawdown_pct": round(max_drawdown_pct, 2),
        "num_trades": num_trades,
        "win_rate": round(win_rate, 2),
        "trade_log": trade_log,
    }


if __name__ == "__main__":
    print("=" * 60)
    print("TRAIN PERIOD: 2024-04-01 to 2024-09-30")
    print("=" * 60)
    train_results = run_backtest("2024-04-01", "2024-09-30")
    print(f"Final Equity:    ${train_results['final_equity']}")
    print(f"Total Return:    {train_results['total_return_pct']}%")
    print(f"Sharpe Ratio:    {train_results['sharpe_ratio']}")
    print(f"Max Drawdown:    {train_results['max_drawdown_pct']}%")
    print(f"Num Trades:      {train_results['num_trades']}")
    print(f"Win Rate:        {train_results['win_rate']}%")
    print("\nTrade details:")
    for t in train_results["trade_log"]:
        print(f"  {t['entry_time']} -> {t['exit_time']} | {t['symbol']} {t['side']} "
              f"| ${t['pnl']} ({t['return_pct']}%) | {t['exit_reason']} | held {t['hold_days']}d")
    print()

    print("=" * 60)
    print("TEST PERIOD: 2024-10-01 to 2024-12-31")
    print("=" * 60)
    test_results = run_backtest("2024-10-01", "2024-12-31")
    print(f"Final Equity:    ${test_results['final_equity']}")
    print(f"Total Return:    {test_results['total_return_pct']}%")
    print(f"Sharpe Ratio:    {test_results['sharpe_ratio']}")
    print(f"Max Drawdown:    {test_results['max_drawdown_pct']}%")
    print(f"Num Trades:      {test_results['num_trades']}")
    print(f"Win Rate:        {test_results['win_rate']}%")
    print("\nTrade details:")
    for t in test_results["trade_log"]:
        print(f"  {t['entry_time']} -> {t['exit_time']} | {t['symbol']} {t['side']} "
              f"| ${t['pnl']} ({t['return_pct']}%) | {t['exit_reason']} | held {t['hold_days']}d")

    # Save results
    with open("/Users/rc/Projects/workspace/nautilus-trading/competition/agent-1-quant/round2/results.txt", "w") as f:
        f.write("Agent 1 — Quantitative Trader | Round 2\n")
        f.write("Strategy: Long-Only Trend Following (Donchian Breakout + Momentum)\n")
        f.write("Assets: BTCUSDT (50%), ETHUSDT (30%), SOLUSDT (20%)\n")
        f.write("Timeframe: Daily candles\n")
        f.write("Entry: Close > 20-day Donchian high AND close > close[10] AND > 50-EMA\n")
        f.write("Exit: 4x ATR trailing stop OR close < 10-day Donchian low\n")
        f.write("Position sizing: 3% equity risk, volatility-scaled, max 45% per position\n")
        f.write("Fees: 0.1% per trade\n")
        f.write("Long only — no shorts\n")
        f.write("\n")
        f.write("=" * 60 + "\n")
        f.write("TRAIN PERIOD: 2024-04-01 to 2024-09-30\n")
        f.write("=" * 60 + "\n")
        f.write(f"Final Equity:    ${train_results['final_equity']}\n")
        f.write(f"Total Return:    {train_results['total_return_pct']}%\n")
        f.write(f"Sharpe Ratio:    {train_results['sharpe_ratio']}\n")
        f.write(f"Max Drawdown:    {train_results['max_drawdown_pct']}%\n")
        f.write(f"Num Trades:      {train_results['num_trades']}\n")
        f.write(f"Win Rate:        {train_results['win_rate']}%\n")
        f.write("\nTrade Log (TRAIN):\n")
        for t in train_results["trade_log"]:
            f.write(f"  {t['entry_time']} -> {t['exit_time']} | {t['symbol']} {t['side']} "
                    f"| entry=${t['entry_price']} exit=${t['exit_price']} "
                    f"| pnl=${t['pnl']} ({t['return_pct']}%) | {t['exit_reason']} | held {t['hold_days']}d\n")

        f.write("\n")
        f.write("=" * 60 + "\n")
        f.write("TEST PERIOD: 2024-10-01 to 2024-12-31\n")
        f.write("=" * 60 + "\n")
        f.write(f"Final Equity:    ${test_results['final_equity']}\n")
        f.write(f"Total Return:    {test_results['total_return_pct']}%\n")
        f.write(f"Sharpe Ratio:    {test_results['sharpe_ratio']}\n")
        f.write(f"Max Drawdown:    {test_results['max_drawdown_pct']}%\n")
        f.write(f"Num Trades:      {test_results['num_trades']}\n")
        f.write(f"Win Rate:        {test_results['win_rate']}%\n")
        f.write("\nTrade Log (TEST):\n")
        for t in test_results["trade_log"]:
            f.write(f"  {t['entry_time']} -> {t['exit_time']} | {t['symbol']} {t['side']} "
                    f"| entry=${t['entry_price']} exit=${t['exit_price']} "
                    f"| pnl=${t['pnl']} ({t['return_pct']}%) | {t['exit_reason']} | held {t['hold_days']}d\n")

    print("\nResults saved to results.txt")
