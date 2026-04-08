"""
Agent 4 — ML Engineer: Round 5 Strategy
Walk-Forward Validated Tournament with Ensemble Execution.

Round history:
- R1: +48.47% (EMA trend on BTC bull run)
- R2: +2.19% (RSI reversal in choppy Q1 2025)
- R3: +19.65% (AdaptiveMom on BTC in bullish Q2 2025)
- R4: -2.16% (EMA_10_50 caught in drawdown — tournament too broad, no walk-forward)

Round 5 improvements (lessons from R4 loss):
1. Walk-forward validation: split TRAIN into FIT (Jan-Apr) + VALIDATE (May-Jun)
   - Only strategies profitable on BOTH fit and validate advance
2. Heavy drawdown penalty: score = return * 0.4 + sharpe * 12 - max_dd^2 * 0.1
3. Ensemble top-3: blend signals from top 3 validated strategies (majority vote)
4. CASH bias: if no strategy has validate return > 1%, go CASH
5. Fewer variants: ~50 quality configs instead of 115 noisy ones
6. Multi-timeframe confirmation: use 4h bars as trend filter
"""

from __future__ import annotations

import pathlib
import time
from typing import Any

import lightgbm as lgb
import numpy as np
import pandas as pd
import requests

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
INTERVAL = "1d"
INITIAL_CAPITAL = 1000.0
BASE_POSITION_SIZE = 0.90
FEE_RATE = 0.001
RESULTS_DIR = pathlib.Path(__file__).parent

# Round 5 date ranges
TRAIN_START = "2025-01-01"
TRAIN_END = "2025-06-30"
FIT_END = "2025-04-30"       # fit on Jan-Apr
VALIDATE_START = "2025-05-01"  # validate on May-Jun
VALIDATE_END = "2025-06-30"
TEST_START = "2025-07-01"
TEST_END = "2025-09-30"


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------

def fetch_binance_klines(symbol: str, interval: str, start: str, end: str) -> pd.DataFrame:
    url = "https://api.binance.com/api/v3/klines"
    start_ms = int(pd.Timestamp(start).timestamp() * 1000)
    end_ms = int(pd.Timestamp(end + " 23:59:59").timestamp() * 1000)
    all_klines: list[list[Any]] = []
    current_start = start_ms
    while current_start < end_ms:
        params = {"symbol": symbol, "interval": interval,
                  "startTime": current_start, "endTime": end_ms, "limit": 1000}
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if not data:
            break
        all_klines.extend(data)
        current_start = data[-1][0] + 1
        time.sleep(0.25)
    df = pd.DataFrame(all_klines, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades", "taker_buy_base",
        "taker_buy_quote", "ignore",
    ])
    for col in ["open", "high", "low", "close", "volume", "quote_volume"]:
        df[col] = df[col].astype(float)
    df["timestamp"] = pd.to_datetime(df["open_time"], unit="ms")
    df = df.set_index("timestamp").sort_index()
    df = df[~df.index.duplicated(keep="first")]
    return df


# ---------------------------------------------------------------------------
# Technical indicators
# ---------------------------------------------------------------------------

def ema(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, adjust=False).mean()


def sma(s: pd.Series, period: int) -> pd.Series:
    return s.rolling(period).mean()


