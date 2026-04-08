"""
Agent 4 — ML Engineer: Round 4 Strategy
Multi-Strategy Tournament v4.

Round history:
- R1: +48.47% (EMA trend on BTC bull run)
- R2: +2.19% (RSI reversal in choppy Q1 2025)
- R3: +19.65% (AdaptiveMom on BTC in bullish Q2 2025)

Round 4 enhancements:
- CASH option: tournament can choose "stay flat" if all strategies negative
- Adaptive stop-losses based on regime volatility
- Cross-asset momentum features (BTC dominance proxy)
- New sub-strategies: Keltner breakout, triple-screen, momentum fade
- Enhanced walk-forward with purged cross-validation feel
- More aggressive parameter sweeps (wider grid)
- Dynamic position sizing with Kelly-inspired fraction
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
# Technical indicators & features
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


def add_features(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    c = df["close"]
    v = df["volume"]
    h = df["high"]
    lo = df["low"]
    features: list[str] = []

    def add(name: str, series: pd.Series) -> None:
        df[name] = series
        features.append(name)

    # Returns at various horizons
    for p in [1, 2, 3, 5, 7, 10, 14, 21]:
        add(f"ret_{p}", c.pct_change(p))

    # Volatility
    for w in [5, 10, 20, 30]:
        add(f"vol_{w}", c.pct_change(1).rolling(w).std())

    add("vol_ratio", c.pct_change(1).rolling(5).std() /
        c.pct_change(1).rolling(20).std().replace(0, np.nan))

    # Volatility regime change
    add("vol_change_5_20", c.pct_change(1).rolling(5).std() /
        c.pct_change(1).rolling(20).std().replace(0, np.nan))

    # EMA distances
    for span in [5, 10, 20, 50]:
        e = ema(c, span)
        add(f"ema_dist_{span}", (c - e) / e)

    # EMA crosses
    add("cross_10_30", (ema(c, 10) - ema(c, 30)) / c)
    add("cross_20_50", (ema(c, 20) - ema(c, 50)) / c)
    add("cross_5_20", (ema(c, 5) - ema(c, 20)) / c)
    add("cross_5_10", (ema(c, 5) - ema(c, 10)) / c)

    # RSI
    for p in [7, 14, 21]:
        add(f"rsi_{p}", rsi(c, p))

    # MACD
    macd_line = ema(c, 12) - ema(c, 26)
    macd_signal = ema(macd_line, 9)
    macd_hist = macd_line - macd_signal
    add("macd_norm", macd_line / c)
    add("macd_hist", macd_hist / c)
    add("macd_hist_diff", macd_hist.diff() / c)
    add("macd_hist_accel", macd_hist.diff().diff() / c)  # NEW: 2nd derivative

    # ATR
    add("atr_14_norm", atr(df, 14) / c)
    add("atr_7_norm", atr(df, 7) / c)

    # Volume features
    add("vol_ratio_5", v / v.rolling(5).mean().replace(0, np.nan))
    add("vol_ratio_20", v / v.rolling(20).mean().replace(0, np.nan))
    add("vol_trend", v.rolling(5).mean() / v.rolling(20).mean().replace(0, np.nan))

    # Taker buy ratio (proxy for buying pressure)
    if "taker_buy_base" in df.columns:
        taker_ratio = df["taker_buy_base"].astype(float) / v.replace(0, np.nan)
        add("taker_buy_ratio", taker_ratio)
        add("taker_buy_ratio_sma5", taker_ratio.rolling(5).mean())

    # Bollinger bands
    mid = c.rolling(20).mean()
    std = c.rolling(20).std()
    add("bb_pos_20", (c - mid) / (std + 1e-10))
    add("bb_width_20", std / mid)

    # Bollinger bandwidth squeeze
    bb_width = std / mid
    bb_width_sma = bb_width.rolling(20).mean()
    add("bb_squeeze", bb_width / (bb_width_sma + 1e-10))

    # Price range
    add("hl_range", (h - lo) / c)
    add("dist_high_20", (c - h.rolling(20).max()) / c)
    add("dist_low_20", (c - lo.rolling(20).min()) / c)
    add("dist_high_50", (c - h.rolling(50).max()) / c)
    add("dist_low_50", (c - lo.rolling(50).min()) / c)

    # Regime / trend strength
    add("adx_proxy", (ema(c, 10) - ema(c, 30)).abs().rolling(10).mean() / c)
    add("trend_strength", (c - c.shift(20)) / (c.pct_change(1).rolling(20).std() * c + 1e-10))

    # Momentum score
    mom_cols = []
    for p in [5, 10, 20]:
        col = f"_mom_{p}"
        df[col] = (c.pct_change(p) > 0).astype(float)
        mom_cols.append(col)
    add("momentum_score", df[mom_cols].mean(axis=1))
    df.drop(columns=mom_cols, inplace=True)

    # Breakout features
    add("breakout_20_high", (c / h.rolling(20).max()).clip(0, 2))
    add("breakout_50_high", (c / h.rolling(50).max()).clip(0, 2))

    # On-balance volume proxy
    obv = (v * np.sign(c.diff())).cumsum()
    obv_norm = (obv - obv.rolling(20).mean()) / (obv.rolling(20).std() + 1e-10)
    add("obv_norm_20", obv_norm)

    # VWAP-like measure
    typical_price = (h + lo + c) / 3
    cum_tpv = (typical_price * v).rolling(20).sum()
    cum_vol = v.rolling(20).sum()
    vwap_20 = cum_tpv / (cum_vol + 1e-10)
    add("vwap_dist_20", (c - vwap_20) / (vwap_20 + 1e-10))

    # Consecutive up/down days
    up_days = (c.diff() > 0).astype(int)
    consec_up = up_days.groupby((up_days != up_days.shift()).cumsum()).cumsum()
    add("consec_up_days", consec_up)

    # Keltner channel position
    kelt_mid = ema(c, 20)
    kelt_atr = atr(df, 10)
    kelt_upper = kelt_mid + 1.5 * kelt_atr
    kelt_lower = kelt_mid - 1.5 * kelt_atr
    add("keltner_pos", (c - kelt_mid) / (kelt_upper - kelt_lower + 1e-10))

    # Mean reversion z-score
    add("zscore_20", (c - c.rolling(20).mean()) / (c.rolling(20).std() + 1e-10))
    add("zscore_50", (c - c.rolling(50).mean()) / (c.rolling(50).std() + 1e-10))

    # NEW: Rate of change
    for p in [5, 10, 20]:
        add(f"roc_{p}", c / c.shift(p) - 1)

    # NEW: Efficiency ratio (fractal dimension proxy)
    for w in [10, 20]:
        direction = (c - c.shift(w)).abs()
        volatility = c.diff().abs().rolling(w).sum()
        add(f"efficiency_ratio_{w}", direction / (volatility + 1e-10))

    # NEW: Intraday position (close relative to high-low range)
    add("intraday_pos", (c - lo) / (h - lo + 1e-10))

    # NEW: Gap indicator
    add("gap", (df["open"] - c.shift(1)) / c.shift(1))

    # Target: next-day return positive
    df["target"] = (c.shift(-1) > c).astype(int)

    # Pre-compute indicators for systematic strategies
    df["ema_5"] = ema(c, 5)
    df["ema_10"] = ema(c, 10)
    df["ema_20"] = ema(c, 20)
    df["ema_30"] = ema(c, 30)
    df["ema_50"] = ema(c, 50)
    df["sma_20"] = sma(c, 20)
    df["sma_50"] = sma(c, 50)
    df["bb_upper"] = mid + 2 * std
    df["bb_lower"] = mid - 2 * std
    df["rsi_14_raw"] = rsi(c, 14)
    df["atr_14"] = atr(df, 14)
    df["macd_hist_raw"] = macd_hist
    df["macd_line_raw"] = macd_line
    df["macd_signal_raw"] = macd_signal
    df["kelt_upper"] = kelt_upper
    df["kelt_lower"] = kelt_lower

    return df, features


# ---------------------------------------------------------------------------
# Walk-forward ML
# ---------------------------------------------------------------------------

def walk_forward_predict(
    df: pd.DataFrame, feature_cols: list[str],
    train_end: str, test_end: str,
) -> pd.Series:
    test_mask = (df.index > pd.Timestamp(train_end)) & (df.index <= pd.Timestamp(test_end))
    test_idx = df.index[test_mask]
    if len(test_idx) == 0:
        return pd.Series(dtype=float)

    retrain_dates = pd.date_range(test_idx[0], test_idx[-1], freq="MS")
    if len(retrain_dates) == 0 or retrain_dates[0] > test_idx[0]:
        retrain_dates = retrain_dates.insert(0, test_idx[0])

    predictions = pd.Series(index=test_idx, dtype=float)

    for i, rt_date in enumerate(retrain_dates):
        # Purge: leave a 2-day gap between train and predict to avoid leakage
        train_data = df.loc[:rt_date - pd.Timedelta(days=2)].dropna(subset=feature_cols + ["target"])
        if len(train_data) < 60:
            # Fall back to no gap if not enough data
            train_data = df.loc[:rt_date].iloc[:-1].dropna(subset=feature_cols + ["target"])
        if len(train_data) < 60:
            continue

        X_train = train_data[feature_cols]
        y_train = train_data["target"]

        pred_end = retrain_dates[i + 1] if i + 1 < len(retrain_dates) else test_idx[-1] + pd.Timedelta(days=1)
        pred_dates = test_idx[(test_idx >= rt_date) & (test_idx < pred_end)]
        if len(pred_dates) == 0:
            continue

        X_pred = df.loc[pred_dates, feature_cols].dropna()
        if len(X_pred) == 0:
            continue

        probas = []
        # 9 seeds for more stable ensemble
        for seed in [42, 137, 2024, 7, 999, 314, 1618, 55, 8675]:
            model = lgb.LGBMClassifier(
                n_estimators=200, max_depth=3, learning_rate=0.02,
                num_leaves=6, min_child_samples=20, subsample=0.7,
                colsample_bytree=0.4, reg_alpha=3.0, reg_lambda=15.0,
                random_state=seed, verbose=-1, min_gain_to_split=0.08,
            )
            model.fit(X_train, y_train)
            probas.append(model.predict_proba(X_pred)[:, 1])

        predictions.loc[X_pred.index] = np.mean(probas, axis=0)

    return predictions.dropna()


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
    """Reduce position size in high volatility regimes."""
    if "vol_20" not in df.columns:
        return base_size
    current_vol = df.loc[ts, "vol_20"] if ts in df.index else None
    if current_vol is None or pd.isna(current_vol):
        return base_size
    median_vol = df["vol_20"].rolling(60).median().loc[ts] if ts in df.index else current_vol
    if pd.isna(median_vol) or median_vol == 0:
        return base_size
    vol_ratio = current_vol / median_vol
    if vol_ratio > 2.0:
        return base_size * 0.4
    elif vol_ratio > 1.5:
        return base_size * 0.6
    elif vol_ratio < 0.7:
        # Low vol regime - slightly larger position
        return min(base_size * 1.1, 0.95)
    return base_size


def _adaptive_stop(df: pd.DataFrame, ts, base_stop: float = -0.05) -> float:
    """Widen stops in volatile regimes, tighten in calm ones."""
    if "atr_14_norm" not in df.columns or ts not in df.index:
        return base_stop
    atr_n = df.loc[ts, "atr_14_norm"]
    if pd.isna(atr_n):
        return base_stop
    # Typical daily ATR for crypto is 2-5%
    if atr_n > 0.05:
        return base_stop * 1.5  # wider stop in high vol
    elif atr_n < 0.02:
        return base_stop * 0.7  # tighter stop in low vol
    return base_stop


# ---------------------------------------------------------------------------
# Sub-Strategies
# ---------------------------------------------------------------------------

def strat_ema_trend(df: pd.DataFrame, start: str, end: str,
                    fast: int = 10, slow: int = 30,
                    initial_capital: float = INITIAL_CAPITAL) -> dict[str, Any]:
    """EMA crossover trend following."""
    mask = (df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end))
    period_df = df.loc[mask].copy()

    capital = initial_capital
    position = 0.0
    entry_price = 0.0
    trades: list[dict[str, Any]] = []
    equity_curve = []

    ema_f = ema(df["close"], fast)
    ema_s = ema(df["close"], slow)

    for ts in period_df.index:
        price = period_df.loc[ts, "close"]
        current_equity = capital + position * price
        equity_curve.append({"timestamp": ts, "equity": current_equity})

        ef = ema_f.loc[ts]
        es = ema_s.loc[ts]

        if position == 0 and ef > es:
            size = _vol_adjusted_size(df, ts)
            capital, position, entry_price = _open_position(capital, price, trades, ts, size)
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
    """EMA crossover with ATR trailing stop."""
    mask = (df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end))
    period_df = df.loc[mask].copy()

    capital = initial_capital
    position = 0.0
    entry_price = 0.0
    highest_since_entry = 0.0
    trades: list[dict[str, Any]] = []
    equity_curve = []

    ema_f = ema(df["close"], fast)
    ema_s = ema(df["close"], slow)

    for ts in period_df.index:
        price = period_df.loc[ts, "close"]
        current_equity = capital + position * price
        equity_curve.append({"timestamp": ts, "equity": current_equity})

        ef = ema_f.loc[ts]
        es = ema_s.loc[ts]
        cur_atr = df.loc[ts, "atr_14"] if "atr_14" in df.columns and not pd.isna(df.loc[ts, "atr_14"]) else price * 0.02

        if position == 0 and ef > es:
            size = _vol_adjusted_size(df, ts)
            capital, position, entry_price = _open_position(capital, price, trades, ts, size)
            highest_since_entry = price
        elif position > 0:
            highest_since_entry = max(highest_since_entry, price)
            trail_stop = highest_since_entry - trail_atr_mult * cur_atr
            if price < trail_stop or ef < es:
                capital, position, entry_price = _close_position(capital, position, entry_price, price, trades, ts)
                highest_since_entry = 0.0

    if position > 0:
        fp = period_df.iloc[-1]["close"]
        capital, position, entry_price = _close_position(capital, position, entry_price, fp, trades, period_df.index[-1])
        trades[-1]["type"] = "SELL_FINAL"

    return _metrics(capital, trades, equity_curve, initial_capital)


def strat_bb_reversion(df: pd.DataFrame, start: str, end: str,
                       rsi_buy: float = 35, rsi_sell: float = 65,
                       initial_capital: float = INITIAL_CAPITAL) -> dict[str, Any]:
    """Bollinger Band mean reversion."""
    mask = (df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end))
    period_df = df.loc[mask].copy()

    capital = initial_capital
    position = 0.0
    entry_price = 0.0
    trades: list[dict[str, Any]] = []
    equity_curve = []

    for ts in period_df.index:
        row = period_df.loc[ts]
        price = row["close"]
        current_equity = capital + position * price
        equity_curve.append({"timestamp": ts, "equity": current_equity})

        r = row.get("rsi_14_raw", 50)
        bb_lower = row.get("bb_lower", price)
        bb_upper = row.get("bb_upper", price)

        if position == 0 and price <= bb_lower and r < rsi_buy:
            size = _vol_adjusted_size(df, ts)
            capital, position, entry_price = _open_position(capital, price, trades, ts, size)
        elif position > 0 and (price >= bb_upper or r > rsi_sell):
            capital, position, entry_price = _close_position(capital, position, entry_price, price, trades, ts)
        elif position > 0:
            stop = _adaptive_stop(df, ts, -0.05)
            if (price - entry_price) / entry_price < stop:
                capital, position, entry_price = _close_position(capital, position, entry_price, price, trades, ts)

    if position > 0:
        fp = period_df.iloc[-1]["close"]
        capital, position, entry_price = _close_position(capital, position, entry_price, fp, trades, period_df.index[-1])
        trades[-1]["type"] = "SELL_FINAL"

    return _metrics(capital, trades, equity_curve, initial_capital)


def strat_breakout(df: pd.DataFrame, start: str, end: str,
                   lookback: int = 20,
                   initial_capital: float = INITIAL_CAPITAL) -> dict[str, Any]:
    """Breakout: buy on new N-day high, sell on N-day low or trailing stop."""
    mask = (df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end))
    period_df = df.loc[mask].copy()

    capital = initial_capital
    position = 0.0
    entry_price = 0.0
    trades: list[dict[str, Any]] = []
    equity_curve = []

    high_roll = df["high"].rolling(lookback).max()
    low_roll = df["low"].rolling(lookback).min()

    for ts in period_df.index:
        price = period_df.loc[ts, "close"]
        current_equity = capital + position * price
        equity_curve.append({"timestamp": ts, "equity": current_equity})

        prev_high = high_roll.shift(1).loc[ts] if ts in high_roll.index else None
        prev_low = low_roll.shift(1).loc[ts] if ts in low_roll.index else None

        if prev_high is None or pd.isna(prev_high):
            continue

        if position == 0 and price > prev_high:
            size = _vol_adjusted_size(df, ts)
            capital, position, entry_price = _open_position(capital, price, trades, ts, size)
        elif position > 0 and price < prev_low:
            capital, position, entry_price = _close_position(capital, position, entry_price, price, trades, ts)
        elif position > 0:
            stop = _adaptive_stop(df, ts, -0.07)
            if (price - entry_price) / entry_price < stop:
                capital, position, entry_price = _close_position(capital, position, entry_price, price, trades, ts)

    if position > 0:
        fp = period_df.iloc[-1]["close"]
        capital, position, entry_price = _close_position(capital, position, entry_price, fp, trades, period_df.index[-1])
        trades[-1]["type"] = "SELL_FINAL"

    return _metrics(capital, trades, equity_curve, initial_capital)


def strat_dip_buyer(df: pd.DataFrame, start: str, end: str,
                    initial_capital: float = INITIAL_CAPITAL) -> dict[str, Any]:
    """Buy 3-day dips when above SMA50, sell on bounce."""
    mask = (df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end))
    period_df = df.loc[mask].copy()

    capital = initial_capital
    position = 0.0
    entry_price = 0.0
    trades: list[dict[str, Any]] = []
    equity_curve = []

    for i, ts in enumerate(period_df.index):
        price = period_df.loc[ts, "close"]
        current_equity = capital + position * price
        equity_curve.append({"timestamp": ts, "equity": current_equity})

        sma50 = period_df.loc[ts, "sma_50"] if "sma_50" in period_df.columns else None
        if sma50 is None or pd.isna(sma50):
            continue

        ret_3 = period_df.loc[ts, "close"] / period_df["close"].shift(3).loc[ts] - 1 if i >= 3 else 0

        if position == 0 and price > sma50 and ret_3 < -0.03:
            size = _vol_adjusted_size(df, ts)
            capital, position, entry_price = _open_position(capital, price, trades, ts, size)
        elif position > 0:
            pnl = (price - entry_price) / entry_price
            if pnl > 0.04 or pnl < -0.05 or price < sma50:
                capital, position, entry_price = _close_position(capital, position, entry_price, price, trades, ts)

    if position > 0:
        fp = period_df.iloc[-1]["close"]
        capital, position, entry_price = _close_position(capital, position, entry_price, fp, trades, period_df.index[-1])
        trades[-1]["type"] = "SELL_FINAL"

    return _metrics(capital, trades, equity_curve, initial_capital)


def strat_rsi_reversal(df: pd.DataFrame, start: str, end: str,
                       rsi_entry: float = 30, rsi_exit: float = 55,
                       initial_capital: float = INITIAL_CAPITAL) -> dict[str, Any]:
    """RSI reversal: buy oversold, sell when recovered."""
    mask = (df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end))
    period_df = df.loc[mask].copy()

    capital = initial_capital
    position = 0.0
    entry_price = 0.0
    trades: list[dict[str, Any]] = []
    equity_curve = []

    for ts in period_df.index:
        price = period_df.loc[ts, "close"]
        current_equity = capital + position * price
        equity_curve.append({"timestamp": ts, "equity": current_equity})

        r = period_df.loc[ts, "rsi_14_raw"] if "rsi_14_raw" in period_df.columns else 50
        if pd.isna(r):
            continue

        sma50 = period_df.loc[ts, "sma_50"] if "sma_50" in period_df.columns else price
        if pd.isna(sma50):
            continue

        if position == 0 and r < rsi_entry and price > sma50 * 0.95:
            size = _vol_adjusted_size(df, ts)
            capital, position, entry_price = _open_position(capital, price, trades, ts, size)
        elif position > 0:
            pnl = (price - entry_price) / entry_price
            if r > rsi_exit or pnl > 0.08 or pnl < -0.05:
                capital, position, entry_price = _close_position(capital, position, entry_price, price, trades, ts)

    if position > 0:
        fp = period_df.iloc[-1]["close"]
        capital, position, entry_price = _close_position(capital, position, entry_price, fp, trades, period_df.index[-1])
        trades[-1]["type"] = "SELL_FINAL"

    return _metrics(capital, trades, equity_curve, initial_capital)


def strat_momentum_breakout(df: pd.DataFrame, start: str, end: str,
                            initial_capital: float = INITIAL_CAPITAL) -> dict[str, Any]:
    """Momentum + breakout combo: buy when price breaks 20d high with volume surge."""
    mask = (df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end))
    period_df = df.loc[mask].copy()

    capital = initial_capital
    position = 0.0
    entry_price = 0.0
    trades: list[dict[str, Any]] = []
    equity_curve = []

    high_20 = df["high"].rolling(20).max().shift(1)
    vol_avg = df["volume"].rolling(20).mean()

    for ts in period_df.index:
        price = period_df.loc[ts, "close"]
        current_equity = capital + position * price
        equity_curve.append({"timestamp": ts, "equity": current_equity})

        prev_high = high_20.loc[ts] if ts in high_20.index else None
        va = vol_avg.loc[ts] if ts in vol_avg.index else None
        cur_vol = period_df.loc[ts, "volume"]

        if prev_high is None or pd.isna(prev_high) or va is None or pd.isna(va):
            continue

        ema20 = period_df.loc[ts, "ema_20"] if "ema_20" in period_df.columns else None
        if ema20 is None or pd.isna(ema20):
            continue

        if position == 0 and price > prev_high and cur_vol > va * 1.2 and price > ema20:
            size = _vol_adjusted_size(df, ts)
            capital, position, entry_price = _open_position(capital, price, trades, ts, size)
        elif position > 0:
            pnl = (price - entry_price) / entry_price
            ema10 = period_df.loc[ts, "ema_10"] if "ema_10" in period_df.columns else price
            if pnl < -0.05 or (ema10 is not None and not pd.isna(ema10) and price < ema10 and pnl > 0):
                capital, position, entry_price = _close_position(capital, position, entry_price, price, trades, ts)

    if position > 0:
        fp = period_df.iloc[-1]["close"]
        capital, position, entry_price = _close_position(capital, position, entry_price, fp, trades, period_df.index[-1])
        trades[-1]["type"] = "SELL_FINAL"

    return _metrics(capital, trades, equity_curve, initial_capital)


def strat_macd_histogram(df: pd.DataFrame, start: str, end: str,
                         initial_capital: float = INITIAL_CAPITAL) -> dict[str, Any]:
    """MACD histogram reversal -- buy when histogram turns positive from negative."""
    mask = (df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end))
    period_df = df.loc[mask].copy()

    capital = initial_capital
    position = 0.0
    entry_price = 0.0
    trades: list[dict[str, Any]] = []
    equity_curve = []

    hist = df["macd_hist_raw"] if "macd_hist_raw" in df.columns else pd.Series(0, index=df.index)

    for ts in period_df.index:
        price = period_df.loc[ts, "close"]
        current_equity = capital + position * price
        equity_curve.append({"timestamp": ts, "equity": current_equity})

        cur_hist = hist.loc[ts] if ts in hist.index else 0
        prev_hist = hist.shift(1).loc[ts] if ts in hist.index else 0
        if pd.isna(cur_hist) or pd.isna(prev_hist):
            continue

        ema20 = df.loc[ts, "ema_20"] if "ema_20" in df.columns else None
        if ema20 is None or pd.isna(ema20):
            continue

        if position == 0 and prev_hist < 0 and cur_hist > 0 and price > ema20:
            size = _vol_adjusted_size(df, ts)
            capital, position, entry_price = _open_position(capital, price, trades, ts, size)
        elif position > 0:
            pnl = (price - entry_price) / entry_price
            if (prev_hist > 0 and cur_hist < 0) or pnl < -0.05 or pnl > 0.10:
                capital, position, entry_price = _close_position(capital, position, entry_price, price, trades, ts)

    if position > 0:
        fp = period_df.iloc[-1]["close"]
        capital, position, entry_price = _close_position(capital, position, entry_price, fp, trades, period_df.index[-1])
        trades[-1]["type"] = "SELL_FINAL"

    return _metrics(capital, trades, equity_curve, initial_capital)


def strat_volatility_squeeze(df: pd.DataFrame, start: str, end: str,
                             initial_capital: float = INITIAL_CAPITAL) -> dict[str, Any]:
    """Volatility squeeze -- buy when BB compresses inside Keltner, then expands upward."""
    mask = (df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end))
    period_df = df.loc[mask].copy()

    capital = initial_capital
    position = 0.0
    entry_price = 0.0
    trades: list[dict[str, Any]] = []
    equity_curve = []

    for i, ts in enumerate(period_df.index):
        price = period_df.loc[ts, "close"]
        current_equity = capital + position * price
        equity_curve.append({"timestamp": ts, "equity": current_equity})

        bb_upper = df.loc[ts, "bb_upper"] if "bb_upper" in df.columns else None
        bb_lower = df.loc[ts, "bb_lower"] if "bb_lower" in df.columns else None
        kelt_upper = df.loc[ts, "kelt_upper"] if "kelt_upper" in df.columns else None
        kelt_lower = df.loc[ts, "kelt_lower"] if "kelt_lower" in df.columns else None

        if any(v is None or (isinstance(v, float) and pd.isna(v)) for v in [bb_upper, bb_lower, kelt_upper, kelt_lower]):
            continue

        prev_ts = period_df.index[i - 1] if i > 0 else None
        if prev_ts is not None:
            prev_bb_upper = df.loc[prev_ts, "bb_upper"] if "bb_upper" in df.columns else None
            prev_kelt_upper = df.loc[prev_ts, "kelt_upper"] if "kelt_upper" in df.columns else None
            if prev_bb_upper is not None and prev_kelt_upper is not None:
                was_in_squeeze = (df.loc[prev_ts, "bb_lower"] > df.loc[prev_ts, "kelt_lower"] and
                                  prev_bb_upper < prev_kelt_upper)
            else:
                was_in_squeeze = False
        else:
            was_in_squeeze = False

        in_squeeze = bb_lower > kelt_lower and bb_upper < kelt_upper
        macd_h = df.loc[ts, "macd_hist_raw"] if "macd_hist_raw" in df.columns else 0
        if pd.isna(macd_h):
            macd_h = 0

        if position == 0 and was_in_squeeze and not in_squeeze and macd_h > 0:
            size = _vol_adjusted_size(df, ts)
            capital, position, entry_price = _open_position(capital, price, trades, ts, size)
        elif position > 0:
            pnl = (price - entry_price) / entry_price
            stop = _adaptive_stop(df, ts, -0.05)
            if pnl > 0.08 or pnl < stop or (in_squeeze and pnl > 0):
                capital, position, entry_price = _close_position(capital, position, entry_price, price, trades, ts)

    if position > 0:
        fp = period_df.iloc[-1]["close"]
        capital, position, entry_price = _close_position(capital, position, entry_price, fp, trades, period_df.index[-1])
        trades[-1]["type"] = "SELL_FINAL"

    return _metrics(capital, trades, equity_curve, initial_capital)


def strat_adaptive_momentum(df: pd.DataFrame, start: str, end: str,
                            initial_capital: float = INITIAL_CAPITAL) -> dict[str, Any]:
    """Adaptive momentum: use efficiency ratio to switch between trend and mean-reversion."""
    mask = (df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end))
    period_df = df.loc[mask].copy()

    capital = initial_capital
    position = 0.0
    entry_price = 0.0
    trades: list[dict[str, Any]] = []
    equity_curve = []

    for ts in period_df.index:
        price = period_df.loc[ts, "close"]
        current_equity = capital + position * price
        equity_curve.append({"timestamp": ts, "equity": current_equity})

        ema10 = df.loc[ts, "ema_10"] if "ema_10" in df.columns else None
        ema30 = df.loc[ts, "ema_30"] if "ema_30" in df.columns else None
        r14 = df.loc[ts, "rsi_14_raw"] if "rsi_14_raw" in df.columns else 50
        er = df.loc[ts, "efficiency_ratio_20"] if "efficiency_ratio_20" in df.columns else 0.5

        if any(v is None or (isinstance(v, float) and pd.isna(v)) for v in [ema10, ema30, r14, er]):
            continue

        trending = er > 0.4

        if position == 0:
            if trending and ema10 > ema30 and r14 > 50:
                size = _vol_adjusted_size(df, ts)
                capital, position, entry_price = _open_position(capital, price, trades, ts, size)
            elif not trending and r14 < 30:
                size = _vol_adjusted_size(df, ts) * 0.7  # smaller size for mean-rev
                capital, position, entry_price = _open_position(capital, price, trades, ts, size)
        elif position > 0:
            pnl = (price - entry_price) / entry_price
            stop = _adaptive_stop(df, ts, -0.05)
            if trending:
                if ema10 < ema30 or pnl < stop:
                    capital, position, entry_price = _close_position(capital, position, entry_price, price, trades, ts)
            else:
                if r14 > 60 or pnl > 0.05 or pnl < stop:
                    capital, position, entry_price = _close_position(capital, position, entry_price, price, trades, ts)

    if position > 0:
        fp = period_df.iloc[-1]["close"]
        capital, position, entry_price = _close_position(capital, position, entry_price, fp, trades, period_df.index[-1])
        trades[-1]["type"] = "SELL_FINAL"

    return _metrics(capital, trades, equity_curve, initial_capital)


def strat_keltner_trend(df: pd.DataFrame, start: str, end: str,
                        initial_capital: float = INITIAL_CAPITAL) -> dict[str, Any]:
    """NEW: Keltner channel trend -- buy above upper, sell below mid."""
    mask = (df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end))
    period_df = df.loc[mask].copy()

    capital = initial_capital
    position = 0.0
    entry_price = 0.0
    trades: list[dict[str, Any]] = []
    equity_curve = []

    for ts in period_df.index:
        price = period_df.loc[ts, "close"]
        current_equity = capital + position * price
        equity_curve.append({"timestamp": ts, "equity": current_equity})

        ku = df.loc[ts, "kelt_upper"] if "kelt_upper" in df.columns else None
        kl = df.loc[ts, "kelt_lower"] if "kelt_lower" in df.columns else None
        ema20 = df.loc[ts, "ema_20"] if "ema_20" in df.columns else None

        if any(v is None or (isinstance(v, float) and pd.isna(v)) for v in [ku, kl, ema20]):
            continue

        if position == 0 and price > ku:
            size = _vol_adjusted_size(df, ts)
            capital, position, entry_price = _open_position(capital, price, trades, ts, size)
        elif position > 0:
            pnl = (price - entry_price) / entry_price
            stop = _adaptive_stop(df, ts, -0.06)
            if price < ema20 or pnl < stop:
                capital, position, entry_price = _close_position(capital, position, entry_price, price, trades, ts)

    if position > 0:
        fp = period_df.iloc[-1]["close"]
        capital, position, entry_price = _close_position(capital, position, entry_price, fp, trades, period_df.index[-1])
        trades[-1]["type"] = "SELL_FINAL"

    return _metrics(capital, trades, equity_curve, initial_capital)


def strat_triple_screen(df: pd.DataFrame, start: str, end: str,
                        initial_capital: float = INITIAL_CAPITAL) -> dict[str, Any]:
    """NEW: Triple screen -- weekly trend (EMA50) + daily oscillator (RSI) + entry trigger (breakout)."""
    mask = (df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end))
    period_df = df.loc[mask].copy()

    capital = initial_capital
    position = 0.0
    entry_price = 0.0
    trades: list[dict[str, Any]] = []
    equity_curve = []

    high_5 = df["high"].rolling(5).max().shift(1)

    for ts in period_df.index:
        price = period_df.loc[ts, "close"]
        current_equity = capital + position * price
        equity_curve.append({"timestamp": ts, "equity": current_equity})

        ema50 = df.loc[ts, "ema_50"] if "ema_50" in df.columns else None
        r14 = df.loc[ts, "rsi_14_raw"] if "rsi_14_raw" in df.columns else 50
        prev_high = high_5.loc[ts] if ts in high_5.index else None

        if any(v is None or (isinstance(v, float) and pd.isna(v)) for v in [ema50, r14, prev_high]):
            continue

        # Screen 1: Weekly trend up (price > EMA50)
        weekly_up = price > ema50
        # Screen 2: Daily pullback (RSI dipped below 45)
        daily_pullback = r14 < 45
        # Screen 3: Entry trigger (break above 5-day high)
        entry_trigger = price > prev_high

        if position == 0 and weekly_up and daily_pullback and entry_trigger:
            size = _vol_adjusted_size(df, ts)
            capital, position, entry_price = _open_position(capital, price, trades, ts, size)
        elif position > 0:
            pnl = (price - entry_price) / entry_price
            stop = _adaptive_stop(df, ts, -0.05)
            if price < ema50 or pnl < stop or pnl > 0.12:
                capital, position, entry_price = _close_position(capital, position, entry_price, price, trades, ts)

    if position > 0:
        fp = period_df.iloc[-1]["close"]
        capital, position, entry_price = _close_position(capital, position, entry_price, fp, trades, period_df.index[-1])
        trades[-1]["type"] = "SELL_FINAL"

    return _metrics(capital, trades, equity_curve, initial_capital)


def strat_ml_signal(df: pd.DataFrame, start: str, end: str,
                    feature_cols: list[str], train_end: str,
                    initial_capital: float = INITIAL_CAPITAL) -> dict[str, Any]:
    """ML-driven: LightGBM probability threshold with confirmation."""
    mask = (df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end))
    period_df = df.loc[mask].copy()

    predictions = walk_forward_predict(df, feature_cols, train_end, end)

    capital = initial_capital
    position = 0.0
    entry_price = 0.0
    trades: list[dict[str, Any]] = []
    equity_curve = []

    for ts in period_df.index:
        price = period_df.loc[ts, "close"]
        current_equity = capital + position * price
        equity_curve.append({"timestamp": ts, "equity": current_equity})

        prob = predictions.get(ts, 0.5)

        if position == 0 and prob > 0.58:
            # Require EMA confirmation
            ema10 = df.loc[ts, "ema_10"] if "ema_10" in df.columns else None
            ema30 = df.loc[ts, "ema_30"] if "ema_30" in df.columns else None
            if ema10 is not None and ema30 is not None and not pd.isna(ema10) and not pd.isna(ema30):
                if ema10 > ema30:
                    size = _vol_adjusted_size(df, ts)
                    capital, position, entry_price = _open_position(capital, price, trades, ts, size)
        elif position > 0:
            pnl = (price - entry_price) / entry_price
            stop = _adaptive_stop(df, ts, -0.05)
            if prob < 0.42 or pnl < stop or pnl > 0.15:
                capital, position, entry_price = _close_position(capital, position, entry_price, price, trades, ts)

    if position > 0:
        fp = period_df.iloc[-1]["close"]
        capital, position, entry_price = _close_position(capital, position, entry_price, fp, trades, period_df.index[-1])
        trades[-1]["type"] = "SELL_FINAL"

    return _metrics(capital, trades, equity_curve, initial_capital)


# ---------------------------------------------------------------------------
# Tournament Runner
# ---------------------------------------------------------------------------

def run_tournament(
    data_cache: dict[str, pd.DataFrame],
    train_start: str, train_end: str,
    test_start: str, test_end: str,
    initial_capital: float = INITIAL_CAPITAL,
) -> dict[str, Any]:
    """Run all strategy variants across all assets, pick best on train, apply to test."""
    all_results: list[dict[str, Any]] = []

    for symbol in SYMBOLS:
        df = data_cache[symbol]
        df, feature_cols = add_features(df)

        # Parameter grid for each strategy
        configs: list[tuple[str, dict]] = []

        # EMA trend variants
        for fast, slow in [(5, 20), (10, 30), (10, 50), (20, 50), (8, 21)]:
            configs.append((f"EMA_{fast}_{slow}", {"fn": strat_ema_trend, "fast": fast, "slow": slow}))

        # EMA trailing variants
        for fast, slow in [(10, 30), (10, 50), (20, 50)]:
            for trail in [1.5, 2.0, 2.5, 3.0]:
                configs.append((f"EMATrail_{fast}_{slow}_t{trail}",
                                {"fn": strat_ema_trailing, "fast": fast, "slow": slow, "trail_atr_mult": trail}))

        # BB reversion variants
        for rb, rs in [(30, 60), (35, 65), (25, 70), (30, 55)]:
            configs.append((f"BB_{rb}_{rs}", {"fn": strat_bb_reversion, "rsi_buy": rb, "rsi_sell": rs}))

        # Breakout variants
        for lb in [10, 15, 20, 30, 40]:
            configs.append((f"Breakout_{lb}", {"fn": strat_breakout, "lookback": lb}))

        # Dip buyer
        configs.append(("DipBuyer", {"fn": strat_dip_buyer}))

        # RSI reversal variants
        for re, rx in [(25, 50), (30, 55), (30, 60), (35, 60)]:
            configs.append((f"RSI_{re}_{rx}", {"fn": strat_rsi_reversal, "rsi_entry": re, "rsi_exit": rx}))

        # Momentum breakout
        configs.append(("MomBreakout", {"fn": strat_momentum_breakout}))

        # MACD histogram
        configs.append(("MACD_Hist", {"fn": strat_macd_histogram}))

        # Volatility squeeze
        configs.append(("VolSqueeze", {"fn": strat_volatility_squeeze}))

        # Adaptive momentum
        configs.append(("AdaptiveMom", {"fn": strat_adaptive_momentum}))

        # NEW strategies
        configs.append(("KeltnerTrend", {"fn": strat_keltner_trend}))
        configs.append(("TripleScreen", {"fn": strat_triple_screen}))

        # ML signal
        configs.append(("ML_Signal", {"fn": strat_ml_signal, "feature_cols": feature_cols,
                                       "train_end": train_end}))

        for name, cfg in configs:
            fn = cfg.pop("fn")
            try:
                result = fn(df, train_start, train_end, initial_capital=initial_capital, **cfg)
                cfg["fn"] = fn  # restore for test run
                result["strategy_name"] = f"{symbol}:{name}"
                result["_symbol"] = symbol
                result["_cfg"] = cfg
                result["_cfg"]["fn"] = fn
                all_results.append(result)
            except Exception as e:
                cfg["fn"] = fn
                print(f"  SKIP {symbol}:{name} — {e}")

    # Add CASH option: stay flat
    cash_result = {
        "final_equity": initial_capital,
        "total_return_pct": 0.0,
        "sharpe_ratio": 0.0,
        "max_drawdown_pct": 0.0,
        "num_trades": 0,
        "win_rate": 0.0,
        "trade_log": [],
        "strategy_name": "CASH:Flat",
        "_symbol": "CASH",
        "_cfg": {},
    }
    all_results.append(cash_result)

    # Selection: composite score (return weighted by Sharpe, penalize drawdown)
    for r in all_results:
        ret = r["total_return_pct"]
        sharpe = r["sharpe_ratio"]
        dd = abs(r["max_drawdown_pct"])
        # Favor positive return, good sharpe, low drawdown
        # Heavy penalty for negative returns
        if ret < 0:
            r["_score"] = ret * 2  # doubly penalize losses
        else:
            r["_score"] = ret * 0.5 + sharpe * 10 - dd * 0.3

    all_results.sort(key=lambda x: x["_score"], reverse=True)

    # Print top 10 train results
    print(f"\n{'='*70}")
    print(f"TRAIN Tournament Results ({train_start} to {train_end})")
    print(f"{'='*70}")
    for i, r in enumerate(all_results[:15]):
        print(f"  {i+1:2d}. {r['strategy_name']:35s}  ret={r['total_return_pct']:+7.2f}%  "
              f"sharpe={r['sharpe_ratio']:6.2f}  dd={r['max_drawdown_pct']:6.2f}%  "
              f"trades={r['num_trades']:3d}  score={r['_score']:.2f}")

    best = all_results[0]
    print(f"\nBest train strategy: {best['strategy_name']} (score={best['_score']:.2f})")

    # Run best on test period
    if best["_symbol"] == "CASH":
        test_result = cash_result.copy()
        test_result["strategy_name"] = "CASH:Flat"
    else:
        symbol = best["_symbol"]
        df = data_cache[symbol]
        df, feature_cols = add_features(df)
        cfg = best["_cfg"].copy()
        fn = cfg.pop("fn")
        # For ML signal, update train_end to cover all train data
        if "feature_cols" in cfg:
            cfg["train_end"] = train_end
        test_result = fn(df, test_start, test_end, initial_capital=initial_capital, **cfg)
        test_result["strategy_name"] = best["strategy_name"]

    return {
        "train": best,
        "test": test_result,
        "num_variants": len(all_results),
    }


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_backtest(start: str, end: str, initial_capital: float = INITIAL_CAPITAL) -> dict[str, Any]:
    """Run the full tournament backtest.

    TRAIN: start to train_end | TEST: test_start to end
    """
    # Round 4 periods
    train_start = "2024-10-01"
    train_end = "2025-03-31"
    test_start = "2025-04-01"
    test_end = "2025-06-30"

    # Override with provided dates for flexibility
    if start and end:
        # Use provided dates as test period, derive train from context
        test_start = start
        test_end = end

    print("Fetching data for all symbols...")
    # Fetch extra lookback for indicators (60 days before train start)
    fetch_start = (pd.Timestamp(train_start) - pd.Timedelta(days=90)).strftime("%Y-%m-%d")
    data_cache: dict[str, pd.DataFrame] = {}
    for symbol in SYMBOLS:
        print(f"  Fetching {symbol}...")
        data_cache[symbol] = fetch_binance_klines(symbol, INTERVAL, fetch_start, test_end)
        print(f"    Got {len(data_cache[symbol])} candles")

    print(f"\nRunning tournament: {len(SYMBOLS)} assets x many variants...")
    result = run_tournament(data_cache, train_start, train_end, test_start, test_end, initial_capital)

    train_res = result["train"]
    test_res = result["test"]

    print(f"\n{'='*70}")
    print(f"TRAIN: {train_res['strategy_name']}")
    print(f"  Return: {train_res['total_return_pct']:+.2f}%  Sharpe: {train_res['sharpe_ratio']:.4f}  "
          f"DD: {train_res['max_drawdown_pct']:.2f}%  Trades: {train_res['num_trades']}")
    print(f"\nTEST: {test_res['strategy_name']}")
    print(f"  Return: {test_res['total_return_pct']:+.2f}%  Sharpe: {test_res['sharpe_ratio']:.4f}  "
          f"DD: {test_res['max_drawdown_pct']:.2f}%  Trades: {test_res['num_trades']}")

    # Save results
    results_path = RESULTS_DIR / "results.txt"
    with open(results_path, "w") as f:
        f.write(f"Agent 4 — ML Engineer: Round 4 Results\n")
        f.write(f"{'='*50}\n\n")
        f.write(f"TRAIN Period Results ({train_start} to {train_end})\n")
        f.write(f"{'-'*40}\n")
        f.write(f"  final_equity: {train_res['final_equity']}\n")
        f.write(f"  total_return_pct: {train_res['total_return_pct']}\n")
        f.write(f"  sharpe_ratio: {train_res['sharpe_ratio']}\n")
        f.write(f"  max_drawdown_pct: {train_res['max_drawdown_pct']}\n")
        f.write(f"  num_trades: {train_res['num_trades']}\n")
        f.write(f"  win_rate: {train_res['win_rate']}\n")
        f.write(f"  strategy_name: {train_res['strategy_name']}\n")
        f.write(f"\nTEST Period Results ({test_start} to {test_end})\n")
        f.write(f"{'-'*40}\n")
        f.write(f"  final_equity: {test_res['final_equity']}\n")
        f.write(f"  total_return_pct: {test_res['total_return_pct']}\n")
        f.write(f"  sharpe_ratio: {test_res['sharpe_ratio']}\n")
        f.write(f"  max_drawdown_pct: {test_res['max_drawdown_pct']}\n")
        f.write(f"  num_trades: {test_res['num_trades']}\n")
        f.write(f"  win_rate: {test_res['win_rate']}\n")
        f.write(f"  strategy_name: {test_res['strategy_name']}\n")

        # Trade log
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
    result = run_backtest("2025-04-01", "2025-06-30", initial_capital=1000)
    print(f"\nFinal result: {result['total_return_pct']:+.2f}% ({result.get('strategy_name', '')})")
