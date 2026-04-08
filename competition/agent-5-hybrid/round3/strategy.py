"""
Agent 5 - Hybrid Strategist - Round 3
Adaptive regime-switching strategy: dynamically selects bull vs bear parameters
based on real-time regime detection.

Key design:
- Daily BTC bars only (highest signal-to-noise)
- Regime detection via EMA alignment + ADX + DI crossover
- BULL regime: go long with moderate stops, tight TP
- BEAR regime: go short with moderate stops, tight TP
- RANGE regime: stay flat (cash is king)
- Drawdown circuit breaker reduces sizing
- Separate bull/bear parameter sets tuned for each regime
"""

import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import numpy as np
import requests


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------

def fetch_klines(symbol: str, interval: str, start: str, end: str) -> np.ndarray:
    """Fetch Binance klines. Returns array of [timestamp, O, H, L, C, volume]."""
    url = "https://api.binance.com/api/v3/klines"
    start_ms = int(datetime.strptime(start, "%Y-%m-%d").timestamp() * 1000)
    end_ms = int(datetime.strptime(end, "%Y-%m-%d").timestamp() * 1000)
    all_klines: list[list[float]] = []
    current = start_ms

    while current < end_ms:
        params = {
            "symbol": symbol, "interval": interval,
            "startTime": current, "endTime": end_ms, "limit": 1000,
        }
        for attempt in range(3):
            try:
                resp = requests.get(url, params=params, timeout=30)
                resp.raise_for_status()
                break
            except Exception:
                if attempt == 2:
                    raise
                time.sleep(1)
        data = resp.json()
        if not data:
            break
        for k in data:
            all_klines.append([k[0], float(k[1]), float(k[2]), float(k[3]),
                               float(k[4]), float(k[5])])
        current = data[-1][0] + 1
        time.sleep(0.15)

    return np.array(all_klines) if all_klines else np.empty((0, 6))


# ---------------------------------------------------------------------------
# Indicators
# ---------------------------------------------------------------------------

def sma(data: np.ndarray, period: int) -> np.ndarray:
    out = np.full(len(data), np.nan)
    for i in range(period - 1, len(data)):
        out[i] = np.mean(data[i - period + 1 : i + 1])
    return out


def ema(data: np.ndarray, period: int) -> np.ndarray:
    out = np.full(len(data), np.nan)
    if len(data) < period:
        return out
    k = 2.0 / (period + 1)
    out[period - 1] = np.mean(data[:period])
    for i in range(period, len(data)):
        out[i] = data[i] * k + out[i - 1] * (1 - k)
    return out


def calc_atr(high: np.ndarray, low: np.ndarray, close: np.ndarray,
             period: int = 14) -> np.ndarray:
    n = len(close)
    tr = np.zeros(n)
    tr[0] = high[0] - low[0]
    for i in range(1, n):
        tr[i] = max(high[i] - low[i], abs(high[i] - close[i - 1]),
                     abs(low[i] - close[i - 1]))
    return sma(tr, period)


def calc_rsi(close: np.ndarray, period: int = 14) -> np.ndarray:
    n = len(close)
    out = np.full(n, np.nan)
    deltas = np.diff(close)
    if len(deltas) < period:
        return out
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses[:period])
    if avg_loss > 0:
        out[period] = 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)
    else:
        out[period] = 100.0
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            out[i + 1] = 100.0
        else:
            out[i + 1] = 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)
    return out


def calc_macd(close: np.ndarray, fast: int = 12, slow: int = 26, signal: int = 9):
    ema_fast = ema(close, fast)
    ema_slow = ema(close, slow)
    macd_line = ema_fast - ema_slow
    signal_line = np.full(len(close), np.nan)
    valid_start = slow - 1
    if valid_start + signal <= len(close):
        valid_macd = macd_line[valid_start:]
        sig = ema(valid_macd, signal)
        signal_line[valid_start:] = sig
    hist = macd_line - signal_line
    return macd_line, signal_line, hist


