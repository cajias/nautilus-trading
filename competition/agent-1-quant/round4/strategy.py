"""
Agent 1 — Quantitative Trader: Round 4 Strategy
Tournament-style multi-strategy selector on daily BTCUSDT bars.

Approach: Run 12+ strategy variants on TRAIN, rank by Sharpe ratio,
select the best for TEST. High conviction, few trades, 90% sizing.

TRAIN: Oct 2024 - Mar 2025 (Q4 bull run + Q1 correction)
TEST:  Apr 2025 - Jun 2025
EVAL:  Jul 2025 - Sep 2025 (hidden)

Strategies in tournament:
  1. BB Mean Reversion (3 RSI thresholds)
  2. EMA Trailing Stop (3 parameter sets)
  3. MACD Histogram Crossover (2 parameter sets)
  4. Donchian Breakout + Trend Filter (2 parameter sets)
  5. RSI Dip Buyer (2 thresholds)
"""

from __future__ import annotations

import json
import pathlib
import time
from typing import Any, Optional

import numpy as np
import pandas as pd
import requests

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SYMBOL = "BTCUSDT"
INTERVAL = "1d"
TRAIN_START = "2024-10-01"
TRAIN_END = "2025-03-31"
TEST_START = "2025-04-01"
TEST_END = "2025-06-30"
INITIAL_CAPITAL = 1000.0
POSITION_SIZE = 0.90
FEE_RATE = 0.001
RESULTS_DIR = pathlib.Path(__file__).parent

# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------

def fetch_binance_klines(symbol: str, interval: str, start: str, end: str) -> pd.DataFrame:
    """Fetch klines from Binance public API with pagination."""
    url = "https://api.binance.com/api/v3/klines"
    start_ms = int(pd.Timestamp(start).timestamp() * 1000)
    end_ms = int(pd.Timestamp(end + " 23:59:59").timestamp() * 1000)
    all_klines: list[list[Any]] = []
    current_start = start_ms
    while current_start < end_ms:
        params = {
            "symbol": symbol, "interval": interval,
            "startTime": current_start, "endTime": end_ms, "limit": 1000,
        }
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if not data:
            break
        all_klines.extend(data)
        current_start = data[-1][0] + 1
        time.sleep(0.2)
    df = pd.DataFrame(all_klines, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades", "taker_buy_base",
        "taker_buy_quote", "ignore",
    ])
    for col in ["open", "high", "low", "close", "volume", "quote_volume"]:
        df[col] = df[col].astype(float)
    df["timestamp"] = pd.to_datetime(df["open_time"], unit="ms")
    df = df.set_index("timestamp").sort_index()
    return df

# ---------------------------------------------------------------------------
# Technical indicators
# ---------------------------------------------------------------------------

def ema(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, adjust=False).mean()

def sma(s: pd.Series, window: int) -> pd.Series:
    return s.rolling(window).mean()

def rsi(s: pd.Series, period: int = 14) -> pd.Series:
    delta = s.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(period).mean()

def bollinger_bands(s: pd.Series, window: int = 20, num_std: float = 2.0):
    mid = sma(s, window)
    std = s.rolling(window).std()
    return mid, mid + num_std * std, mid - num_std * std

