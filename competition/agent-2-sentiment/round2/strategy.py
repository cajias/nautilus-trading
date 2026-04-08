"""
Agent 2 - Sentiment Trader (Round 2)
=====================================
Long-biased volume-sentiment strategy for crypto.

Key changes from Round 1:
- Long-only bias (learned from bull run failure)
- Minimal parameters (avoid overfitting)
- Volume-based accumulation detection (OBV trend + volume spikes)
- Fear/greed regime via volatility compression -> expansion
- Asymmetric: aggressive longs, conservative shorts (short only extreme conditions)
"""

import json
import time
from datetime import datetime, timedelta
from typing import Any

import requests
import numpy as np


def fetch_binance_klines(symbol: str, interval: str, start_ms: int, end_ms: int) -> list:
    """Fetch klines from Binance public API."""
    all_klines = []
    current_start = start_ms

    while current_start < end_ms:
        url = "https://api.binance.com/api/v3/klines"
        params = {
            "symbol": symbol,
            "interval": interval,
            "startTime": current_start,
            "endTime": end_ms,
            "limit": 1000,
        }
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if not data:
            break
        all_klines.extend(data)
        current_start = data[-1][0] + 1
        time.sleep(0.1)

    return all_klines


def parse_klines(raw: list) -> dict:
    """Parse raw klines into numpy arrays."""
    opens = np.array([float(k[1]) for k in raw])
    highs = np.array([float(k[2]) for k in raw])
    lows = np.array([float(k[3]) for k in raw])
    closes = np.array([float(k[4]) for k in raw])
    volumes = np.array([float(k[5]) for k in raw])
    taker_buy_vol = np.array([float(k[9]) for k in raw])
    timestamps = [k[0] for k in raw]
    return {
        "open": opens, "high": highs, "low": lows, "close": closes,
        "volume": volumes, "taker_buy_vol": taker_buy_vol, "timestamps": timestamps,
    }


def ema(data: np.ndarray, period: int) -> np.ndarray:
    """Exponential moving average."""
    result = np.full_like(data, np.nan)
    if len(data) < period:
        return result
    result[period - 1] = np.mean(data[:period])
    multiplier = 2.0 / (period + 1)
    for i in range(period, len(data)):
        result[i] = data[i] * multiplier + result[i - 1] * (1 - multiplier)
    return result


def sma(data: np.ndarray, period: int) -> np.ndarray:
    """Simple moving average."""
    result = np.full_like(data, np.nan)
    for i in range(period - 1, len(data)):
        result[i] = np.mean(data[i - period + 1 : i + 1])
    return result


def atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> np.ndarray:
    """Average True Range."""
    tr = np.maximum(high[1:] - low[1:],
                    np.maximum(np.abs(high[1:] - close[:-1]),
                               np.abs(low[1:] - close[:-1])))
    tr = np.concatenate([[high[0] - low[0]], tr])
    return ema(tr, period)


def obv(close: np.ndarray, volume: np.ndarray) -> np.ndarray:
    """On-Balance Volume."""
    result = np.zeros_like(close)
    result[0] = volume[0]
    for i in range(1, len(close)):
        if close[i] > close[i - 1]:
            result[i] = result[i - 1] + volume[i]
        elif close[i] < close[i - 1]:
            result[i] = result[i - 1] - volume[i]
        else:
            result[i] = result[i - 1]
    return result


def rsi(close: np.ndarray, period: int = 14) -> np.ndarray:
    """Relative Strength Index."""
    result = np.full_like(close, np.nan)
    deltas = np.diff(close)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)

    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses[:period])

    if avg_loss == 0:
        result[period] = 100.0
    else:
        result[period] = 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)

    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            result[i + 1] = 100.0
        else:
            result[i + 1] = 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)

    return result