def calc_adx(high: np.ndarray, low: np.ndarray, close: np.ndarray,
             period: int = 14):
    """Returns (adx, plus_di, minus_di)."""
    n = len(close)
    adx_out = np.full(n, np.nan)
    pdi_out = np.full(n, np.nan)
    mdi_out = np.full(n, np.nan)
    if n < 2 * period + 1:
        return adx_out, pdi_out, mdi_out

    tr = np.zeros(n)
    plus_dm = np.zeros(n)
    minus_dm = np.zeros(n)
    for i in range(1, n):
        hl = high[i] - low[i]
        hc = abs(high[i] - close[i - 1])
        lc = abs(low[i] - close[i - 1])
        tr[i] = max(hl, hc, lc)
        up = high[i] - high[i - 1]
        down = low[i - 1] - low[i]
        plus_dm[i] = up if (up > down and up > 0) else 0.0
        minus_dm[i] = down if (down > up and down > 0) else 0.0

    atr_s = np.sum(tr[1:period + 1])
    pdm_s = np.sum(plus_dm[1:period + 1])
    mdm_s = np.sum(minus_dm[1:period + 1])

    dx_vals = []
    for i in range(period, n):
        if i > period:
            atr_s = atr_s - atr_s / period + tr[i]
            pdm_s = pdm_s - pdm_s / period + plus_dm[i]
            mdm_s = mdm_s - mdm_s / period + minus_dm[i]

        if atr_s > 0:
            pdi = 100.0 * pdm_s / atr_s
            mdi = 100.0 * mdm_s / atr_s
        else:
            pdi = mdi = 0.0

        pdi_out[i] = pdi
        mdi_out[i] = mdi

        denom = pdi + mdi
        dx = 100.0 * abs(pdi - mdi) / denom if denom > 0 else 0.0
        dx_vals.append(dx)

        if len(dx_vals) == period:
            adx_out[i] = np.mean(dx_vals)
        elif len(dx_vals) > period:
            adx_out[i] = (adx_out[i - 1] * (period - 1) + dx) / period

    return adx_out, pdi_out, mdi_out


# ---------------------------------------------------------------------------
# Regime detection — the core of the adaptive approach
# ---------------------------------------------------------------------------

def detect_regime(i: int, close: np.ndarray, ema8: np.ndarray, ema21: np.ndarray,
                  ema55: np.ndarray, adx_v: np.ndarray, pdi: np.ndarray,
                  mdi: np.ndarray, rsi_v: np.ndarray) -> str:
    """
    Detect market regime using multiple signals.
    Returns: 'bull', 'bear', or 'range'
    """
    bull_signals = 0
    bear_signals = 0

    # 1. EMA alignment
    if not (np.isnan(ema8[i]) or np.isnan(ema21[i]) or np.isnan(ema55[i])):
        if ema8[i] > ema21[i] > ema55[i]:
            bull_signals += 2
        elif ema8[i] < ema21[i] < ema55[i]:
            bear_signals += 2
        # Price vs EMA55
        if close[i] > ema55[i]:
            bull_signals += 1
        else:
            bear_signals += 1

    # 2. ADX + DI crossover
    if not (np.isnan(adx_v[i]) or np.isnan(pdi[i]) or np.isnan(mdi[i])):
        if adx_v[i] > 20:
            if pdi[i] > mdi[i]:
                bull_signals += 2
            else:
                bear_signals += 2
        # Even without strong ADX, DI gives direction
        elif pdi[i] > mdi[i] + 5:
            bull_signals += 1
        elif mdi[i] > pdi[i] + 5:
            bear_signals += 1

    # 3. RSI trend
    if not np.isnan(rsi_v[i]):
        if rsi_v[i] > 55:
            bull_signals += 1
        elif rsi_v[i] < 45:
            bear_signals += 1

    # Decision: need clear majority
    if bull_signals >= 4 and bull_signals > bear_signals + 2:
        return "bull"
    elif bear_signals >= 4 and bear_signals > bull_signals + 2:
        return "bear"
    return "range"


# ---------------------------------------------------------------------------
# Signal strength scoring
# ---------------------------------------------------------------------------

def compute_bull_score(i: int, close: np.ndarray, ema8: np.ndarray,
                       ema21: np.ndarray, rsi_v: np.ndarray,
                       macd_hist: np.ndarray) -> float:
    """Score for long entry. Higher = more bullish. Range ~0 to 5."""
    score = 0.0

    # EMA momentum
    if not (np.isnan(ema8[i]) or np.isnan(ema21[i])):
        if ema8[i] > ema21[i]:
            score += 1.5
        # Price above short EMA
        if close[i] > ema8[i]:
            score += 0.5

    # RSI: not overbought, moderate strength
    if not np.isnan(rsi_v[i]):
        if 40 <= rsi_v[i] <= 65:
            score += 1.0  # Healthy range
        elif rsi_v[i] < 35:
            score += 1.5  # Oversold bounce opportunity
        elif rsi_v[i] > 70:
            score -= 0.5  # Overbought, risky

    # MACD momentum
    if not np.isnan(macd_hist[i]) and i > 0 and not np.isnan(macd_hist[i - 1]):
        if macd_hist[i] > 0 and macd_hist[i] > macd_hist[i - 1]:
            score += 1.5
        elif macd_hist[i] > 0:
            score += 0.5

    return score