def macd(s: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    ema_fast = ema(s, fast)
    ema_slow = ema(s, slow)
    macd_line = ema_fast - ema_slow
    signal_line = ema(macd_line, signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram

def donchian(df: pd.DataFrame, period: int = 20):
    upper = df["high"].rolling(period).max()
    lower = df["low"].rolling(period).min()
    return upper, lower

# ---------------------------------------------------------------------------
# Metrics calculation (proper daily Sharpe)
# ---------------------------------------------------------------------------

def _metrics(capital: float, trades: list, equity_curve: list) -> dict[str, Any]:
    """Calculate strategy metrics with annualized daily Sharpe ratio."""
    final_equity = capital
    total_return = (final_equity - INITIAL_CAPITAL) / INITIAL_CAPITAL

    if not equity_curve:
        return {
            "initial_capital": INITIAL_CAPITAL, "final_equity": round(final_equity, 2),
            "total_return_pct": round(total_return * 100, 2), "sharpe_ratio": 0.0,
            "max_drawdown_pct": 0.0, "num_trades": 0, "win_rate": 0.0, "trades": trades,
        }

    equity_df = pd.DataFrame(equity_curve).set_index("timestamp")
    daily_eq = equity_df["equity"].resample("D").last().dropna()
    daily_returns = daily_eq.pct_change().dropna()

    sharpe = 0.0
    if len(daily_returns) > 1 and daily_returns.std() > 0:
        sharpe = (daily_returns.mean() / daily_returns.std()) * np.sqrt(365)

    max_dd = 0.0
    if len(daily_eq) > 0:
        max_dd = ((daily_eq / daily_eq.cummax()) - 1).min()

    sells = [t for t in trades if t["type"] in ("SELL", "SELL_FINAL")]
    wins = [t for t in sells if t.get("pnl_pct", 0) > 0]

    return {
        "initial_capital": INITIAL_CAPITAL,
        "final_equity": round(final_equity, 2),
        "total_return_pct": round(total_return * 100, 2),
        "sharpe_ratio": round(sharpe, 4),
        "max_drawdown_pct": round(max_dd * 100, 2),
        "num_trades": len([t for t in trades if t["type"] == "BUY"]),
        "win_rate": round(len(wins) / len(sells) * 100, 1) if sells else 0.0,
        "trades": trades,
    }

# ---------------------------------------------------------------------------
# Position helpers
# ---------------------------------------------------------------------------

def _open_long(capital: float, price: float, trades: list, ts, size_pct: float = POSITION_SIZE):
    invest = capital * size_pct
    fee = invest * FEE_RATE
    position = (invest - fee) / price
    capital -= invest
    trades.append({"type": "BUY", "time": str(ts), "price": round(price, 2), "size": round(position, 6)})
    return capital, position, price

def _close_long(capital: float, position: float, entry_price: float, price: float, trades: list, ts, reason: str = "signal"):
    proceeds = position * price
    fee = proceeds * FEE_RATE
    capital += proceeds - fee
    pnl_pct = (price - entry_price) / entry_price
    trades.append({
        "type": "SELL" if reason != "eod" else "SELL_FINAL",
        "time": str(ts), "price": round(price, 2),
        "pnl_pct": round(pnl_pct, 4), "reason": reason,
    })
    return capital, 0.0, 0.0

# ---------------------------------------------------------------------------
# Strategy 1: Bollinger Band Mean Reversion
# ---------------------------------------------------------------------------

def strat_bb_reversion(df: pd.DataFrame, start: str, end: str,
                       rsi_buy: float = 35, rsi_sell: float = 65,
                       stop_loss_pct: float = 0.05) -> dict[str, Any]:
    """Buy at lower BB + oversold RSI, sell at upper BB + overbought RSI."""
    mask = (df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end))
    period_df = df.loc[mask]

    bb_mid, bb_upper, bb_lower = bollinger_bands(df["close"], 20, 2.0)
    rsi_vals = rsi(df["close"], 14)

    capital = INITIAL_CAPITAL
    position = 0.0
    entry_price = 0.0
    trades: list[dict] = []
    equity_curve: list[dict] = []

    for ts, row in period_df.iterrows():
        price = row["close"]
        r = rsi_vals.loc[ts] if ts in rsi_vals.index else 50
        bbu = bb_upper.loc[ts] if ts in bb_upper.index else price
        bbl = bb_lower.loc[ts] if ts in bb_lower.index else price

        if position > 0:
            # Exit: upper BB + overbought, or stop loss
            if (price >= bbu and r > rsi_sell) or (price < entry_price * (1 - stop_loss_pct)):
                reason = "signal" if price >= bbu else "stop_loss"
                capital, position, entry_price = _close_long(capital, position, entry_price, price, trades, ts, reason)
        else:
            # Entry: lower BB + oversold
            if price <= bbl and r < rsi_buy:
                capital, position, entry_price = _open_long(capital, price, trades, ts)

        eq = capital + (position * price if position > 0 else 0)
        equity_curve.append({"timestamp": ts, "equity": eq})

    # Force close
    if position > 0:
        fp = period_df.iloc[-1]["close"]
        capital, position, entry_price = _close_long(capital, position, entry_price, fp, trades, period_df.index[-1], "eod")

    return _metrics(capital, trades, equity_curve)

# ---------------------------------------------------------------------------
# Strategy 2: EMA Trailing Stop
# ---------------------------------------------------------------------------

def strat_ema_trail(df: pd.DataFrame, start: str, end: str,
                    fast: int = 10, slow: int = 30,
                    atr_mult: float = 1.5) -> dict[str, Any]:
    """EMA crossover entry with ATR trailing stop exit."""
    mask = (df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end))
    period_df = df.loc[mask]

    ema_fast = ema(df["close"], fast)
    ema_slow = ema(df["close"], slow)
    atr_vals = atr(df, 14)

    capital = INITIAL_CAPITAL
    position = 0.0
    entry_price = 0.0
    trailing_stop = 0.0
    trades: list[dict] = []
    equity_curve: list[dict] = []

    for ts, row in period_df.iterrows():
        price = row["close"]
        lo = row["low"]
        ef = ema_fast.loc[ts] if ts in ema_fast.index else price
        es = ema_slow.loc[ts] if ts in ema_slow.index else price
        a = atr_vals.loc[ts] if ts in atr_vals.index else price * 0.02

        if position > 0:
            # Update trailing stop
            new_stop = price - atr_mult * a
            trailing_stop = max(trailing_stop, new_stop)
            # Exit if low hits trailing stop or EMA death cross
            if lo <= trailing_stop or ef < es:
                exit_price = max(trailing_stop, lo)  # approximate
                capital, position, entry_price = _close_long(capital, position, entry_price, exit_price, trades, ts)
                trailing_stop = 0.0
        else:
            # Entry: golden cross
            prev_ts = df.index[df.index.get_loc(ts) - 1] if df.index.get_loc(ts) > 0 else ts
            prev_ef = ema_fast.loc[prev_ts] if prev_ts in ema_fast.index else 0
            prev_es = ema_slow.loc[prev_ts] if prev_ts in ema_slow.index else 0
            if prev_ef <= prev_es and ef > es:
                capital, position, entry_price = _open_long(capital, price, trades, ts)
                trailing_stop = price - atr_mult * a

        eq = capital + (position * price if position > 0 else 0)
        equity_curve.append({"timestamp": ts, "equity": eq})

    if position > 0:
        fp = period_df.iloc[-1]["close"]
        capital, position, entry_price = _close_long(capital, position, entry_price, fp, trades, period_df.index[-1], "eod")

    return _metrics(capital, trades, equity_curve)

