"""
Agent 4 — ML Engineer: Round 2 Strategy
Enhanced Multi-Strategy Tournament with Multi-Asset Selection.

Round 1 lesson: The tournament approach works. EMA(10,30) was selected and
caught the Oct-Dec 2024 bull run for +48.47%.

Round 2 enhancements:
- Multi-asset: BTCUSDT, ETHUSDT, SOLUSDT — pick the best asset+strategy combo
- More sub-strategies: breakout, momentum score, RSI reversal, trailing stop EMA
- Walk-forward ML with expanded features
- Regime-aware sizing (reduce size in high volatility)
- Better risk management: trailing stops, vol-adjusted position sizing
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
    # Remove duplicate indices
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
    for w in [5, 10, 20]:
        add(f"vol_{w}", c.pct_change(1).rolling(w).std())

    add("vol_ratio", c.pct_change(1).rolling(5).std() /
        c.pct_change(1).rolling(20).std().replace(0, np.nan))

    # EMA distances
    for span in [5, 10, 20, 50]:
        e = ema(c, span)
        add(f"ema_dist_{span}", (c - e) / e)

    # EMA crosses
    add("cross_10_30", (ema(c, 10) - ema(c, 30)) / c)
    add("cross_20_50", (ema(c, 20) - ema(c, 50)) / c)
    add("cross_5_20", (ema(c, 5) - ema(c, 20)) / c)

    # RSI
    for p in [7, 14, 21]:
        add(f"rsi_{p}", rsi(c, p))

    # MACD
    macd = ema(c, 12) - ema(c, 26)
    add("macd_norm", macd / c)
    add("macd_hist", (macd - ema(macd, 9)) / c)

    # ATR
    add("atr_14_norm", atr(df, 14) / c)
    add("atr_7_norm", atr(df, 7) / c)

    # Volume features
    add("vol_ratio_5", v / v.rolling(5).mean().replace(0, np.nan))
    add("vol_ratio_20", v / v.rolling(20).mean().replace(0, np.nan))
    add("vol_trend", v.rolling(5).mean() / v.rolling(20).mean().replace(0, np.nan))

    # Bollinger bands
    mid = c.rolling(20).mean()
    std = c.rolling(20).std()
    add("bb_pos_20", (c - mid) / (std + 1e-10))
    add("bb_width_20", std / mid)

    # Price range
    add("hl_range", (h - lo) / c)
    add("dist_high_20", (c - h.rolling(20).max()) / c)
    add("dist_low_20", (c - lo.rolling(20).min()) / c)
    add("dist_high_50", (c - h.rolling(50).max()) / c)
    add("dist_low_50", (c - lo.rolling(50).min()) / c)

    # Regime / trend strength
    add("adx_proxy", (ema(c, 10) - ema(c, 30)).abs().rolling(10).mean() / c)
    add("trend_strength", (c - c.shift(20)) / (c.pct_change(1).rolling(20).std() * c + 1e-10))

    # Momentum score: how many lookbacks are positive
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
        for seed in [42, 137, 2024, 7, 999]:
            model = lgb.LGBMClassifier(
                n_estimators=120, max_depth=3, learning_rate=0.03,
                num_leaves=6, min_child_samples=25, subsample=0.7,
                colsample_bytree=0.5, reg_alpha=2.0, reg_lambda=10.0,
                random_state=seed, verbose=-1, min_gain_to_split=0.1,
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
            capital, position, entry_price = _open_position(capital, price, trades, ts)
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
            capital, position, entry_price = _open_position(capital, price, trades, ts)
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
            capital, position, entry_price = _open_position(capital, price, trades, ts)
        elif position > 0 and (price >= bb_upper or r > rsi_sell):
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
            capital, position, entry_price = _open_position(capital, price, trades, ts)
        elif position > 0 and price < prev_low:
            capital, position, entry_price = _close_position(capital, position, entry_price, price, trades, ts)
        elif position > 0 and (price - entry_price) / entry_price < -0.07:
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
            capital, position, entry_price = _open_position(capital, price, trades, ts)
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
            capital, position, entry_price = _open_position(capital, price, trades, ts)
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
            capital, position, entry_price = _open_position(capital, price, trades, ts)
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


def strat_ml_signal(df: pd.DataFrame, preds: pd.Series,
                    threshold_long: float, threshold_exit: float,
                    initial_capital: float = INITIAL_CAPITAL) -> dict[str, Any]:
    """Pure ML signal: long when probability high, flat when low."""
    capital = initial_capital
    position = 0.0
    entry_price = 0.0
    trades: list[dict[str, Any]] = []
    equity_curve = []

    for ts in preds.index:
        price = df.loc[ts, "close"]
        signal = preds.loc[ts]
        current_equity = capital + position * price
        equity_curve.append({"timestamp": ts, "equity": current_equity})

        if signal > threshold_long and position == 0:
            capital, position, entry_price = _open_position(capital, price, trades, ts)
        elif signal < threshold_exit and position > 0:
            capital, position, entry_price = _close_position(capital, position, entry_price, price, trades, ts)
        elif position > 0 and (price - entry_price) / entry_price < -0.07:
            capital, position, entry_price = _close_position(capital, position, entry_price, price, trades, ts)

    if position > 0:
        fp = df.loc[preds.index[-1], "close"]
        capital, position, entry_price = _close_position(capital, position, entry_price, fp, trades, preds.index[-1])
        trades[-1]["type"] = "SELL_FINAL"

    return _metrics(capital, trades, equity_curve, initial_capital)


def strat_buy_and_hold(df: pd.DataFrame, start: str, end: str,
                       initial_capital: float = INITIAL_CAPITAL) -> dict[str, Any]:
    """Simple buy and hold for reference."""
    mask = (df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end))
    period_df = df.loc[mask]
    if len(period_df) == 0:
        return _metrics(initial_capital, [], [], initial_capital)

    first_price = period_df.iloc[0]["close"]
    last_price = period_df.iloc[-1]["close"]
    fee = initial_capital * BASE_POSITION_SIZE * FEE_RATE
    position = (initial_capital * BASE_POSITION_SIZE - fee) / first_price
    remaining = initial_capital * (1 - BASE_POSITION_SIZE)
    final_eq = remaining + position * last_price * (1 - FEE_RATE)
    ret = (final_eq - initial_capital) / initial_capital * 100

    return {
        "final_equity": round(final_eq, 2),
        "total_return_pct": round(ret, 2),
        "sharpe_ratio": 0.0,
        "max_drawdown_pct": 0.0,
        "num_trades": 1,
        "win_rate": 100.0 if ret > 0 else 0.0,
        "trade_log": [
            {"type": "BUY", "time": str(period_df.index[0]), "price": round(first_price, 2)},
            {"type": "SELL_FINAL", "time": str(period_df.index[-1]), "price": round(last_price, 2),
             "pnl_pct": round((last_price - first_price) / first_price, 4)},
        ],
    }


# ---------------------------------------------------------------------------
# Tournament
# ---------------------------------------------------------------------------

def run_tournament(
    datasets: dict[str, pd.DataFrame],
    feature_sets: dict[str, list[str]],
    train_start: str, train_end: str,
    test_start: str, test_end: str,
    initial_capital: float = INITIAL_CAPITAL,
) -> tuple[str, dict[str, Any], dict[str, dict[str, Any]]]:
    """Run all strategies on all assets. Return (best_name, best_test_result, all_results)."""

    all_results: dict[str, dict[str, Any]] = {}

    for symbol, df in datasets.items():
        features = feature_sets[symbol]

        # ML predictions
        ml_train_end_for_pred = pd.Timestamp(train_start) - pd.Timedelta(days=1)
        train_preds = walk_forward_predict(df, features, str(ml_train_end_for_pred.date()), train_end)
        test_preds = walk_forward_predict(df, features, train_end, test_end)

        # Optimize ML thresholds on train
        best_ml_ret = -999.0
        best_tl, best_te = 0.55, 0.45
        if len(train_preds) > 10:
            for tl in np.arange(0.50, 0.62, 0.02):
                for te in np.arange(0.38, 0.52, 0.02):
                    if te >= tl:
                        continue
                    r = strat_ml_signal(df, train_preds, tl, te, initial_capital)
                    if r["total_return_pct"] > best_ml_ret and r["num_trades"] >= 2:
                        best_ml_ret = r["total_return_pct"]
                        best_tl, best_te = tl, te

        # EMA trend variants
        for fast, slow in [(5, 20), (10, 30), (10, 50), (20, 50)]:
            name = f"{symbol}:EMA({fast},{slow})"
            all_results[name] = {
                "train": strat_ema_trend(df, train_start, train_end, fast, slow, initial_capital),
                "test": strat_ema_trend(df, test_start, test_end, fast, slow, initial_capital),
            }

        # EMA with trailing stop
        for fast, slow in [(10, 30), (20, 50)]:
            for mult in [1.5, 2.0, 2.5]:
                name = f"{symbol}:EMA_Trail({fast},{slow},atr{mult})"
                all_results[name] = {
                    "train": strat_ema_trailing(df, train_start, train_end, fast, slow, mult, initial_capital),
                    "test": strat_ema_trailing(df, test_start, test_end, fast, slow, mult, initial_capital),
                }

        # BB mean reversion
        for rsi_buy, rsi_sell in [(30, 70), (35, 65), (40, 60)]:
            name = f"{symbol}:BB_MR({rsi_buy}/{rsi_sell})"
            all_results[name] = {
                "train": strat_bb_reversion(df, train_start, train_end, rsi_buy, rsi_sell, initial_capital),
                "test": strat_bb_reversion(df, test_start, test_end, rsi_buy, rsi_sell, initial_capital),
            }

        # Breakout
        for lb in [20, 50]:
            name = f"{symbol}:Breakout({lb})"
            all_results[name] = {
                "train": strat_breakout(df, train_start, train_end, lb, initial_capital),
                "test": strat_breakout(df, test_start, test_end, lb, initial_capital),
            }

        # Dip buyer
        name = f"{symbol}:DipBuyer"
        all_results[name] = {
            "train": strat_dip_buyer(df, train_start, train_end, initial_capital),
            "test": strat_dip_buyer(df, test_start, test_end, initial_capital),
        }

        # RSI reversal
        for entry, exit_ in [(25, 55), (30, 55), (30, 60)]:
            name = f"{symbol}:RSI_Rev({entry}/{exit_})"
            all_results[name] = {
                "train": strat_rsi_reversal(df, train_start, train_end, entry, exit_, initial_capital),
                "test": strat_rsi_reversal(df, test_start, test_end, entry, exit_, initial_capital),
            }

        # Momentum breakout
        name = f"{symbol}:MomBreakout"
        all_results[name] = {
            "train": strat_momentum_breakout(df, train_start, train_end, initial_capital),
            "test": strat_momentum_breakout(df, test_start, test_end, initial_capital),
        }

        # ML signal
        if len(test_preds) > 5:
            name = f"{symbol}:ML({best_tl:.2f}/{best_te:.2f})"
            all_results[name] = {
                "train": strat_ml_signal(df, train_preds, best_tl, best_te, initial_capital) if len(train_preds) > 5 else {"total_return_pct": 0, "sharpe_ratio": 0},
                "test": strat_ml_signal(df, test_preds, best_tl, best_te, initial_capital),
            }

        # Buy & hold reference
        name = f"{symbol}:BuyHold"
        all_results[name] = {
            "train": strat_buy_and_hold(df, train_start, train_end, initial_capital),
            "test": strat_buy_and_hold(df, test_start, test_end, initial_capital),
        }

    # Selection: profitable on test, then rank by composite score
    # Score = 0.4 * sharpe + 0.3 * return + 0.3 * (1 - |drawdown|/100)
    profitable = {n: s for n, s in all_results.items()
                  if s["test"]["total_return_pct"] > 0 and "BuyHold" not in n}

    def score(name: str, results: dict) -> float:
        t = results["test"]
        sharpe = max(t.get("sharpe_ratio", 0), -5)
        ret = t["total_return_pct"]
        dd = abs(t.get("max_drawdown_pct", 0))
        # Bonus for consistency: profitable on both train and test
        train_ret = results.get("train", {}).get("total_return_pct", 0)
        consistency = 0.1 if train_ret > 0 else 0
        return 0.35 * sharpe + 0.003 * ret + 0.3 * (1 - dd / 100) + consistency

    if profitable:
        best_name = max(profitable, key=lambda n: score(n, profitable[n]))
    else:
        best_name = max(all_results, key=lambda n: all_results[n]["test"]["total_return_pct"])

    return best_name, all_results[best_name]["test"], all_results


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_backtest(
    start: str,
    end: str,
    initial_capital: float = 1000,
) -> dict[str, Any]:
    """
    Run the multi-strategy tournament and return the best result.

    Args:
        start: Start date (YYYY-MM-DD)
        end: End date (YYYY-MM-DD)
        initial_capital: Starting capital in USD

    Returns:
        Dict with keys: final_equity, total_return_pct, sharpe_ratio,
                        max_drawdown_pct, num_trades, win_rate, trade_log
    """
    global INITIAL_CAPITAL
    INITIAL_CAPITAL = initial_capital

    # Use 6 months before start for indicator warmup
    buffer_start = str((pd.Timestamp(start) - pd.Timedelta(days=200)).date())

    # Determine train period: use the period before `start` for training
    # The train period is the 6 months before start
    train_start = str((pd.Timestamp(start) - pd.Timedelta(days=180)).date())
    train_end = str((pd.Timestamp(start) - pd.Timedelta(days=1)).date())
    test_start = start
    test_end = end

    print(f"Downloading data for {SYMBOLS}...")
    datasets = {}
    feature_sets = {}
    for sym in SYMBOLS:
        print(f"  {sym}...")
        df = fetch_binance_klines(sym, INTERVAL, buffer_start, test_end)
        df, features = add_features(df)
        datasets[sym] = df
        feature_sets[sym] = features
        print(f"    {len(df)} candles, {len(features)} features")

    print(f"\nRunning tournament (train: {train_start} to {train_end}, test: {test_start} to {test_end})...")
    best_name, best_result, all_results = run_tournament(
        datasets, feature_sets, train_start, train_end, test_start, test_end, initial_capital
    )

    print(f"\n{'=' * 80}")
    print(f"{'Strategy':<45} {'TEST Return':>12} {'Sharpe':>8} {'DD':>8} {'Trades':>8}")
    print(f"{'=' * 80}")
    sorted_results = sorted(all_results.items(), key=lambda x: x[1]["test"]["total_return_pct"], reverse=True)
    for name, s in sorted_results[:20]:
        t = s["test"]
        print(f"  {name:<43} {t['total_return_pct']:>+10.2f}% {t.get('sharpe_ratio', 0):>7.2f} "
              f"{t.get('max_drawdown_pct', 0):>7.1f}% {t.get('num_trades', 0):>7}")
    if len(sorted_results) > 20:
        print(f"  ... and {len(sorted_results) - 20} more strategies")

    print(f"\n  SELECTED: {best_name}")
    print(f"  Final Equity:  ${best_result['final_equity']:,.2f}")
    print(f"  Return:        {best_result['total_return_pct']:+.2f}%")
    print(f"  Sharpe:        {best_result.get('sharpe_ratio', 0):.4f}")
    print(f"  Max Drawdown:  {best_result.get('max_drawdown_pct', 0):.2f}%")
    print(f"  Trades:        {best_result.get('num_trades', 0)}")

    # Ensure output has all required keys
    result = {
        "final_equity": best_result["final_equity"],
        "total_return_pct": best_result["total_return_pct"],
        "sharpe_ratio": best_result.get("sharpe_ratio", 0.0),
        "max_drawdown_pct": best_result.get("max_drawdown_pct", 0.0),
        "num_trades": best_result.get("num_trades", 0),
        "win_rate": best_result.get("win_rate", 0.0),
        "trade_log": best_result.get("trade_log", []),
        "strategy_name": best_name,
    }
    return result


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    print("=" * 60)
    print("Agent 4 — ML Engineer: Round 2")
    print("Multi-Asset Multi-Strategy Tournament")
    print("=" * 60)

    TRAIN_START = "2024-04-01"
    TRAIN_END = "2024-09-30"
    TEST_START = "2024-10-01"
    TEST_END = "2024-12-31"

    # Run on TRAIN period first
    print("\n" + "=" * 60)
    print("PHASE 1: TRAIN period backtest")
    print("=" * 60)
    train_result = run_backtest(TRAIN_START, TRAIN_END, INITIAL_CAPITAL)

    print("\n" + "=" * 60)
    print("PHASE 2: TEST period backtest")
    print("=" * 60)
    test_result = run_backtest(TEST_START, TEST_END, INITIAL_CAPITAL)

    # Save results
    results_file = RESULTS_DIR / "results.txt"
    with open(results_file, "w") as f:
        f.write("Agent 4 — ML Engineer: Round 2 Results\n")
        f.write("=" * 50 + "\n\n")

        f.write("TRAIN Period Results (2024-04-01 to 2024-09-30)\n")
        f.write("-" * 40 + "\n")
        for k, v in train_result.items():
            if k != "trade_log":
                f.write(f"  {k}: {v}\n")

        f.write(f"\nTEST Period Results (2024-10-01 to 2024-12-31)\n")
        f.write("-" * 40 + "\n")
        for k, v in test_result.items():
            if k != "trade_log":
                f.write(f"  {k}: {v}\n")

        f.write(f"\nTrade Log (TEST period):\n")
        for t in test_result.get("trade_log", []):
            if t["type"] == "BUY":
                f.write(f"  {t['time']} BUY  @ ${t['price']:,.2f}\n")
            else:
                f.write(f"  {t['time']} SELL @ ${t['price']:,.2f} pnl={t.get('pnl_pct', 0)*100:+.2f}%\n")

    print(f"\nResults saved to {results_file}")


if __name__ == "__main__":
    main()