def run_backtest(
    start: str,
    end: str,
    initial_capital: float = 1000.0,
    symbol: str = "BTCUSDT",
) -> dict[str, Any]:
    """
    Run sentiment-based backtest.

    Strategy logic:
    - Use 4h candles for BTC (good balance of noise vs signal)
    - Long-biased: enter long on accumulation signals, only short on extreme greed + divergence
    - Signals:
      1. OBV trend (EMA of OBV rising = accumulation)
      2. Taker buy ratio (>0.5 = buyers aggressive)
      3. RSI regime (not overbought for longs, oversold = strong buy)
      4. Volatility compression (low ATR% -> breakout imminent, go with trend)
      5. Price above EMA 50 = uptrend bias
    - Position sizing: 90% of equity for longs, 40% for shorts
    - Stop loss: 2x ATR trailing
    - Take profit: 4x ATR (2:1 R:R for longs)
    """
    # Parse dates
    start_dt = datetime.strptime(start, "%Y-%m-%d")
    end_dt = datetime.strptime(end, "%Y-%m-%d")

    # Fetch extra lookback (60 days for indicators)
    lookback_dt = start_dt - timedelta(days=60)
    start_ms = int(lookback_dt.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000)

    print(f"Fetching {symbol} 4h klines from {lookback_dt.date()} to {end_dt.date()}...")
    raw = fetch_binance_klines(symbol, "4h", start_ms, end_ms)
    data = parse_klines(raw)
    print(f"Got {len(data['close'])} candles")

    closes = data["close"]
    highs = data["high"]
    lows = data["low"]
    volumes = data["volume"]
    taker_buy = data["taker_buy_vol"]
    timestamps = data["timestamps"]

    # Indicators
    ema_20 = ema(closes, 20)
    ema_50 = ema(closes, 50)
    atr_14 = atr(highs, lows, closes, 14)
    rsi_14 = rsi(closes, 14)
    obv_vals = obv(closes, volumes)
    obv_ema = ema(obv_vals, 20)
    vol_sma = sma(volumes, 20)

    # Taker buy ratio (smoothed)
    taker_ratio = np.where(volumes > 0, taker_buy / volumes, 0.5)
    taker_ratio_sma = sma(taker_ratio, 10)

    # ATR as % of price (volatility measure)
    atr_pct = np.where(closes > 0, atr_14 / closes * 100, 0)
    atr_pct_sma = sma(atr_pct, 20)

    # Find start index for actual trading
    actual_start_ms = int(start_dt.timestamp() * 1000)
    trade_start_idx = 0
    for i, ts in enumerate(timestamps):
        if ts >= actual_start_ms:
            trade_start_idx = i
            break

    # Ensure indicators are warmed up
    warmup = max(trade_start_idx, 55)

    # Trading state
    equity = initial_capital
    position = 0  # +1 long, -1 short, 0 flat
    entry_price = 0.0
    position_size_usd = 0.0
    stop_loss = 0.0
    take_profit = 0.0
    trailing_stop = 0.0
    fee_rate = 0.001  # 0.1%
    cooldown = 0  # Bars to wait after a loss

    peak_equity = initial_capital
    max_drawdown_pct = 0.0
    trade_log = []
    current_trade = {}

    for i in range(warmup, len(closes)):
        price = closes[i]
        high = highs[i]
        low = lows[i]

        # Skip if indicators not ready
        if np.isnan(ema_50[i]) or np.isnan(rsi_14[i]) or np.isnan(obv_ema[i]):
            continue
        if np.isnan(atr_pct_sma[i]) or np.isnan(taker_ratio_sma[i]) or np.isnan(vol_sma[i]):
            continue

        # --- Check exits first ---
        if position != 0:
            # Update trailing stop for longs
            if position == 1:
                new_trail = price - 2.0 * atr_14[i]
                if new_trail > trailing_stop:
                    trailing_stop = new_trail

                # Check stop loss (use max of fixed stop and trailing)
                effective_stop = max(stop_loss, trailing_stop)
                if low <= effective_stop:
                    exit_price = effective_stop
                    pnl_pct = (exit_price / entry_price - 1) * 100
                    fee = position_size_usd * fee_rate
                    pnl_usd = position_size_usd * (exit_price / entry_price - 1) - fee
                    equity += pnl_usd
                    current_trade["exit_price"] = exit_price
                    current_trade["exit_time"] = timestamps[i]
                    current_trade["pnl_pct"] = round(pnl_pct, 2)
                    current_trade["pnl_usd"] = round(pnl_usd, 2)
                    current_trade["exit_reason"] = "stop"
                    trade_log.append(current_trade)
                    position = 0
                    current_trade = {}
                    cooldown = 6  # Wait 6 bars (24h) after a stop loss
                    continue

                # Check take profit
                if high >= take_profit:
                    exit_price = take_profit
                    pnl_pct = (exit_price / entry_price - 1) * 100
                    fee = position_size_usd * fee_rate
                    pnl_usd = position_size_usd * (exit_price / entry_price - 1) - fee
                    equity += pnl_usd
                    current_trade["exit_price"] = exit_price
                    current_trade["exit_time"] = timestamps[i]
                    current_trade["pnl_pct"] = round(pnl_pct, 2)
                    current_trade["pnl_usd"] = round(pnl_usd, 2)
                    current_trade["exit_reason"] = "tp"
                    trade_log.append(current_trade)
                    position = 0
                    current_trade = {}
                    continue

            elif position == -1:
                # Short trailing stop (moves down)
                new_trail = price + 2.0 * atr_14[i]
                if new_trail < trailing_stop:
                    trailing_stop = new_trail

                effective_stop = min(stop_loss, trailing_stop)
                if high >= effective_stop:
                    exit_price = effective_stop
                    pnl_pct = (1 - exit_price / entry_price) * 100
                    fee = position_size_usd * fee_rate
                    pnl_usd = position_size_usd * (1 - exit_price / entry_price) - fee
                    equity += pnl_usd
                    current_trade["exit_price"] = exit_price
                    current_trade["exit_time"] = timestamps[i]
                    current_trade["pnl_pct"] = round(pnl_pct, 2)
                    current_trade["pnl_usd"] = round(pnl_usd, 2)
                    current_trade["exit_reason"] = "stop"
                    trade_log.append(current_trade)
                    position = 0
                    current_trade = {}
                    continue

                if low <= take_profit:
                    exit_price = take_profit
                    pnl_pct = (1 - exit_price / entry_price) * 100
                    fee = position_size_usd * fee_rate
                    pnl_usd = position_size_usd * (1 - exit_price / entry_price) - fee
                    equity += pnl_usd
                    current_trade["exit_price"] = exit_price
                    current_trade["exit_time"] = timestamps[i]
                    current_trade["pnl_pct"] = round(pnl_pct, 2)
                    current_trade["pnl_usd"] = round(pnl_usd, 2)
                    current_trade["exit_reason"] = "tp"
                    trade_log.append(current_trade)
                    position = 0
                    current_trade = {}
                    continue

        # --- Entry signals (only when flat) ---
        if cooldown > 0:
            cooldown -= 1

        if position == 0 and timestamps[i] >= actual_start_ms and cooldown == 0:
            # Signal components
            trend_up = closes[i] > ema_50[i]
            trend_down = closes[i] < ema_50[i]
            ema_cross_up = ema_20[i] > ema_50[i]  # EMA crossover confirmation
            obv_rising = obv_vals[i] > obv_ema[i]
            obv_falling = obv_vals[i] < obv_ema[i]
            buyers_aggressive = taker_ratio_sma[i] > 0.52
            sellers_aggressive = taker_ratio_sma[i] < 0.48
            rsi_val = rsi_14[i]
            vol_expanding = volumes[i] > vol_sma[i] * 1.2
            vol_compressing = atr_pct[i] < atr_pct_sma[i] * 0.8

            # --- LONG signals (aggressive but filtered) ---
            long_score = 0
            if trend_up:
                long_score += 2  # Strong weight for trend
            if ema_cross_up:
                long_score += 1  # EMA alignment confirmation
            if obv_rising:
                long_score += 1
            if buyers_aggressive:
                long_score += 1
            if rsi_val < 40:  # Oversold in uptrend = great entry
                long_score += 2
            elif rsi_val < 55:
                long_score += 1
            if vol_expanding and trend_up:
                long_score += 1
            if vol_compressing:  # Squeeze before breakout
                long_score += 1

            # --- SHORT signals (very conservative) ---
            short_score = 0
            if trend_down:
                short_score += 1
            if obv_falling:
                short_score += 1
            if sellers_aggressive:
                short_score += 1
            if rsi_val > 80:  # Only short extreme overbought
                short_score += 2
            if vol_expanding and trend_down:
                short_score += 1

            # Entry decisions - require higher score to reduce false signals
            if long_score >= 5:
                position = 1
                entry_price = price
                # Size: 90% of equity for longs
                position_size_usd = equity * 0.90
                fee = position_size_usd * fee_rate
                equity -= fee  # Entry fee
                stop_loss = price - 2.5 * atr_14[i]
                take_profit = price + 5.0 * atr_14[i]  # 2:1 R:R
                trailing_stop = stop_loss
                current_trade = {
                    "side": "LONG",
                    "entry_price": price,
                    "entry_time": timestamps[i],
                    "size_usd": round(position_size_usd, 2),
                    "stop_loss": round(stop_loss, 2),
                    "take_profit": round(take_profit, 2),
                }

            elif short_score >= 5:  # Higher threshold for shorts
                position = -1
                entry_price = price
                # Size: 40% of equity for shorts (asymmetric)
                position_size_usd = equity * 0.40
                fee = position_size_usd * fee_rate
                equity -= fee
                stop_loss = price + 2.5 * atr_14[i]
                take_profit = price - 4.0 * atr_14[i]
                trailing_stop = stop_loss
                current_trade = {
                    "side": "SHORT",
                    "entry_price": price,
                    "entry_time": timestamps[i],
                    "size_usd": round(position_size_usd, 2),
                    "stop_loss": round(stop_loss, 2),
                    "take_profit": round(take_profit, 2),
                }

        # Track drawdown
        if equity > peak_equity:
            peak_equity = equity
        dd = (peak_equity - equity) / peak_equity * 100
        if dd > max_drawdown_pct:
            max_drawdown_pct = dd

    # Close any open position at end
    if position != 0:
        exit_price = closes[-1]
        if position == 1:
            pnl_pct = (exit_price / entry_price - 1) * 100
            pnl_usd = position_size_usd * (exit_price / entry_price - 1) - position_size_usd * fee_rate
        else:
            pnl_pct = (1 - exit_price / entry_price) * 100
            pnl_usd = position_size_usd * (1 - exit_price / entry_price) - position_size_usd * fee_rate
        equity += pnl_usd
        current_trade["exit_price"] = exit_price
        current_trade["exit_time"] = timestamps[-1]
        current_trade["pnl_pct"] = round(pnl_pct, 2)
        current_trade["pnl_usd"] = round(pnl_usd, 2)
        current_trade["exit_reason"] = "end"
        trade_log.append(current_trade)

    # Calculate metrics
    total_return_pct = (equity / initial_capital - 1) * 100
    num_trades = len(trade_log)
    wins = [t for t in trade_log if t.get("pnl_usd", 0) > 0]
    win_rate = len(wins) / num_trades * 100 if num_trades > 0 else 0

    # Sharpe ratio (approximate from trade returns)
    if num_trades > 1:
        trade_returns = [t["pnl_pct"] / 100 for t in trade_log]
        avg_ret = np.mean(trade_returns)
        std_ret = np.std(trade_returns)
        # Annualize: ~6 candles/day * 365 = ~2190 4h candles/year
        # Approximate trades per year based on trading period
        days = (end_dt - start_dt).days
        trades_per_year = num_trades / days * 365 if days > 0 else num_trades
        sharpe = (avg_ret / std_ret) * np.sqrt(trades_per_year) if std_ret > 0 else 0
    else:
        sharpe = 0

    results = {
        "final_equity": round(equity, 2),
        "total_return_pct": round(total_return_pct, 2),
        "sharpe_ratio": round(sharpe, 2),
        "max_drawdown_pct": round(max_drawdown_pct, 2),
        "num_trades": num_trades,
        "win_rate": round(win_rate, 2),
        "trade_log": trade_log,
    }

    return results