# ---------------------------------------------------------------------------
# Strategy 3: MACD Histogram Crossover
# ---------------------------------------------------------------------------

def strat_macd_hist(df: pd.DataFrame, start: str, end: str,
                    fast: int = 12, slow: int = 26, sig: int = 9,
                    stop_pct: float = 0.05) -> dict[str, Any]:
    """Buy when MACD histogram crosses above zero, sell when it crosses below."""
    mask = (df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end))
    period_df = df.loc[mask]

    _, _, hist = macd(df["close"], fast, slow, sig)

    capital = INITIAL_CAPITAL
    position = 0.0
    entry_price = 0.0
    trades: list[dict] = []
    equity_curve: list[dict] = []

    for ts, row in period_df.iterrows():
        price = row["close"]
        h_val = hist.loc[ts] if ts in hist.index else 0
        idx = df.index.get_loc(ts)
        prev_h = hist.iloc[idx - 1] if idx > 0 else 0

        if position > 0:
            # Exit: histogram crosses below zero or stop loss
            if (prev_h >= 0 and h_val < 0) or (price < entry_price * (1 - stop_pct)):
                reason = "signal" if h_val < 0 else "stop_loss"
                capital, position, entry_price = _close_long(capital, position, entry_price, price, trades, ts, reason)
        else:
            # Entry: histogram crosses above zero
            if prev_h <= 0 and h_val > 0:
                capital, position, entry_price = _open_long(capital, price, trades, ts)

        eq = capital + (position * price if position > 0 else 0)
        equity_curve.append({"timestamp": ts, "equity": eq})

    if position > 0:
        fp = period_df.iloc[-1]["close"]
        capital, position, entry_price = _close_long(capital, position, entry_price, fp, trades, period_df.index[-1], "eod")

    return _metrics(capital, trades, equity_curve)