def compute_bear_score(i: int, close: np.ndarray, ema8: np.ndarray,
                        ema21: np.ndarray, rsi_v: np.ndarray,
                        macd_hist: np.ndarray) -> float:
    """Score for short entry. Higher = more bearish. Range ~0 to 5."""
    score = 0.0

    # EMA momentum (bearish)
    if not (np.isnan(ema8[i]) or np.isnan(ema21[i])):
        if ema8[i] < ema21[i]:
            score += 1.5
        if close[i] < ema8[i]:
            score += 0.5

    # RSI: not oversold, moderate weakness
    if not np.isnan(rsi_v[i]):
        if 35 <= rsi_v[i] <= 55:
            score += 1.0
        elif rsi_v[i] > 65:
            score += 1.5  # Overbought reversal opportunity
        elif rsi_v[i] < 30:
            score -= 0.5  # Oversold, risky to short

    # MACD momentum (bearish)
    if not np.isnan(macd_hist[i]) and i > 0 and not np.isnan(macd_hist[i - 1]):
        if macd_hist[i] < 0 and macd_hist[i] < macd_hist[i - 1]:
            score += 1.5
        elif macd_hist[i] < 0:
            score += 0.5

    return score


# ---------------------------------------------------------------------------
# Backtest engine — regime-adaptive
# ---------------------------------------------------------------------------