if __name__ == "__main__":
    print("=" * 60)
    print("AGENT 2 - SENTIMENT TRADER - ROUND 2")
    print("=" * 60)

    print("\n--- TRAIN PERIOD (2024-04-01 to 2024-09-30) ---")
    train = run_backtest("2024-04-01", "2024-09-30")
    print(f"Final Equity: ${train['final_equity']:.2f}")
    print(f"Return: {train['total_return_pct']:.2f}%")
    print(f"Sharpe: {train['sharpe_ratio']:.2f}")
    print(f"Max DD: {train['max_drawdown_pct']:.2f}%")
    print(f"Trades: {train['num_trades']}")
    print(f"Win Rate: {train['win_rate']:.2f}%")

    print("\n--- TEST PERIOD (2024-10-01 to 2024-12-31) ---")
    test = run_backtest("2024-10-01", "2024-12-31")
    print(f"Final Equity: ${test['final_equity']:.2f}")
    print(f"Return: {test['total_return_pct']:.2f}%")
    print(f"Sharpe: {test['sharpe_ratio']:.2f}")
    print(f"Max DD: {test['max_drawdown_pct']:.2f}%")
    print(f"Trades: {test['num_trades']}")
    print(f"Win Rate: {test['win_rate']:.2f}%")

    # Save results
    output = {
        "agent": "Agent 2 - Sentiment Trader",
        "round": 2,
        "strategy": "Long-biased volume-sentiment (OBV + taker ratio + RSI + ATR squeeze)",
        "symbol": "BTCUSDT",
        "timeframe": "4h",
        "train": {k: v for k, v in train.items() if k != "trade_log"},
        "test": {k: v for k, v in test.items() if k != "trade_log"},
        "train_trade_log": train["trade_log"],
        "test_trade_log": test["trade_log"],
    }

    results_path = "/Users/rc/Projects/workspace/nautilus-trading/competition/agent-2-sentiment/round2/results.txt"
    with open(results_path, "w") as f:
        f.write("AGENT 2 - SENTIMENT TRADER - ROUND 2 RESULTS\n")
        f.write("=" * 50 + "\n\n")
        f.write(f"Strategy: Long-biased volume-sentiment\n")
        f.write(f"Symbol: BTCUSDT | Timeframe: 4h\n")
        f.write(f"Signals: OBV trend, taker buy ratio, RSI, ATR squeeze, EMA50 trend\n\n")

        f.write("TRAIN (2024-04-01 to 2024-09-30)\n")
        f.write("-" * 30 + "\n")
        f.write(f"Final Equity: ${train['final_equity']:.2f}\n")
        f.write(f"Return: {train['total_return_pct']:.2f}%\n")
        f.write(f"Sharpe: {train['sharpe_ratio']:.2f}\n")
        f.write(f"Max Drawdown: {train['max_drawdown_pct']:.2f}%\n")
        f.write(f"Trades: {train['num_trades']}\n")
        f.write(f"Win Rate: {train['win_rate']:.2f}%\n\n")

        f.write("TEST (2024-10-01 to 2024-12-31)\n")
        f.write("-" * 30 + "\n")
        f.write(f"Final Equity: ${test['final_equity']:.2f}\n")
        f.write(f"Return: {test['total_return_pct']:.2f}%\n")
        f.write(f"Sharpe: {test['sharpe_ratio']:.2f}\n")
        f.write(f"Max Drawdown: {test['max_drawdown_pct']:.2f}%\n")
        f.write(f"Trades: {test['num_trades']}\n")
        f.write(f"Win Rate: {test['win_rate']:.2f}%\n\n")

        f.write("TRADE LOG (TEST)\n")
        f.write("-" * 30 + "\n")
        for t in test["trade_log"]:
            f.write(json.dumps(t) + "\n")

        f.write(f"\nFull JSON:\n")
        f.write(json.dumps({k: v for k, v in output.items() if k not in ("train_trade_log", "test_trade_log")}, indent=2))

    print(f"\nResults saved to {results_path}")