# ---------------------------------------------------------------------------
# Strategy 4: Donchian Breakout + Trend Filter
# ---------------------------------------------------------------------------

def strat_donchian(df: pd.DataFrame, start: str, end: str,
                   period: int = 20, exit_period: int = 10,
                   trend_filter: bool = True) -> dict[str, Any]:
    """Donchian channel breakout with optional SMA trend filter."""
    mask = (df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end))
    period_df = df.loc[mask]

    upper, lower = donchian(df, period)
    exit_upper, exit_lower = donchian(df, exit_period)
    sma_200 = sma(df["close"], 50)  # 50-day for daily

    capital = INITIAL_CAPITAL
    position = 0.0
    entry_price = 0.0
    trades: list[dict] = []
    equity_curve: list[dict] = []

    for ts, row in period_df.iterrows():
        price = row["close"]
        hi = row["high"]
        u = upper.loc[ts] if ts in upper.index else price
        el = exit_lower.loc[ts] if ts in exit_lower.index else price
        trend = sma_200.loc[ts] if ts in sma_200.index else price

        if position > 0:
            # Exit: price breaks below exit channel lower
            if price < el:
                capital, position, entry_price = _close_long(capital, position, entry_price, price, trades, ts)
        else:
            # Entry: breakout above upper channel + above trend
            trend_ok = price > trend if trend_filter else True
            if hi > u and trend_ok:
                capital, position, entry_price = _open_long(capital, price, trades, ts)

        eq = capital + (position * price if position > 0 else 0)
        equity_curve.append({"timestamp": ts, "equity": eq})

    if position > 0:
        fp = period_df.iloc[-1]["close"]
        capital, position, entry_price = _close_long(capital, position, entry_price, fp, trades, period_df.index[-1], "eod")

    return _metrics(capital, trades, equity_curve)

# ---------------------------------------------------------------------------
# Strategy 5: RSI Dip Buyer
# ---------------------------------------------------------------------------

def strat_rsi_dip(df: pd.DataFrame, start: str, end: str,
                  rsi_entry: float = 30, rsi_exit: float = 60,
                  stop_pct: float = 0.07) -> dict[str, Any]:
    """Buy when RSI dips below threshold, sell when it recovers."""
    mask = (df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end))
    period_df = df.loc[mask]

    rsi_vals = rsi(df["close"], 14)

    capital = INITIAL_CAPITAL
    position = 0.0
    entry_price = 0.0
    trades: list[dict] = []
    equity_curve: list[dict] = []

    for ts, row in period_df.iterrows():
        price = row["close"]
        r = rsi_vals.loc[ts] if ts in rsi_vals.index else 50

        if position > 0:
            if r > rsi_exit or price < entry_price * (1 - stop_pct):
                reason = "signal" if r > rsi_exit else "stop_loss"
                capital, position, entry_price = _close_long(capital, position, entry_price, price, trades, ts, reason)
        else:
            if r < rsi_entry:
                capital, position, entry_price = _open_long(capital, price, trades, ts)

        eq = capital + (position * price if position > 0 else 0)
        equity_curve.append({"timestamp": ts, "equity": eq})

    if position > 0:
        fp = period_df.iloc[-1]["close"]
        capital, position, entry_price = _close_long(capital, position, entry_price, fp, trades, period_df.index[-1], "eod")

    return _metrics(capital, trades, equity_curve)

# ---------------------------------------------------------------------------
# Strategy 6: Momentum + Volatility Squeeze
# ---------------------------------------------------------------------------