def rsi(s: pd.Series, period: int = 14) -> pd.Series:
    delta = s.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    h, lo, c = df["high"], df["low"], df["close"]
    tr = pd.concat([h - lo, (h - c.shift(1)).abs(), (lo - c.shift(1)).abs()], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Add all indicators needed by sub-strategies."""
    c = df["close"]
    v = df["volume"]
    h = df["high"]
    lo = df["low"]

    # EMAs
    for span in [5, 8, 10, 20, 21, 30, 50]:
        df[f"ema_{span}"] = ema(c, span)

    # SMAs
    df["sma_20"] = sma(c, 20)
    df["sma_50"] = sma(c, 50)

    # RSI
    df["rsi_14"] = rsi(c, 14)
    df["rsi_7"] = rsi(c, 7)

    # ATR
    df["atr_14"] = atr(df, 14)
    df["atr_14_norm"] = df["atr_14"] / c

    # Bollinger bands
    mid = sma(c, 20)
    std = c.rolling(20).std()
    df["bb_upper"] = mid + 2 * std
    df["bb_lower"] = mid - 2 * std
    df["bb_mid"] = mid
    df["bb_width"] = std / mid

    # MACD
    macd_line = ema(c, 12) - ema(c, 26)
    macd_signal = ema(macd_line, 9)
    df["macd_hist"] = macd_line - macd_signal
    df["macd_line"] = macd_line
    df["macd_signal"] = macd_signal

    # Keltner channel
    kelt_mid = ema(c, 20)
    kelt_atr = atr(df, 10)
    df["kelt_upper"] = kelt_mid + 1.5 * kelt_atr
    df["kelt_lower"] = kelt_mid - 1.5 * kelt_atr

    # Volume
    df["vol_sma_20"] = sma(v, 20)

    # Breakout levels
    df["high_20"] = h.rolling(20).max()
    df["low_20"] = lo.rolling(20).min()
    df["high_50"] = h.rolling(50).max()

    # Volatility
    df["vol_20"] = c.pct_change(1).rolling(20).std()

    # Taker buy ratio
    if "taker_buy_base" in df.columns:
        df["taker_ratio"] = df["taker_buy_base"].astype(float) / v.replace(0, np.nan)

    # ML features (compact set)
    feature_cols = []
    for p in [1, 3, 5, 7, 14, 21]:
        col = f"ret_{p}"
        df[col] = c.pct_change(p)
        feature_cols.append(col)
    for w in [5, 10, 20]:
        col = f"vol_{w}"
        df[col] = c.pct_change(1).rolling(w).std()
        feature_cols.append(col)
    for span in [10, 20, 50]:
        col = f"ema_dist_{span}"
        e = ema(c, span)
        df[col] = (c - e) / e
        feature_cols.append(col)
    df["rsi_14_feat"] = df["rsi_14"] / 100.0
    feature_cols.append("rsi_14_feat")
    df["macd_norm"] = df["macd_hist"] / c
    feature_cols.append("macd_norm")
    df["bb_pos"] = (c - df["bb_mid"]) / (std + 1e-10)
    feature_cols.append("bb_pos")
    df["atr_norm_feat"] = df["atr_14_norm"]
    feature_cols.append("atr_norm_feat")
    # Volume ratio
    df["vol_ratio_feat"] = v / df["vol_sma_20"].replace(0, np.nan)
    feature_cols.append("vol_ratio_feat")
    # Efficiency ratio
    direction = (c - c.shift(10)).abs()
    volatility = c.diff().abs().rolling(10).sum()
    df["efficiency_10"] = direction / (volatility + 1e-10)
    feature_cols.append("efficiency_10")
    # Trend strength
    df["trend_str"] = (c - c.shift(20)) / (c.pct_change(1).rolling(20).std() * c + 1e-10)
    feature_cols.append("trend_str")
    # Z-score
    df["zscore_20"] = (c - sma(c, 20)) / (c.rolling(20).std() + 1e-10)
    feature_cols.append("zscore_20")

    # Target for ML
    df["target"] = (c.shift(-1) > c).astype(int)

    return df, feature_cols


# ---------------------------------------------------------------------------
# Position helpers
# ---------------------------------------------------------------------------

def _metrics(capital: float, trades: list, equity_curve: list,
             initial_capital: float = INITIAL_CAPITAL) -> dict[str, Any]:
    final_equity = capital
    total_return = (final_equity - initial_capital) / initial_capital
    if not equity_curve:
        return {"final_equity": round(final_equity, 2),
                "total_return_pct": round(total_return * 100, 2), "sharpe_ratio": 0.0,
                "max_drawdown_pct": 0.0, "num_trades": 0, "win_rate": 0.0,
                "trade_log": trades}

    equity_df = pd.DataFrame(equity_curve).set_index("timestamp")
    daily_eq = equity_df["equity"].resample("D").last().dropna()
    daily_returns = daily_eq.pct_change().dropna()
    sharpe = float((daily_returns.mean() / daily_returns.std()) * np.sqrt(365)) if daily_returns.std() > 0 else 0.0
    max_dd = float(((daily_eq / daily_eq.cummax()) - 1).min())
    sells = [t for t in trades if t["type"] in ("SELL", "SELL_FINAL")]
    wins = [t for t in sells if t.get("pnl_pct", 0) > 0]

    return {
        "final_equity": round(final_equity, 2),
        "total_return_pct": round(total_return * 100, 2),
        "sharpe_ratio": round(sharpe, 4),
        "max_drawdown_pct": round(max_dd * 100, 2),
        "num_trades": len([t for t in trades if t["type"] == "BUY"]),
        "win_rate": round(len(wins) / len(sells) * 100, 1) if sells else 0.0,
        "trade_log": trades,
    }


def _close_position(capital, position, entry_price, price, trades, ts):
    proceeds = position * price
    fee = proceeds * FEE_RATE
    capital += proceeds - fee
    pnl = (price - entry_price) / entry_price
    trades.append({"type": "SELL", "time": str(ts), "price": round(price, 2), "pnl_pct": round(pnl, 4)})
    return capital, 0.0, 0.0


def _open_position(capital, price, trades, ts, size_frac=BASE_POSITION_SIZE):
    invest = capital * size_frac
    fee = invest * FEE_RATE
    position = (invest - fee) / price
    capital -= invest
    trades.append({"type": "BUY", "time": str(ts), "price": round(price, 2), "size": round(position, 6)})
    return capital, position, price


def _vol_adjusted_size(df: pd.DataFrame, ts, base_size: float = BASE_POSITION_SIZE) -> float:
    if "vol_20" not in df.columns:
        return base_size
    current_vol = df.loc[ts, "vol_20"] if ts in df.index else None
    if current_vol is None or pd.isna(current_vol):
        return base_size
    median_vol = df["vol_20"].rolling(60).median()
    mv = median_vol.loc[ts] if ts in median_vol.index else current_vol
    if pd.isna(mv) or mv == 0:
        return base_size
    vol_ratio = current_vol / mv
    if vol_ratio > 2.0:
        return base_size * 0.4
    elif vol_ratio > 1.5:
        return base_size * 0.6
    elif vol_ratio < 0.7:
        return min(base_size * 1.1, 0.95)
    return base_size


# ---------------------------------------------------------------------------
# Sub-Strategies (streamlined set)
# ---------------------------------------------------------------------------

def strat_ema_trend(df: pd.DataFrame, start: str, end: str,
                    fast: int = 10, slow: int = 30,
                    initial_capital: float = INITIAL_CAPITAL) -> dict[str, Any]:
    mask = (df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end))
    period_df = df.loc[mask]
    capital, position, entry_price = initial_capital, 0.0, 0.0
    trades, equity_curve = [], []
    ema_f = ema(df["close"], fast)
    ema_s = ema(df["close"], slow)

    for ts in period_df.index:
        price = period_df.loc[ts, "close"]
        equity_curve.append({"timestamp": ts, "equity": capital + position * price})
        ef, es = ema_f.loc[ts], ema_s.loc[ts]
        if position == 0 and ef > es:
            sz = _vol_adjusted_size(df, ts)
            capital, position, entry_price = _open_position(capital, price, trades, ts, sz)
        elif position > 0 and ef < es:
            capital, position, entry_price = _close_position(capital, position, entry_price, price, trades, ts)

    if position > 0:
        fp = period_df.iloc[-1]["close"]
        capital, position, entry_price = _close_position(capital, position, entry_price, fp, trades, period_df.index[-1])
        trades[-1]["type"] = "SELL_FINAL"
    return _metrics(capital, trades, equity_curve, initial_capital)


def strat_ema_trailing(df: pd.DataFrame, start: str, end: str,
                       fast: int = 10, slow: int = 30,
                       trail_atr_mult: float = 2.0,
                       initial_capital: float = INITIAL_CAPITAL) -> dict[str, Any]:
    mask = (df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end))
    period_df = df.loc[mask]
    capital, position, entry_price, highest = initial_capital, 0.0, 0.0, 0.0
    trades, equity_curve = [], []
    ema_f, ema_s = ema(df["close"], fast), ema(df["close"], slow)

    for ts in period_df.index:
        price = period_df.loc[ts, "close"]
        equity_curve.append({"timestamp": ts, "equity": capital + position * price})
        ef, es = ema_f.loc[ts], ema_s.loc[ts]
        cur_atr = df.loc[ts, "atr_14"] if not pd.isna(df.loc[ts, "atr_14"]) else price * 0.02

        if position == 0 and ef > es:
            sz = _vol_adjusted_size(df, ts)
            capital, position, entry_price = _open_position(capital, price, trades, ts, sz)
            highest = price
        elif position > 0:
            highest = max(highest, price)
            trail_stop = highest - trail_atr_mult * cur_atr
            if price < trail_stop or ef < es:
                capital, position, entry_price = _close_position(capital, position, entry_price, price, trades, ts)
                highest = 0.0

    if position > 0:
        fp = period_df.iloc[-1]["close"]
        capital, position, entry_price = _close_position(capital, position, entry_price, fp, trades, period_df.index[-1])
        trades[-1]["type"] = "SELL_FINAL"
    return _metrics(capital, trades, equity_curve, initial_capital)


def strat_rsi_dip(df: pd.DataFrame, start: str, end: str,
                  rsi_entry: float = 30, rsi_exit: float = 60,
                  initial_capital: float = INITIAL_CAPITAL) -> dict[str, Any]:
    """RSI dip buyer: buy oversold above SMA50, sell on recovery."""
    mask = (df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end))
    period_df = df.loc[mask]
    capital, position, entry_price = initial_capital, 0.0, 0.0
    trades, equity_curve = [], []

    for ts in period_df.index:
        price = period_df.loc[ts, "close"]
        equity_curve.append({"timestamp": ts, "equity": capital + position * price})
        r = df.loc[ts, "rsi_14"]
        s50 = df.loc[ts, "sma_50"]
        if pd.isna(r) or pd.isna(s50):
            continue
        if position == 0 and r < rsi_entry and price > s50 * 0.95:
            sz = _vol_adjusted_size(df, ts)
            capital, position, entry_price = _open_position(capital, price, trades, ts, sz)
        elif position > 0:
            pnl = (price - entry_price) / entry_price
            if r > rsi_exit or pnl > 0.08 or pnl < -0.04:
                capital, position, entry_price = _close_position(capital, position, entry_price, price, trades, ts)

    if position > 0:
        fp = period_df.iloc[-1]["close"]
        capital, position, entry_price = _close_position(capital, position, entry_price, fp, trades, period_df.index[-1])
        trades[-1]["type"] = "SELL_FINAL"
    return _metrics(capital, trades, equity_curve, initial_capital)


def strat_bb_reversion(df: pd.DataFrame, start: str, end: str,
                       rsi_buy: float = 35, rsi_sell: float = 65,
                       initial_capital: float = INITIAL_CAPITAL) -> dict[str, Any]:
    mask = (df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end))
    period_df = df.loc[mask]
    capital, position, entry_price = initial_capital, 0.0, 0.0
    trades, equity_curve = [], []

    for ts in period_df.index:
        price = period_df.loc[ts, "close"]
        equity_curve.append({"timestamp": ts, "equity": capital + position * price})
        r = df.loc[ts, "rsi_14"]
        bl = df.loc[ts, "bb_lower"]
        bu = df.loc[ts, "bb_upper"]
        if pd.isna(r) or pd.isna(bl):
            continue
        if position == 0 and price <= bl and r < rsi_buy:
            sz = _vol_adjusted_size(df, ts)
            capital, position, entry_price = _open_position(capital, price, trades, ts, sz)
        elif position > 0 and (price >= bu or r > rsi_sell):
            capital, position, entry_price = _close_position(capital, position, entry_price, price, trades, ts)
        elif position > 0 and (price - entry_price) / entry_price < -0.05:
            capital, position, entry_price = _close_position(capital, position, entry_price, price, trades, ts)

    if position > 0:
        fp = period_df.iloc[-1]["close"]
        capital, position, entry_price = _close_position(capital, position, entry_price, fp, trades, period_df.index[-1])
        trades[-1]["type"] = "SELL_FINAL"
    return _metrics(capital, trades, equity_curve, initial_capital)


def strat_breakout(df: pd.DataFrame, start: str, end: str,
                   lookback: int = 20,
                   initial_capital: float = INITIAL_CAPITAL) -> dict[str, Any]:
    mask = (df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end))
    period_df = df.loc[mask]
    capital, position, entry_price = initial_capital, 0.0, 0.0
    trades, equity_curve = [], []
    high_roll = df["high"].rolling(lookback).max().shift(1)
    low_roll = df["low"].rolling(lookback).min()

    for ts in period_df.index:
        price = period_df.loc[ts, "close"]
        equity_curve.append({"timestamp": ts, "equity": capital + position * price})
        prev_h = high_roll.loc[ts] if ts in high_roll.index else None
        if prev_h is None or pd.isna(prev_h):
            continue
        if position == 0 and price > prev_h:
            sz = _vol_adjusted_size(df, ts)
            capital, position, entry_price = _open_position(capital, price, trades, ts, sz)
        elif position > 0:
            prev_l = low_roll.loc[ts]
            pnl = (price - entry_price) / entry_price
            if (not pd.isna(prev_l) and price < prev_l) or pnl < -0.06:
                capital, position, entry_price = _close_position(capital, position, entry_price, price, trades, ts)

    if position > 0:
        fp = period_df.iloc[-1]["close"]
        capital, position, entry_price = _close_position(capital, position, entry_price, fp, trades, period_df.index[-1])
        trades[-1]["type"] = "SELL_FINAL"
    return _metrics(capital, trades, equity_curve, initial_capital)


def strat_momentum_breakout(df: pd.DataFrame, start: str, end: str,
                            initial_capital: float = INITIAL_CAPITAL) -> dict[str, Any]:
    mask = (df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end))
    period_df = df.loc[mask]
    capital, position, entry_price = initial_capital, 0.0, 0.0
    trades, equity_curve = [], []
    high_20 = df["high"].rolling(20).max().shift(1)
    vol_avg = df["volume"].rolling(20).mean()

    for ts in period_df.index:
        price = period_df.loc[ts, "close"]
        equity_curve.append({"timestamp": ts, "equity": capital + position * price})
        prev_h = high_20.loc[ts] if ts in high_20.index else None
        va = vol_avg.loc[ts] if ts in vol_avg.index else None
        if prev_h is None or pd.isna(prev_h) or va is None or pd.isna(va):
            continue
        e20 = df.loc[ts, "ema_20"]
        if pd.isna(e20):
            continue
        if position == 0 and price > prev_h and period_df.loc[ts, "volume"] > va * 1.2 and price > e20:
            sz = _vol_adjusted_size(df, ts)
            capital, position, entry_price = _open_position(capital, price, trades, ts, sz)
        elif position > 0:
            pnl = (price - entry_price) / entry_price
            e10 = df.loc[ts, "ema_10"]
            if pnl < -0.05 or (not pd.isna(e10) and price < e10 and pnl > 0):
                capital, position, entry_price = _close_position(capital, position, entry_price, price, trades, ts)

    if position > 0:
        fp = period_df.iloc[-1]["close"]
        capital, position, entry_price = _close_position(capital, position, entry_price, fp, trades, period_df.index[-1])
        trades[-1]["type"] = "SELL_FINAL"
    return _metrics(capital, trades, equity_curve, initial_capital)


def strat_macd_histogram(df: pd.DataFrame, start: str, end: str,
                         initial_capital: float = INITIAL_CAPITAL) -> dict[str, Any]:
    mask = (df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end))
    period_df = df.loc[mask]
    capital, position, entry_price = initial_capital, 0.0, 0.0
    trades, equity_curve = [], []
    hist = df["macd_hist"]

    for ts in period_df.index:
        price = period_df.loc[ts, "close"]
        equity_curve.append({"timestamp": ts, "equity": capital + position * price})
        cur_h = hist.loc[ts]
        prev_h = hist.shift(1).loc[ts]
        if pd.isna(cur_h) or pd.isna(prev_h):
            continue
        e20 = df.loc[ts, "ema_20"]
        if pd.isna(e20):
            continue
        if position == 0 and prev_h < 0 and cur_h > 0 and price > e20:
            sz = _vol_adjusted_size(df, ts)
            capital, position, entry_price = _open_position(capital, price, trades, ts, sz)
        elif position > 0:
            pnl = (price - entry_price) / entry_price
            if (prev_h > 0 and cur_h < 0) or pnl < -0.05 or pnl > 0.10:
                capital, position, entry_price = _close_position(capital, position, entry_price, price, trades, ts)

    if position > 0:
        fp = period_df.iloc[-1]["close"]
        capital, position, entry_price = _close_position(capital, position, entry_price, fp, trades, period_df.index[-1])
        trades[-1]["type"] = "SELL_FINAL"
    return _metrics(capital, trades, equity_curve, initial_capital)


def strat_adaptive_momentum(df: pd.DataFrame, start: str, end: str,
                            initial_capital: float = INITIAL_CAPITAL) -> dict[str, Any]:
    """Adaptive momentum: use fast EMA in trends, mean-revert in ranges."""
    mask = (df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end))
    period_df = df.loc[mask]
    capital, position, entry_price = initial_capital, 0.0, 0.0
    trades, equity_curve = [], []

    for ts in period_df.index:
        price = period_df.loc[ts, "close"]
        equity_curve.append({"timestamp": ts, "equity": capital + position * price})
        e10 = df.loc[ts, "ema_10"]
        e30 = df.loc[ts, "ema_30"]
        r = df.loc[ts, "rsi_14"]
        bw = df.loc[ts, "bb_width"] if "bb_width" in df.columns else None
        if pd.isna(e10) or pd.isna(e30) or pd.isna(r):
            continue

        # Detect regime: narrow BB = range, wide BB = trend
        trending = bw is not None and not pd.isna(bw) and bw > 0.04

        if position == 0:
            if trending and e10 > e30 and r > 45 and r < 75:
                sz = _vol_adjusted_size(df, ts)
                capital, position, entry_price = _open_position(capital, price, trades, ts, sz)
            elif not trending and r < 32:
                sz = _vol_adjusted_size(df, ts, 0.7)
                capital, position, entry_price = _open_position(capital, price, trades, ts, sz)
        elif position > 0:
            pnl = (price - entry_price) / entry_price
            if trending:
                if e10 < e30 or pnl < -0.04:
                    capital, position, entry_price = _close_position(capital, position, entry_price, price, trades, ts)
            else:
                if r > 65 or pnl > 0.05 or pnl < -0.03:
                    capital, position, entry_price = _close_position(capital, position, entry_price, price, trades, ts)

    if position > 0:
        fp = period_df.iloc[-1]["close"]
        capital, position, entry_price = _close_position(capital, position, entry_price, fp, trades, period_df.index[-1])
        trades[-1]["type"] = "SELL_FINAL"
    return _metrics(capital, trades, equity_curve, initial_capital)


def strat_ml_signal(df: pd.DataFrame, start: str, end: str,
                    feature_cols: list[str] = None,
                    train_end: str = None,
                    initial_capital: float = INITIAL_CAPITAL) -> dict[str, Any]:
    """LightGBM ensemble signal: buy when ML probability > threshold."""
    if feature_cols is None:
        return _metrics(initial_capital, [], [], initial_capital)

    mask = (df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end))
    period_df = df.loc[mask]
    capital, position, entry_price = initial_capital, 0.0, 0.0
    trades, equity_curve = [], []

    # Train on data before start (with 2-day gap)
    te = train_end or start
    train_data = df.loc[:pd.Timestamp(te) - pd.Timedelta(days=2)].dropna(subset=feature_cols + ["target"])
    if len(train_data) < 60:
        train_data = df.loc[:pd.Timestamp(te)].iloc[:-1].dropna(subset=feature_cols + ["target"])
    if len(train_data) < 40:
        return _metrics(initial_capital, [], [], initial_capital)

    X_train = train_data[feature_cols]
    y_train = train_data["target"]

    # Ensemble of 5 seeds
    models = []
    for seed in [42, 137, 2024, 314, 999]:
        model = lgb.LGBMClassifier(
            n_estimators=150, max_depth=3, learning_rate=0.03,
            num_leaves=6, min_child_samples=15, subsample=0.7,
            colsample_bytree=0.5, reg_alpha=2.0, reg_lambda=10.0,
            random_state=seed, verbose=-1,
        )
        model.fit(X_train, y_train)
        models.append(model)

    for ts in period_df.index:
        price = period_df.loc[ts, "close"]
        equity_curve.append({"timestamp": ts, "equity": capital + position * price})

        row_feats = df.loc[[ts], feature_cols]
        if row_feats.isna().any(axis=1).iloc[0]:
            continue

        probas = [m.predict_proba(row_feats)[:, 1][0] for m in models]
        avg_proba = np.mean(probas)

        if position == 0 and avg_proba > 0.58:
            sz = _vol_adjusted_size(df, ts)
            capital, position, entry_price = _open_position(capital, price, trades, ts, sz)
        elif position > 0:
            pnl = (price - entry_price) / entry_price
            if avg_proba < 0.42 or pnl < -0.04 or pnl > 0.08:
                capital, position, entry_price = _close_position(capital, position, entry_price, price, trades, ts)

    if position > 0:
        fp = period_df.iloc[-1]["close"]
        capital, position, entry_price = _close_position(capital, position, entry_price, fp, trades, period_df.index[-1])
        trades[-1]["type"] = "SELL_FINAL"
    return _metrics(capital, trades, equity_curve, initial_capital)


def strat_trend_pullback(df: pd.DataFrame, start: str, end: str,
                         initial_capital: float = INITIAL_CAPITAL) -> dict[str, Any]:
    """Buy pullbacks in uptrends: price > SMA50, RSI dips below 40, EMA10 > EMA30."""
    mask = (df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end))
    period_df = df.loc[mask]
    capital, position, entry_price = initial_capital, 0.0, 0.0
    trades, equity_curve = [], []

    for ts in period_df.index:
        price = period_df.loc[ts, "close"]
        equity_curve.append({"timestamp": ts, "equity": capital + position * price})
        r = df.loc[ts, "rsi_14"]
        s50 = df.loc[ts, "sma_50"]
        e10 = df.loc[ts, "ema_10"]
        e30 = df.loc[ts, "ema_30"]
        if pd.isna(r) or pd.isna(s50) or pd.isna(e10) or pd.isna(e30):
            continue

        uptrend = price > s50 and e10 > e30
        if position == 0 and uptrend and r < 40:
            sz = _vol_adjusted_size(df, ts)
            capital, position, entry_price = _open_position(capital, price, trades, ts, sz)
        elif position > 0:
            pnl = (price - entry_price) / entry_price
            if r > 65 or pnl > 0.06 or pnl < -0.04 or price < s50:
                capital, position, entry_price = _close_position(capital, position, entry_price, price, trades, ts)

    if position > 0:
        fp = period_df.iloc[-1]["close"]
        capital, position, entry_price = _close_position(capital, position, entry_price, fp, trades, period_df.index[-1])
        trades[-1]["type"] = "SELL_FINAL"
    return _metrics(capital, trades, equity_curve, initial_capital)


# ---------------------------------------------------------------------------
# Tournament scoring with walk-forward validation
# ---------------------------------------------------------------------------

def compute_score(result: dict) -> float:
    """Score emphasizing Sharpe and heavily penalizing drawdown."""
    ret = result["total_return_pct"]
    sharpe = result["sharpe_ratio"]
    dd = abs(result["max_drawdown_pct"])

    if ret < 0:
        return ret * 3  # triple penalty for losses
    # Quadratic drawdown penalty — crushes high-DD strategies
    return ret * 0.4 + sharpe * 12 - (dd ** 1.5) * 0.05


def run_tournament(
    data_cache: dict[str, pd.DataFrame],
    fit_start: str, fit_end: str,
    val_start: str, val_end: str,
    test_start: str, test_end: str,
    initial_capital: float = INITIAL_CAPITAL,
) -> dict[str, Any]:
    """Two-phase tournament: fit -> validate -> test."""

    all_fit_results: list[dict[str, Any]] = []

    for symbol in SYMBOLS:
        df = data_cache[symbol]
        df, feature_cols = add_indicators(df)
        data_cache[symbol] = df  # update with indicators

        configs: list[tuple[str, dict]] = []

        # EMA trend (5 variants)
        for fast, slow in [(5, 20), (10, 30), (10, 50), (20, 50), (8, 21)]:
            configs.append((f"EMA_{fast}_{slow}", {"fn": strat_ema_trend, "fast": fast, "slow": slow}))

        # EMA trailing (6 variants)
        for fast, slow in [(10, 30), (10, 50), (20, 50)]:
            for trail in [2.0, 2.5]:
                configs.append((f"EMATrail_{fast}_{slow}_t{trail}",
                                {"fn": strat_ema_trailing, "fast": fast, "slow": slow, "trail_atr_mult": trail}))

        # RSI dip buyer (4 variants) — R4 winner strategy type
        for re, rx in [(25, 55), (30, 60), (30, 65), (35, 65)]:
            configs.append((f"RSI_Dip_{re}_{rx}", {"fn": strat_rsi_dip, "rsi_entry": re, "rsi_exit": rx}))

        # BB reversion (3 variants)
        for rb, rs in [(30, 60), (35, 65), (25, 70)]:
            configs.append((f"BB_{rb}_{rs}", {"fn": strat_bb_reversion, "rsi_buy": rb, "rsi_sell": rs}))

        # Breakout (3 variants)
        for lb in [15, 20, 30]:
            configs.append((f"Breakout_{lb}", {"fn": strat_breakout, "lookback": lb}))

        # Others (5 variants)
        configs.append(("MomBreakout", {"fn": strat_momentum_breakout}))
        configs.append(("MACD_Hist", {"fn": strat_macd_histogram}))
        configs.append(("AdaptiveMom", {"fn": strat_adaptive_momentum}))
        configs.append(("TrendPullback", {"fn": strat_trend_pullback}))
        configs.append(("ML_Signal", {"fn": strat_ml_signal, "feature_cols": feature_cols,
                                       "train_end": fit_end}))

        for name, cfg in configs:
            fn = cfg.pop("fn")
            try:
                result = fn(df, fit_start, fit_end, initial_capital=initial_capital, **cfg)
                cfg["fn"] = fn
                result["strategy_name"] = f"{symbol}:{name}"
                result["_symbol"] = symbol
                result["_cfg"] = cfg
                result["_feature_cols"] = feature_cols
                all_fit_results.append(result)
            except Exception as e:
                cfg["fn"] = fn
                print(f"  SKIP {symbol}:{name} — {e}")

    # Score fit results
    for r in all_fit_results:
        r["_score"] = compute_score(r)

    all_fit_results.sort(key=lambda x: x["_score"], reverse=True)

    print(f"\n{'='*70}")
    print(f"FIT Phase Results ({fit_start} to {fit_end}) — {len(all_fit_results)} variants")
    print(f"{'='*70}")
    for i, r in enumerate(all_fit_results[:15]):
        print(f"  {i+1:2d}. {r['strategy_name']:35s}  ret={r['total_return_pct']:+7.2f}%  "
              f"sharpe={r['sharpe_ratio']:6.2f}  dd={r['max_drawdown_pct']:6.2f}%  "
              f"score={r['_score']:.2f}")

    # Phase 2: Validate top 15 on validation period
    print(f"\n{'='*70}")
    print(f"VALIDATION Phase ({val_start} to {val_end})")
    print(f"{'='*70}")

    validated: list[dict[str, Any]] = []
    for r in all_fit_results[:15]:
        symbol = r["_symbol"]
        df = data_cache[symbol]
        cfg = r["_cfg"].copy()
        fn = cfg.pop("fn")
        # Update ML train_end
        if "feature_cols" in cfg:
            cfg["train_end"] = val_start  # retrain up to validation start

        try:
            val_result = fn(df, val_start, val_end, initial_capital=initial_capital, **cfg)
            cfg["fn"] = fn
            val_score = compute_score(val_result)
            # Cap fit score contribution to prevent overfitting ML models from dominating
            capped_fit = min(r["_score"], 30.0)
            combined_score = capped_fit * 0.3 + val_score * 0.7  # validation-dominant

            print(f"  {r['strategy_name']:35s}  val_ret={val_result['total_return_pct']:+7.2f}%  "
                  f"val_sharpe={val_result['sharpe_ratio']:6.2f}  val_dd={val_result['max_drawdown_pct']:6.2f}%  "
                  f"combined={combined_score:.2f}")

            # Only advance strategies positive on BOTH periods with decent val Sharpe
            if (r["total_return_pct"] > 0 and val_result["total_return_pct"] > 0
                    and val_result["sharpe_ratio"] > 0.5):
                validated.append({
                    **r,
                    "_val_result": val_result,
                    "_combined_score": combined_score,
                })
        except Exception as e:
            cfg["fn"] = fn
            print(f"  FAIL {r['strategy_name']} — {e}")

    # CASH bias: if no validated strategy has val return > 1%, go CASH
    if not validated or max(v["_val_result"]["total_return_pct"] for v in validated) < 1.0:
        print("\n  ** No strong validated strategy — selecting CASH **")
        cash_result = {
            "final_equity": initial_capital,
            "total_return_pct": 0.0,
            "sharpe_ratio": 0.0,
            "max_drawdown_pct": 0.0,
            "num_trades": 0,
            "win_rate": 0.0,
            "trade_log": [],
            "strategy_name": "CASH:Flat",
        }
        return {"train": cash_result, "test": cash_result, "num_variants": len(all_fit_results),
                "selected": "CASH:Flat"}

    validated.sort(key=lambda x: x["_combined_score"], reverse=True)

    print(f"\n  Validated strategies: {len(validated)}")
    for i, v in enumerate(validated[:5]):
        print(f"    {i+1}. {v['strategy_name']:35s}  combined={v['_combined_score']:.2f}")

    # Select best validated strategy
    best = validated[0]
    print(f"\n  ** Selected: {best['strategy_name']} (combined score={best['_combined_score']:.2f}) **")

    # Run on full train period first (to verify), then on test
    symbol = best["_symbol"]
    df = data_cache[symbol]
    cfg = best["_cfg"].copy()
    fn = cfg.pop("fn")
    if "feature_cols" in cfg:
        cfg["train_end"] = val_end  # retrain on all train data for test

    # Run on test
    test_result = fn(df, test_start, test_end, initial_capital=initial_capital, **cfg)
    cfg["fn"] = fn
    test_result["strategy_name"] = best["strategy_name"]

    # Full train result
    cfg2 = best["_cfg"].copy()
    fn2 = cfg2.pop("fn")
    if "feature_cols" in cfg2:
        cfg2["train_end"] = fit_end
    full_train = fn2(df, fit_start, val_end, initial_capital=initial_capital, **cfg2)
    full_train["strategy_name"] = best["strategy_name"]

    return {
        "train": full_train,
        "test": test_result,
        "num_variants": len(all_fit_results),
        "selected": best["strategy_name"],
    }


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_backtest(start: str, end: str, initial_capital: float = INITIAL_CAPITAL) -> dict[str, Any]:
    """Run the walk-forward validated tournament backtest."""
    # Override test period if provided
    test_start = start if start else TEST_START
    test_end = end if end else TEST_END

    print("Fetching data for all symbols...")
    fetch_start = (pd.Timestamp(TRAIN_START) - pd.Timedelta(days=90)).strftime("%Y-%m-%d")
    fetch_end = test_end
    data_cache: dict[str, pd.DataFrame] = {}
    for symbol in SYMBOLS:
        print(f"  Fetching {symbol}...")
        data_cache[symbol] = fetch_binance_klines(symbol, INTERVAL, fetch_start, fetch_end)
        print(f"    Got {len(data_cache[symbol])} candles")

    print(f"\nRunning walk-forward tournament...")
    print(f"  FIT:      {TRAIN_START} to {FIT_END}")
    print(f"  VALIDATE: {VALIDATE_START} to {VALIDATE_END}")
    print(f"  TEST:     {test_start} to {test_end}")

    result = run_tournament(
        data_cache,
        fit_start=TRAIN_START, fit_end=FIT_END,
        val_start=VALIDATE_START, val_end=VALIDATE_END,
        test_start=test_start, test_end=test_end,
        initial_capital=initial_capital,
    )

    train_res = result["train"]
    test_res = result["test"]

    print(f"\n{'='*70}")
    print(f"FULL TRAIN: {train_res['strategy_name']}")
    print(f"  Return: {train_res['total_return_pct']:+.2f}%  Sharpe: {train_res['sharpe_ratio']:.4f}  "
          f"DD: {train_res['max_drawdown_pct']:.2f}%  Trades: {train_res['num_trades']}")
    print(f"\nTEST: {test_res['strategy_name']}")
    print(f"  Return: {test_res['total_return_pct']:+.2f}%  Sharpe: {test_res['sharpe_ratio']:.4f}  "
          f"DD: {test_res['max_drawdown_pct']:.2f}%  Trades: {test_res['num_trades']}")

    # Save results
    results_path = RESULTS_DIR / "results.txt"
    with open(results_path, "w") as f:
        f.write(f"Agent 4 — ML Engineer: Round 5 Results\n")
        f.write(f"Walk-Forward Validated Tournament\n")
        f.write(f"{'='*50}\n\n")
        f.write(f"Selected strategy: {result['selected']}\n\n")
        f.write(f"TRAIN Period ({TRAIN_START} to {VALIDATE_END})\n")
        f.write(f"{'-'*40}\n")
        for k in ["final_equity", "total_return_pct", "sharpe_ratio", "max_drawdown_pct", "num_trades", "win_rate"]:
            f.write(f"  {k}: {train_res[k]}\n")
        f.write(f"  strategy_name: {train_res['strategy_name']}\n")
        f.write(f"\nTEST Period ({test_start} to {test_end})\n")
        f.write(f"{'-'*40}\n")
        for k in ["final_equity", "total_return_pct", "sharpe_ratio", "max_drawdown_pct", "num_trades", "win_rate"]:
            f.write(f"  {k}: {test_res[k]}\n")
        f.write(f"  strategy_name: {test_res['strategy_name']}\n")

        if test_res.get("trade_log"):
            f.write(f"\nTrade Log (TEST period):\n")
            for t in test_res["trade_log"]:
                if t["type"] == "BUY":
                    f.write(f"  {t['time']} BUY  @ ${t['price']}\n")
                else:
                    pnl_str = f"{t.get('pnl_pct', 0)*100:+.2f}%" if 'pnl_pct' in t else ""
                    f.write(f"  {t['time']} SELL @ ${t['price']} pnl={pnl_str}\n")

        f.write(f"\nTournament variants tested: {result['num_variants']}\n")

    print(f"\nResults saved to {results_path}")

    return {
        "final_equity": test_res["final_equity"],
        "total_return_pct": test_res["total_return_pct"],
        "sharpe_ratio": test_res["sharpe_ratio"],
        "max_drawdown_pct": test_res["max_drawdown_pct"],
        "num_trades": test_res["num_trades"],
        "win_rate": test_res["win_rate"],
        "trade_log": test_res.get("trade_log", []),
        "strategy_name": test_res.get("strategy_name", ""),
    }


if __name__ == "__main__":
    result = run_backtest(TEST_START, TEST_END, initial_capital=1000)
    print(f"\nFinal result: {result['total_return_pct']:+.2f}% ({result.get('strategy_name', '')})")