def backtest_adaptive(klines: np.ndarray, capital: float,
                      fee_rate: float = 0.001,
                      backtest_start_ms: int = 0) -> tuple:
    """
    Adaptive regime-switching backtest.
    - In bull regime: only longs, score threshold 2.0, stop 1.5 ATR, TP 2.0 ATR
    - In bear regime: only shorts, score threshold 2.0, stop 1.5 ATR, TP 2.0 ATR
    - In range: stay flat
    """
    warmup = 60
    if len(klines) < warmup + 5:
        return capital, [], [capital]

    close = klines[:, 4]
    high = klines[:, 2]
    low = klines[:, 3]
    timestamps = klines[:, 0]

    # Indicators
    ema8 = ema(close, 8)
    ema21 = ema(close, 21)
    ema55 = ema(close, 55)
    rsi_v = calc_rsi(close, 14)
    _, _, macd_hist = calc_macd(close, 12, 26, 9)
    atr_v = calc_atr(high, low, close, 14)
    adx_v, pdi, mdi = calc_adx(high, low, close, 14)

    # Find start index
    start_idx = warmup
    if backtest_start_ms > 0:
        for j in range(len(timestamps)):
            if timestamps[j] >= backtest_start_ms:
                start_idx = max(j, warmup)
                break

    equity = capital
    position = 0.0  # positive = long, negative = short
    entry_price = 0.0
    stop_price = 0.0
    take_profit = 0.0
    trade_log: list[dict] = []
    equity_curve: list[float] = []
    bars_in_trade = 0
    peak_equity = capital

    # Parameters — tuned for profitability across regimes
    LONG_SCORE_THRESH = 2.0
    SHORT_SCORE_THRESH = 2.0
    STOP_ATR = 1.5
    TP_ATR = 2.0
    RISK_PCT = 0.02
    MAX_POS_PCT = 0.40

    for i in range(start_idx, len(close)):
        unrealized = position * (close[i] - entry_price) if position != 0 else 0.0
        current_eq = equity + unrealized
        equity_curve.append(current_eq)

        if current_eq > peak_equity:
            peak_equity = current_eq

        if np.isnan(atr_v[i]) or atr_v[i] <= 0:
            continue

        ts = datetime.fromtimestamp(timestamps[i] / 1000).strftime("%Y-%m-%d")
        regime = detect_regime(i, close, ema8, ema21, ema55, adx_v, pdi, mdi, rsi_v)

        # Drawdown circuit breaker
        dd = (peak_equity - current_eq) / peak_equity if peak_equity > 0 else 0
        dd_scale = 0.5 if dd > 0.06 else (0.75 if dd > 0.04 else 1.0)

        # --- Exit logic ---
        if position > 0:  # Long position
            bars_in_trade += 1
            # Trailing stop
            new_stop = close[i] - atr_v[i] * STOP_ATR
            if new_stop > stop_price:
                stop_price = new_stop

            hit_stop = low[i] <= stop_price
            hit_tp = high[i] >= take_profit
            # Regime flip exit: if regime turns bear, exit immediately
            regime_exit = regime == "bear" and bars_in_trade >= 2

            if hit_stop or hit_tp or regime_exit:
                if hit_tp:
                    exit_p = min(take_profit, high[i])
                    reason = "tp"
                elif hit_stop:
                    exit_p = max(stop_price, low[i])
                    reason = "stop"
                else:
                    exit_p = close[i]
                    reason = "regime_flip"

                pnl = position * (exit_p - entry_price) - abs(position * exit_p * fee_rate)
                equity += position * (exit_p - entry_price) - abs(position * exit_p * fee_rate)
                trade_log.append({
                    "symbol": "BTCUSDT", "side": "close_long", "date": ts,
                    "entry": round(entry_price, 2), "exit": round(exit_p, 2),
                    "pnl": round(pnl, 2), "reason": reason,
                    "bars_held": bars_in_trade, "regime": regime,
                })
                position = 0.0
                bars_in_trade = 0
                continue

        elif position < 0:  # Short position
            bars_in_trade += 1
            new_stop = close[i] + atr_v[i] * STOP_ATR
            if new_stop < stop_price:
                stop_price = new_stop

            hit_stop = high[i] >= stop_price
            hit_tp = low[i] <= take_profit
            regime_exit = regime == "bull" and bars_in_trade >= 2

            if hit_stop or hit_tp or regime_exit:
                if hit_tp:
                    exit_p = max(take_profit, low[i])
                    reason = "tp"
                elif hit_stop:
                    exit_p = min(stop_price, high[i])
                    reason = "stop"
                else:
                    exit_p = close[i]
                    reason = "regime_flip"

                pnl = abs(position) * (entry_price - exit_p) - abs(position * exit_p * fee_rate)
                equity += abs(position) * (entry_price - exit_p) - abs(position * exit_p * fee_rate)
                trade_log.append({
                    "symbol": "BTCUSDT", "side": "close_short", "date": ts,
                    "entry": round(entry_price, 2), "exit": round(exit_p, 2),
                    "pnl": round(pnl, 2), "reason": reason,
                    "bars_held": bars_in_trade, "regime": regime,
                })
                position = 0.0
                bars_in_trade = 0
                continue

        # --- Entry logic ---
        if position == 0:
            if regime == "bull":
                bull_score = compute_bull_score(i, close, ema8, ema21, rsi_v, macd_hist)
                if bull_score >= LONG_SCORE_THRESH:
                    stop_dist = atr_v[i] * STOP_ATR
                    risk = current_eq * RISK_PCT * dd_scale
                    size = min(risk / stop_dist, current_eq * MAX_POS_PCT / close[i])
                    if size * close[i] < 10:
                        continue
                    entry_price = close[i]
                    position = size
                    stop_price = entry_price - stop_dist
                    take_profit = entry_price + atr_v[i] * TP_ATR
                    equity -= size * entry_price * fee_rate
                    bars_in_trade = 0
                    trade_log.append({
                        "symbol": "BTCUSDT", "side": "long", "date": ts,
                        "entry": round(entry_price, 2),
                        "score": round(bull_score, 2), "regime": regime,
                        "size_usd": round(size * entry_price, 2),
                    })

            elif regime == "bear":
                bear_score = compute_bear_score(i, close, ema8, ema21, rsi_v, macd_hist)
                if bear_score >= SHORT_SCORE_THRESH:
                    stop_dist = atr_v[i] * STOP_ATR
                    risk = current_eq * RISK_PCT * dd_scale * 0.75
                    size = min(risk / stop_dist, current_eq * MAX_POS_PCT * 0.7 / close[i])
                    if size * close[i] < 10:
                        continue
                    entry_price = close[i]
                    position = -size
                    stop_price = entry_price + stop_dist
                    take_profit = entry_price - atr_v[i] * TP_ATR
                    equity -= size * entry_price * fee_rate
                    bars_in_trade = 0
                    trade_log.append({
                        "symbol": "BTCUSDT", "side": "short", "date": ts,
                        "entry": round(entry_price, 2),
                        "score": round(bear_score, 2), "regime": regime,
                        "size_usd": round(size * entry_price, 2),
                    })

    # Close remaining position at end
    if position != 0:
        exit_p = close[-1]
        ts = datetime.fromtimestamp(timestamps[-1] / 1000).strftime("%Y-%m-%d")
        if position > 0:
            pnl = position * (exit_p - entry_price) - abs(position * exit_p * fee_rate)
        else:
            pnl = abs(position) * (entry_price - exit_p) - abs(position * exit_p * fee_rate)
        equity += pnl
        trade_log.append({
            "symbol": "BTCUSDT", "side": "close_eod", "date": ts,
            "entry": round(entry_price, 2), "exit": round(exit_p, 2),
            "pnl": round(pnl, 2),
        })

    return equity, trade_log, equity_curve


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_backtest(start: str, end: str, initial_capital: float = 1000.0) -> dict[str, Any]:
    start_dt = datetime.strptime(start, "%Y-%m-%d")
    lookback_start = (start_dt - timedelta(days=200)).strftime("%Y-%m-%d")
    start_ms = int(start_dt.timestamp() * 1000)

    print(f"Fetching BTCUSDT daily data from {lookback_start} to {end}...")
    klines = fetch_klines("BTCUSDT", "1d", lookback_start, end)
    print(f"  {len(klines)} bars loaded")

    equity, trade_log, equity_curve = backtest_adaptive(
        klines, initial_capital, backtest_start_ms=start_ms
    )

    ret_pct = (equity - initial_capital) / initial_capital * 100
    closed = [t for t in trade_log if t.get("side", "").startswith("close")]
    num_trades = len(closed)
    wins = [t for t in closed if t.get("pnl", 0) > 0]
    win_rate = len(wins) / num_trades * 100 if num_trades > 0 else 0

    max_dd = 0.0
    if equity_curve:
        peak = equity_curve[0]
        for v in equity_curve:
            if v > peak:
                peak = v
            dd = (peak - v) / peak * 100
            if dd > max_dd:
                max_dd = dd

    sharpe = 0.0
    if equity_curve and len(equity_curve) > 1:
        rets = np.diff(equity_curve) / np.array(equity_curve[:-1])
        if np.std(rets) > 0:
            sharpe = (np.mean(rets) / np.std(rets)) * np.sqrt(365)

    return {
        "final_equity": round(equity, 2),
        "total_return_pct": round(ret_pct, 2),
        "sharpe_ratio": round(sharpe, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "num_trades": num_trades,
        "win_rate": round(win_rate, 1),
        "trade_log": trade_log,
    }


if __name__ == "__main__":
    print("=" * 70)
    print("Agent 5 - Hybrid Strategist - Round 3")
    print("Adaptive regime-switching: bull/bear/range modes")
    print("=" * 70)

    print("\n--- TRAIN: 2024-07-01 to 2024-12-31 ---")
    train = run_backtest("2024-07-01", "2024-12-31", 1000.0)
    print("\nTrain Results:")
    for k in ["final_equity", "total_return_pct", "sharpe_ratio",
              "max_drawdown_pct", "num_trades", "win_rate"]:
        print(f"  {k}: {train[k]}")

    print("\n--- TEST: 2025-01-01 to 2025-03-31 ---")
    test = run_backtest("2025-01-01", "2025-03-31", 1000.0)
    print("\nTest Results:")
    for k in ["final_equity", "total_return_pct", "sharpe_ratio",
              "max_drawdown_pct", "num_trades", "win_rate"]:
        print(f"  {k}: {test[k]}")

    # Save results
    out = "/Users/rc/Projects/workspace/nautilus-trading/competition/agent-5-hybrid/round3/results.txt"
    with open(out, "w") as f:
        f.write("Agent 5 - Hybrid Strategist - Round 3 Results\n")
        f.write("=" * 70 + "\n\n")
        f.write("Strategy: Adaptive regime-switching (bull/bear/range)\n")
        f.write("Timeframe: Daily | Asset: BTCUSDT only\n")
        f.write("Regime: EMA(8,21,55) alignment + ADX + DI + RSI\n")
        f.write("Bull mode: long only, score >= 2.0, stop 1.5 ATR, TP 2.0 ATR\n")
        f.write("Bear mode: short only, score >= 2.0, stop 1.5 ATR, TP 2.0 ATR\n")
        f.write("Range mode: stay flat\n\n")

        for label, res in [("TRAIN (2024-07-01 to 2024-12-31)", train),
                           ("TEST (2025-01-01 to 2025-03-31)", test)]:
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