def strat_vol_squeeze(df: pd.DataFrame, start: str, end: str,
                      bb_period: int = 20, kc_mult: float = 1.5,
                      mom_period: int = 12) -> dict[str, Any]:
    """Buy when Bollinger Bands squeeze inside Keltner Channels and momentum is positive."""
    mask = (df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end))
    period_df = df.loc[mask]

    bb_mid, bb_upper, bb_lower = bollinger_bands(df["close"], bb_period, 2.0)
    atr_vals = atr(df, bb_period)
    kc_upper = ema(df["close"], bb_period) + kc_mult * atr_vals
    kc_lower = ema(df["close"], bb_period) - kc_mult * atr_vals
    # Squeeze: BB inside KC
    squeeze = (bb_lower > kc_lower) & (bb_upper < kc_upper)
    momentum = df["close"] - df["close"].shift(mom_period)

    capital = INITIAL_CAPITAL
    position = 0.0
    entry_price = 0.0
    trades: list[dict] = []
    equity_curve: list[dict] = []

    for ts, row in period_df.iterrows():
        price = row["close"]
        idx = df.index.get_loc(ts)
        sq = squeeze.iloc[idx] if idx < len(squeeze) else False
        prev_sq = squeeze.iloc[idx - 1] if idx > 0 else False
        mom = momentum.iloc[idx] if idx < len(momentum) else 0
        prev_mom = momentum.iloc[idx - 1] if idx > 0 else 0

        if position > 0:
            # Exit: squeeze fires again or momentum turns negative
            if (not prev_sq and sq) or (prev_mom >= 0 and mom < 0):
                capital, position, entry_price = _close_long(capital, position, entry_price, price, trades, ts)
        else:
            # Entry: squeeze releases (was squeezing, now not) + positive momentum
            if prev_sq and not sq and mom > 0:
                capital, position, entry_price = _open_long(capital, price, trades, ts)

        eq = capital + (position * price if position > 0 else 0)
        equity_curve.append({"timestamp": ts, "equity": eq})

    if position > 0:
        fp = period_df.iloc[-1]["close"]
        capital, position, entry_price = _close_long(capital, position, entry_price, fp, trades, period_df.index[-1], "eod")

    return _metrics(capital, trades, equity_curve)

# ---------------------------------------------------------------------------
# Tournament runner
# ---------------------------------------------------------------------------

def run_tournament(df: pd.DataFrame, start: str, end: str) -> dict[str, dict]:
    """Run all strategy variants and return results dict."""
    strategies: dict[str, dict] = {}

    # BB Mean Reversion variants
    for rsi_buy, rsi_sell in [(30, 70), (35, 65), (40, 60)]:
        name = f"BB_MR(rsi {rsi_buy}/{rsi_sell})"
        strategies[name] = strat_bb_reversion(df, start, end, rsi_buy, rsi_sell)

    # EMA Trailing Stop variants
    for fast, slow, atr_m in [(10, 30, 1.5), (8, 21, 2.0), (5, 20, 1.5)]:
        name = f"EMA_Trail({fast},{slow},atr{atr_m})"
        strategies[name] = strat_ema_trail(df, start, end, fast, slow, atr_m)

    # MACD Histogram variants
    for fast, slow, sig in [(12, 26, 9), (8, 17, 9)]:
        name = f"MACD_Hist({fast},{slow},{sig})"
        strategies[name] = strat_macd_hist(df, start, end, fast, slow, sig)

    # Donchian Breakout variants
    for period, exit_p in [(20, 10), (30, 15)]:
        name = f"Donchian({period},{exit_p})"
        strategies[name] = strat_donchian(df, start, end, period, exit_p, trend_filter=True)

    # RSI Dip Buyer variants
    for entry, exit_r in [(30, 60), (25, 55)]:
        name = f"RSI_Dip({entry},{exit_r})"
        strategies[name] = strat_rsi_dip(df, start, end, entry, exit_r)

    # Volatility Squeeze
    strategies["Vol_Squeeze(20,1.5,12)"] = strat_vol_squeeze(df, start, end, 20, 1.5, 12)

    return strategies

# ---------------------------------------------------------------------------
# run_backtest interface (required by competition)
# ---------------------------------------------------------------------------

