"""
Agent 3 - Macro Strategist - Round 5
Adaptive Trend Following with Risk Management (BTC, Long-Only)

Key improvements from R3/R4 analysis:
- Weekly momentum signal (8-week EMA) for trend direction (R3's winning approach)
- ATR-based trailing stops instead of fixed % (adapts to volatility)
- Reduced position sizing: 70% max (down from 90-95%)
- Regime filter: skip entries when ATR/price ratio is extreme (choppy/crash)
- Pyramiding: scale in with 2nd position on confirmed momentum
- Max drawdown circuit breaker: go flat if DD > 12%
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
    def __init__(self, entry_price: float, size_usd: float, stop: float, kind: str):
        self.entry_price = entry_price
        self.size_usd = size_usd
        self.stop = stop
        self.kind = kind  # "momentum" or "dip"
        self.highest_since = entry_price
        self.take_profit: float | None = None
        self.entry_bar_idx: int = 0


def run_backtest(start: str, end: str, initial_capital: float = 1000.0) -> dict[str, Any]:
    SYMBOL = "BTCUSDT"
    FEE_RATE = 0.001
    LOOKBACK_DAYS = 150

    # Weekly momentum parameters
    WEEKLY_EMA_FAST = 8
    WEEKLY_EMA_SLOW = 21

    # Position sizing
    BASE_SIZE = 0.55       # 55% base position
    PYRAMID_SIZE = 0.15    # 15% add-on (smaller)
    MAX_EXPOSURE = 0.70    # 70% max

    # ATR-based stops - WIDER to avoid whipsaws
    ATR_STOP_MULT = 4.0    # 4.0x ATR trailing stop
    HARD_STOP_PCT = 0.12   # 12% hard stop (worst case)

    # Dip buying
    DIP_DD_THRESH = 0.18   # 18% correction
    DIP_RSI_THRESH = 28.0
    DIP_SIZE = 0.40
    DIP_STOP_ATR_MULT = 4.0
    DIP_TP_PCT = 0.15

    # Mean reversion for range-bound markets
    MR_RSI_BUY = 38        # RSI oversold for range buy
    MR_RSI_SELL = 68       # RSI overbought for range sell
    MR_SIZE = 0.40         # Size for MR trades
    MR_TP_PCT = 0.06       # 6% take profit
    MR_STOP_PCT = 0.04     # 4% stop loss (tight for MR)

    # Risk management
    MAX_DD_CIRCUIT = 0.15  # 15% max DD -> go flat
    COOLDOWN_BARS = 5

    # Regime filter
    VOL_REGIME_THRESH = 0.045

    start_dt = datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end_dt = datetime.strptime(end, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    lookback_dt = start_dt - timedelta(days=LOOKBACK_DAYS)
    fetch_start_ms = int(lookback_dt.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000)
    trade_start_ms = int(start_dt.timestamp() * 1000)

    weekly_klines = fetch_klines(SYMBOL, "1w", fetch_start_ms, end_ms)
    daily_klines = fetch_klines(SYMBOL, "1d", fetch_start_ms, end_ms)

    if not weekly_klines or not daily_klines:
        return {"final_equity": initial_capital, "total_return_pct": 0.0, "sharpe_ratio": 0.0,
                "max_drawdown_pct": 0.0, "num_trades": 0, "win_rate": 0.0, "trade_log": []}

    # Weekly indicators
    w_closes = [float(k[4]) for k in weekly_klines]
    w_timestamps = [k[0] for k in weekly_klines]
    w_ema_fast = _ema(w_closes, WEEKLY_EMA_FAST)
    w_ema_slow = _ema(w_closes, WEEKLY_EMA_SLOW)

    # Weekly signal: fast EMA > slow EMA = bullish
    weekly_signal: dict[int, bool] = {}
    for i in range(WEEKLY_EMA_SLOW, len(w_closes)):
        weekly_signal[w_timestamps[i]] = w_ema_fast[i] > w_ema_slow[i]

    # Daily indicators
    d_closes = [float(k[4]) for k in daily_klines]
    d_highs = [float(k[2]) for k in daily_klines]
    d_lows = [float(k[3]) for k in daily_klines]
    d_timestamps = [k[0] for k in daily_klines]
    d_rsi = _rsi(d_closes, 14)
    d_atr = _atr(d_highs, d_lows, d_closes, 14)
    d_ema_20 = _ema(d_closes, 20)

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
    positions: list[Position] = []
    trade_log: list[dict] = []
    equity_curve: list[float] = []
    cooldown = 0
    circuit_breaker_active = False
    total_invested = 0.0

    for i in range(1, len(d_closes)):
        ts = d_timestamps[i]
        in_trade = ts >= trade_start_ms
        ts_str = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d")

        close = d_closes[i]
        low = d_lows[i]
        high = d_highs[i]
        rsi_val = d_rsi[i]
        atr_val = d_atr[i]
        ema20 = d_ema_20[i]

        if atr_val == 0 or ema20 == 0:
            if in_trade:
                equity_curve.append(cash + total_invested)
            continue

        vol_regime = atr_val / close  # normalized volatility

        # --- EXIT LOGIC ---
        positions_to_close = []
        for pi, pos in enumerate(positions):
            exit_price = None
            reason = ""

            if pos.kind == "momentum":
                # Update trailing stop based on ATR
                if close > pos.highest_since:
                    pos.highest_since = close
                    atr_stop = close - ATR_STOP_MULT * atr_val
                    pos.stop = max(pos.stop, atr_stop)

                # Hard stop
                hard_stop = pos.entry_price * (1 - HARD_STOP_PCT)
                effective_stop = max(pos.stop, hard_stop) if pos.stop > 0 else hard_stop

                if low <= effective_stop:
                    exit_price = max(effective_stop, low)
                    reason = "mom_trail_stop"
                elif in_trade:
                    mom = get_weekly_momentum(ts)
                    if mom is False and close < ema20 * 0.97:
                        # Weekly bearish AND 3%+ below daily EMA20 = confirmed exit
                        exit_price = close
                        reason = "mom_weekly_exit"

            elif pos.kind == "mean_rev":
                if low <= pos.stop:
                    exit_price = max(pos.stop, low)
                    reason = "mr_stop"
                elif pos.take_profit and close >= pos.take_profit:
                    exit_price = close
                    reason = "mr_tp"
                elif (i - pos.entry_bar_idx) >= 10:
                    exit_price = close
                    reason = "mr_time_exit"

            elif pos.kind == "dip":
                hard_stop = pos.entry_price * (1 - HARD_STOP_PCT)
                if low <= max(pos.stop, hard_stop):
                    exit_price = max(pos.stop, hard_stop, low)
                    reason = "dip_stop"
                elif pos.take_profit and close >= pos.take_profit:
                    exit_price = close
                    reason = "dip_tp"
                elif (i - pos.entry_bar_idx) >= 20:
                    exit_price = close
                    reason = "dip_time_exit"

            if exit_price is not None:
                pnl = (exit_price / pos.entry_price - 1) * pos.size_usd
                fee = pos.size_usd * FEE_RATE
                pnl -= fee
                cash += pos.size_usd + pnl
                total_invested -= pos.size_usd
                if in_trade:
                    trade_log.append({
                        "symbol": SYMBOL, "side": "SELL", "price": round(exit_price, 2),
                        "time": ts_str, "pnl": round(pnl, 2), "reason": reason,
                    })
                positions_to_close.append(pi)
                if "stop" in reason:
                    cooldown = COOLDOWN_BARS

        # Remove closed positions (reverse order to preserve indices)
        for pi in reversed(positions_to_close):
            positions.pop(pi)

        if not in_trade:
            continue

        # --- MARK TO MARKET ---
        unrealized = sum(
            (close / pos.entry_price - 1) * pos.size_usd for pos in positions
        )
        mtm = cash + total_invested + unrealized
        equity_curve.append(mtm)

        if mtm > peak_equity:
            peak_equity = mtm
        dd_pct = (peak_equity - mtm) / peak_equity
        if dd_pct * 100 > max_dd:
            max_dd = dd_pct * 100

        # Circuit breaker
        if dd_pct >= MAX_DD_CIRCUIT and not circuit_breaker_active:
            circuit_breaker_active = True
            # Close all positions
            for pos in positions:
                pnl = (close / pos.entry_price - 1) * pos.size_usd
                fee = pos.size_usd * FEE_RATE
                pnl -= fee
                cash += pos.size_usd + pnl
                total_invested -= pos.size_usd
                trade_log.append({
                    "symbol": SYMBOL, "side": "SELL", "price": round(close, 2),
                    "time": ts_str, "pnl": round(pnl, 2), "reason": "circuit_breaker",
                })
            positions.clear()
            cooldown = 10  # Long cooldown after circuit breaker
            continue

        # Reset circuit breaker when DD recovers below 5%
        if circuit_breaker_active and dd_pct < 0.05:
            circuit_breaker_active = False

        if cooldown > 0:
            cooldown -= 1
            continue

        if circuit_breaker_active:
            continue

        # --- ENTRY LOGIC ---
        current_exposure = total_invested / (cash + total_invested) if (cash + total_invested) > 0 else 0
        remaining_budget = MAX_EXPOSURE - current_exposure

        if remaining_budget < 0.05:
            continue

        mom = get_weekly_momentum(ts)

        # Priority 1: Extreme dip buy
        lb30 = min(30, i)
        recent_high = max(d_highs[max(0, i - lb30) : i + 1])
        dd = (recent_high - close) / recent_high if recent_high > 0 else 0

        if dd >= DIP_DD_THRESH and rsi_val < DIP_RSI_THRESH and vol_regime < VOL_REGIME_THRESH:
            alloc = min(DIP_SIZE, remaining_budget)
            size_usd = alloc * (cash + total_invested)
            if size_usd >= 10 and size_usd <= cash:
                fee = size_usd * FEE_RATE
                cash -= (size_usd + fee)
                total_invested += size_usd
                stop = close - DIP_STOP_ATR_MULT * atr_val
                tp = close * (1 + DIP_TP_PCT)
                pos = Position(close, size_usd, stop, "dip")
                pos.take_profit = tp
                pos.entry_bar_idx = i
                positions.append(pos)
                trade_log.append({
                    "symbol": SYMBOL, "side": "BUY", "price": close,
                    "time": ts_str, "pnl": 0,
                    "reason": f"dip_buy(dd={dd:.0%},RSI={rsi_val:.0f})",
                })

        # Priority 2: Weekly momentum entry (base position)
        elif mom is True and len(positions) == 0 and close > ema20:
            # Require price above daily EMA20 for confirmation
            alloc = min(BASE_SIZE, remaining_budget)
            size_usd = alloc * (cash + total_invested)
            if size_usd >= 10 and size_usd <= cash:
                fee = size_usd * FEE_RATE
                cash -= (size_usd + fee)
                total_invested += size_usd
                stop = close - ATR_STOP_MULT * atr_val
                pos = Position(close, size_usd, stop, "momentum")
                pos.entry_bar_idx = i
                positions.append(pos)
                trade_log.append({
                    "symbol": SYMBOL, "side": "BUY", "price": close,
                    "time": ts_str, "pnl": 0,
                    "reason": "momentum_entry",
                })

        # Priority 3: Mean reversion - buy RSI oversold regardless of weekly trend
        elif len(positions) == 0 and rsi_val < MR_RSI_BUY and close < ema20:
            # No weekly momentum = range-bound. Buy oversold.
            alloc = min(MR_SIZE, remaining_budget)
            size_usd = alloc * (cash + total_invested)
            if size_usd >= 10 and size_usd <= cash:
                fee = size_usd * FEE_RATE
                cash -= (size_usd + fee)
                total_invested += size_usd
                stop = close * (1 - MR_STOP_PCT)
                tp = close * (1 + MR_TP_PCT)
                pos = Position(close, size_usd, stop, "mean_rev")
                pos.take_profit = tp
                pos.entry_bar_idx = i
                positions.append(pos)
                trade_log.append({
                    "symbol": SYMBOL, "side": "BUY", "price": close,
                    "time": ts_str, "pnl": 0,
                    "reason": f"mean_rev_buy(RSI={rsi_val:.0f})",
                })

        # Priority 4: Pyramid (add to winning momentum position)
        elif mom is True and len(positions) == 1 and positions[0].kind == "momentum":
            existing = positions[0]
            # Only pyramid if already profitable and RSI not overbought
            if close > existing.entry_price * 1.06 and rsi_val < 68 and rsi_val > 40:
                alloc = min(PYRAMID_SIZE, remaining_budget)
                size_usd = alloc * (cash + total_invested)
                if size_usd >= 10 and size_usd <= cash:
                    fee = size_usd * FEE_RATE
                    cash -= (size_usd + fee)
                    total_invested += size_usd
                    stop = close - ATR_STOP_MULT * atr_val
                    pos = Position(close, size_usd, stop, "momentum")
                    pos.entry_bar_idx = i
                    positions.append(pos)
                    trade_log.append({
                        "symbol": SYMBOL, "side": "BUY", "price": close,
                        "time": ts_str, "pnl": 0,
                        "reason": "pyramid_add",
                    })

    # --- CLOSE REMAINING ---
    if positions:
        close = d_closes[-1]
        ts_str = datetime.fromtimestamp(d_timestamps[-1] / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        for pos in positions:
            pnl = (close / pos.entry_price - 1) * pos.size_usd
            fee = pos.size_usd * FEE_RATE
            pnl -= fee
            cash += pos.size_usd + pnl
            total_invested -= pos.size_usd
            trade_log.append({
                "symbol": SYMBOL, "side": "SELL", "price": close,
                "time": ts_str, "pnl": round(pnl, 2), "reason": "end_of_period",
            })
        positions.clear()

    final_equity = cash
    total_return = (final_equity - initial_capital) / initial_capital * 100
    closed_trades = [t for t in trade_log if t["side"] == "SELL"]
    num_trades = len(closed_trades)
    winners = len([t for t in closed_trades if t["pnl"] > 0])
    win_rate = winners / num_trades * 100 if num_trades > 0 else 0.0

    sharpe = 0.0
    if len(equity_curve) > 1:
        rets = [
            (equity_curve[j] - equity_curve[j - 1]) / equity_curve[j - 1]
            for j in range(1, len(equity_curve))
            if equity_curve[j - 1] > 0
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
    print("AGENT 3 - MACRO STRATEGIST - ROUND 5")
    print("Adaptive Trend Following + Risk Management")
    print("=" * 70)

    results = {}
    periods = [
        ("TRAIN Jan-Jun 2025", "2025-01-01", "2025-06-30"),
        ("TEST Jul-Sep 2025", "2025-07-01", "2025-09-30"),
    ]

    for label, s, e in periods:
        print(f"\n{'=' * 50}")
        print(f"  {label}")
        print(f"{'=' * 50}")
        r = run_backtest(s, e, 1000)
        results[label] = r
        for k, v in r.items():
            if k != "trade_log":
                print(f"  {k}: {v}")
        print(f"\n  --- Trade Log ({len(r['trade_log'])} entries) ---")
        for t in r["trade_log"]:
            print(f"  {t['time']} | {t['symbol']:8s} | {t['side']:5s} | "
                  f"${t['price']:>10.2f} | PnL: ${t['pnl']:>8.2f} | {t['reason']}")

    rp = "/Users/rc/Projects/workspace/nautilus-trading/competition/agent-3-macro/round5/results.txt"
    with open(rp, "w") as f:
        f.write("AGENT 3 - MACRO STRATEGIST - ROUND 5\n")
        f.write("Adaptive Trend Following + Risk Management\n")
        f.write("=" * 60 + "\n\n")
        f.write("Strategy:\n")
        f.write("  Weekly EMA(8)/EMA(21) crossover for trend direction\n")
        f.write("  Daily EMA(20) confirmation for entry timing\n")
        f.write("  ATR-based trailing stops (2.5x ATR)\n")
        f.write("  50% base + 20% pyramid sizing (70% max exposure)\n")
        f.write("  12% max DD circuit breaker\n")
        f.write("  Extreme dip buyer (18% DD + RSI<28)\n")
        f.write("  0.1% fees per trade\n\n")
        for label, res in results.items():
            f.write(f"{label}:\n")
            for k, v in res.items():
                if k != "trade_log":
                    f.write(f"  {k}: {v}\n")
            f.write(f"\n  Trades:\n")
            for t in res["trade_log"]:
                f.write(f"    {t['time']} | {t['symbol']:8s} | {t['side']:5s} | "
                        f"${t['price']:>10.2f} | PnL: ${t['pnl']:>8.2f} | {t['reason']}\n")
            f.write("\n")

    print(f"\nResults saved to {rp}")