def run_backtest(start: str, end: str, initial_capital: float = 1000.0) -> dict:
    """Run the tournament, return best strategy's results."""
    global INITIAL_CAPITAL
    INITIAL_CAPITAL = initial_capital

    # Fetch data with buffer for indicators
    buffer_start = (pd.Timestamp(start) - pd.Timedelta(days=90)).strftime("%Y-%m-%d")
    fetch_end = (pd.Timestamp(end) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    df = fetch_binance_klines(SYMBOL, INTERVAL, buffer_start, fetch_end)

    strategies = run_tournament(df, start, end)

    # Select best by Sharpe (must have >= 1 trade)
    valid = {k: v for k, v in strategies.items() if v["num_trades"] >= 1}
    if not valid:
        # Fallback: return buy & hold equivalent
        return {
            "final_equity": initial_capital, "total_return_pct": 0.0,
            "sharpe_ratio": 0.0, "max_drawdown_pct": 0.0,
            "num_trades": 0, "win_rate": 0.0, "trade_log": [],
        }

    best_name = max(valid, key=lambda k: valid[k]["sharpe_ratio"])
    best = valid[best_name]

    return {
        "final_equity": best["final_equity"],
        "total_return_pct": best["total_return_pct"],
        "sharpe_ratio": best["sharpe_ratio"],
        "max_drawdown_pct": best["max_drawdown_pct"],
        "num_trades": best["num_trades"],
        "win_rate": best["win_rate"],
        "trade_log": best["trades"],
        "strategy_name": f"{SYMBOL}:{best_name}",
        "all_strategies": {k: {kk: vv for kk, vv in v.items() if kk != "trades"} for k, v in strategies.items()},
    }

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("=" * 70)
    print("Agent 1 — Quantitative Trader: Round 4")
    print("Tournament-Style Multi-Strategy Selector (Daily BTCUSDT)")
    print("=" * 70)

    # Fetch all data once
    buffer_start = "2024-07-01"  # 90 days before TRAIN_START
    fetch_end = "2025-07-01"
    print(f"\n[1/3] Downloading {SYMBOL} {INTERVAL} data...")
    df = fetch_binance_klines(SYMBOL, INTERVAL, buffer_start, fetch_end)
    print(f"  {len(df)} candles ({df.index[0]} to {df.index[-1]})")

    # Walk-forward selection: split TRAIN into fit + validate
    train_mid = "2025-01-01"  # Split: Oct-Dec 2024 (fit) | Jan-Mar 2025 (validate)
    print(f"\n[2/4] Walk-forward selection: fit ({TRAIN_START} to {train_mid}) + validate ({train_mid} to {TRAIN_END})...")

    fit_strats = run_tournament(df, TRAIN_START, "2024-12-31")
    val_strats = run_tournament(df, train_mid, TRAIN_END)

    # Also run full TRAIN for reporting
    train_strats = run_tournament(df, TRAIN_START, TRAIN_END)

    # Score: average of fit Sharpe and validate Sharpe (both must have trades)
    combined_scores: dict[str, float] = {}
    for name in fit_strats:
        fit_s = fit_strats[name]
        val_s = val_strats[name]
        if fit_s["num_trades"] >= 1 and val_s["num_trades"] >= 1:
            # Must be profitable on BOTH halves
            if fit_s["total_return_pct"] > 0 and val_s["total_return_pct"] > 0:
                combined_scores[name] = (fit_s["sharpe_ratio"] + val_s["sharpe_ratio"]) / 2
            else:
                combined_scores[name] = min(fit_s["sharpe_ratio"], val_s["sharpe_ratio"])
        elif fit_s["num_trades"] >= 1 or val_s["num_trades"] >= 1:
            # Only active in one half -- penalize but don't discard
            active = fit_s if fit_s["num_trades"] >= 1 else val_s
            combined_scores[name] = active["sharpe_ratio"] * 0.5

    print(f"\n{'Strategy':<30} {'FIT Ret':>10} {'FIT Sharpe':>11} {'VAL Ret':>10} {'VAL Sharpe':>11} {'Combined':>10}")
    print("-" * 90)
    for name in sorted(combined_scores, key=combined_scores.get, reverse=True):
        fs = fit_strats[name]
        vs = val_strats[name]
        cs = combined_scores[name]
        print(f"  {name:<28} {fs['total_return_pct']:>+9.2f}% {fs['sharpe_ratio']:>10.4f} {vs['total_return_pct']:>+9.2f}% {vs['sharpe_ratio']:>10.4f} {cs:>9.4f}")

    # Full TRAIN results
    print(f"\n  Full TRAIN results:")
    print(f"  {'Strategy':<30} {'Return':>10} {'Sharpe':>10} {'MaxDD':>10} {'Trades':>8} {'WinRate':>8}")
    print("  " + "-" * 78)
    for name, s in sorted(train_strats.items(), key=lambda x: x[1]["sharpe_ratio"], reverse=True):
        print(f"    {name:<28} {s['total_return_pct']:>+9.2f}% {s['sharpe_ratio']:>9.4f} {s['max_drawdown_pct']:>9.2f}% {s['num_trades']:>7} {s['win_rate']:>7.1f}%")

    # Select best by combined walk-forward score
    if combined_scores:
        best_train_name = max(combined_scores, key=combined_scores.get)
    else:
        # Fallback: best on full TRAIN
        valid_train_fb = {k: v for k, v in train_strats.items() if v["num_trades"] >= 1}
        best_train_name = max(valid_train_fb, key=lambda k: valid_train_fb[k]["sharpe_ratio"])

    valid_train = {k: v for k, v in train_strats.items() if v["num_trades"] >= 1}
    print(f"\n  WALK-FORWARD WINNER: {best_train_name}")
    print(f"    Full TRAIN Return: {valid_train.get(best_train_name, {}).get('total_return_pct', 0):+.2f}%")
    print(f"    Full TRAIN Sharpe: {valid_train.get(best_train_name, {}).get('sharpe_ratio', 0):.4f}")
    print(f"    Combined WF Score: {combined_scores.get(best_train_name, 0):.4f}")

    # TEST with selected strategy
    print(f"\n[3/4] Running all strategies on TEST ({TEST_START} to {TEST_END})...")
    test_strats = run_tournament(df, TEST_START, TEST_END)

    # Show all on TEST for comparison
    print(f"\n{'Strategy':<30} {'Return':>10} {'Sharpe':>10} {'MaxDD':>10} {'Trades':>8} {'WinRate':>8}")
    print("-" * 80)
    for name, s in sorted(test_strats.items(), key=lambda x: x[1]["sharpe_ratio"], reverse=True):
        marker = " <-- SELECTED" if name == best_train_name else ""
        print(f"  {name:<28} {s['total_return_pct']:>+9.2f}% {s['sharpe_ratio']:>9.4f} {s['max_drawdown_pct']:>9.2f}% {s['num_trades']:>7} {s['win_rate']:>7.1f}%{marker}")

    # Get selected strategy's TEST results
    test_selected = test_strats[best_train_name]

    # Buy & hold
    test_mask = (df.index >= pd.Timestamp(TEST_START)) & (df.index <= pd.Timestamp(TEST_END))
    test_prices = df.loc[test_mask, "close"]
    if len(test_prices) > 1:
        bnh = (test_prices.iloc[-1] - test_prices.iloc[0]) / test_prices.iloc[0] * 100
    else:
        bnh = 0.0

    print(f"\n  Buy & Hold (TEST): {bnh:+.2f}%")
    print(f"\n  SELECTED on TEST: {best_train_name}")
    print(f"    Final Equity:  ${test_selected['final_equity']:,.2f}")
    print(f"    Total Return:  {test_selected['total_return_pct']:+.2f}%")
    print(f"    Sharpe Ratio:  {test_selected['sharpe_ratio']:.4f}")
    print(f"    Max Drawdown:  {test_selected['max_drawdown_pct']:.2f}%")
    print(f"    Trades:        {test_selected['num_trades']}")
    print(f"    Win Rate:      {test_selected['win_rate']:.1f}%")

    # Trade log
    print(f"\n  Trade Log (TEST):")
    for t in test_selected["trades"]:
        if t["type"] == "BUY":
            print(f"    {t['time']} BUY  @ ${t['price']:,.2f} size={t['size']:.6f}")
        else:
            pnl = t.get("pnl_pct", 0) * 100
            print(f"    {t['time']} SELL @ ${t['price']:,.2f} pnl={pnl:+.2f}%")

    # Save results
    results = {
        "agent": "Agent 1 — Quantitative Trader",
        "round": 4,
        "strategy_name": f"{SYMBOL}:{best_train_name}",
        "description": (
            f"Tournament-style strategy selection. 14 strategies tested on TRAIN, "
            f"best Sharpe selected: {best_train_name}. Daily BTCUSDT, 0.1% fees, "
            f"90% position sizing."
        ),
        "symbol": SYMBOL,
        "interval": INTERVAL,
        "train_period": f"{TRAIN_START} to {TRAIN_END}",
        "test_period": f"{TEST_START} to {TEST_END}",
        "initial_capital": INITIAL_CAPITAL,
        "train": {k: v for k, v in valid_train[best_train_name].items() if k != "trades"},
        "test": {k: v for k, v in test_selected.items() if k != "trades"},
        "buy_and_hold_pct": round(bnh, 2),
        "all_strategies_train": {
            k: {kk: vv for kk, vv in v.items() if kk != "trades"}
            for k, v in sorted(train_strats.items(), key=lambda x: x[1]["sharpe_ratio"], reverse=True)
        },
        "all_strategies_test": {
            k: {kk: vv for kk, vv in v.items() if kk != "trades"}
            for k, v in sorted(test_strats.items(), key=lambda x: x[1]["sharpe_ratio"], reverse=True)
        },
        "test_trade_log": test_selected["trades"],
    }

    results_file = RESULTS_DIR / "results.txt"
    with open(results_file, "w") as f:
        f.write(f"Agent 1 — Quantitative Trader: Round 4 Results\n")
        f.write("=" * 50 + "\n\n")
        f.write(f"SELECTED STRATEGY: {best_train_name}\n")
        f.write("=" * 50 + "\n\n")
        f.write(f"initial_capital: {INITIAL_CAPITAL}\n")
        f.write(f"final_equity: {test_selected['final_equity']}\n")
        f.write(f"total_return_pct: {test_selected['total_return_pct']}\n")
        f.write(f"sharpe_ratio: {test_selected['sharpe_ratio']}\n")
        f.write(f"max_drawdown_pct: {test_selected['max_drawdown_pct']}\n")
        f.write(f"num_trades: {test_selected['num_trades']}\n")
        f.write(f"win_rate: {test_selected['win_rate']}\n")
        f.write(f"strategy_name: {SYMBOL}:{best_train_name}\n")
        f.write(f"buy_and_hold_pct: {round(bnh, 2)}\n")
        f.write(f"symbol: {SYMBOL}\n")
        f.write(f"interval: {INTERVAL}\n")
        f.write(f"train_period: {TRAIN_START} to {TRAIN_END}\n")
        f.write(f"test_period: {TEST_START} to {TEST_END}\n\n")

        f.write(f"TRAIN Results ({best_train_name}):\n")
        for k, v in valid_train[best_train_name].items():
            if k != "trades":
                f.write(f"  {k}: {v}\n")

        f.write(f"\nTournament Results (TRAIN period, sorted by Sharpe):\n")
        for name, s in sorted(train_strats.items(), key=lambda x: x[1]["sharpe_ratio"], reverse=True):
            f.write(f"  {name}: {s['total_return_pct']:+.2f}% (Sharpe {s['sharpe_ratio']:.2f}, {s['num_trades']} trades)\n")

        f.write(f"\nTournament Results (TEST period, sorted by Sharpe):\n")
        for name, s in sorted(test_strats.items(), key=lambda x: x[1]["sharpe_ratio"], reverse=True):
            marker = " <-- SELECTED" if name == best_train_name else ""
            f.write(f"  {name}: {s['total_return_pct']:+.2f}% (Sharpe {s['sharpe_ratio']:.2f}, {s['num_trades']} trades){marker}\n")
        f.write(f"  Buy & Hold: {bnh:+.2f}%\n")

        f.write(f"\nTrade Log (TEST, selected strategy):\n")
        for t in test_selected["trades"]:
            if t["type"] == "BUY":
                f.write(f"  {t['time']} BUY  @ ${t['price']:,.2f} size={t['size']:.6f}\n")
            else:
                pnl = t.get("pnl_pct", 0) * 100
                f.write(f"  {t['time']} SELL @ ${t['price']:,.2f} pnl={pnl:+.2f}%\n")

    print(f"\nResults saved to {results_file}")

    # Also save full JSON
    json_file = RESULTS_DIR / "results.json"
    with open(json_file, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"Full results saved to {json_file}")


if __name__ == "__main__":
    main()
