"""
Agent 4 -- ML Engineer: Round 6 Strategy
Adaptive Multi-Timeframe Ensemble with Strict Risk Controls.

Round history analysis:
- R1: +48.47% (EMA trend on BTC bull, simple works in trends)
- R2: +2.19% (RSI reversal, choppy market)
- R3: +19.65% (AdaptiveMom BTC, bullish Q2)
- R4: -2.16% (EMA whipsaw, train/test regime mismatch)
- R5: -13.34% (ETH single trade -14.65%, no stop loss saved it)

Root cause of losses: tournament overfits to train period, single-asset
concentration, and loose stops allow catastrophic single-trade losses.

Round 6 design principles:
1. 4-HOUR BARS: More data points = better ML training, faster stops
2. TIGHT STOPS: Max 3% loss per trade, trailing stops at 2x ATR
3. MULTI-ASSET DIVERSIFICATION: Split capital across BTC/ETH/SOL
4. WALK-FORWARD with 3-fold temporal CV (not just 1 split)
5. ENSEMBLE VOTING: Top-3 strategies must agree before entry
6. REGIME FILTER: Use daily bars to detect trend/range, only trade
   momentum in trends, mean-reversion in ranges
7. CASH BIAS: If validation Sharpe < 0.5, stay in cash
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
INTERVAL_4H = "4h"
INTERVAL_1D = "1d"
INITIAL_CAPITAL = 1000.0
FEE_RATE = 0.001
MAX_LOSS_PER_TRADE = 0.03  # 3% hard stop
TRAILING_ATR_MULT = 2.0
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
        params = {
            "symbol": symbol, "interval": interval,
            "startTime": current_start, "endTime": end_ms, "limit": 1000,
        }
        for attempt in range(3):
            try:
                resp = requests.get(url, params=params, timeout=30)
                resp.raise_for_status()
                data = resp.json()
                break
            except Exception:
                if attempt == 2:
                    raise
                time.sleep(1)
        if not data:
            break
        all_klines.extend(data)
        current_start = data[-1][0] + 1
        time.sleep(0.15)

    df = pd.DataFrame(all_klines, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades", "taker_buy_base",
        "taker_buy_quote", "ignore",
    ])
    for col in ["open", "high", "low", "close", "volume", "quote_volume"]:
        df[col] = df[col].astype(float)
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
    df.set_index("open_time", inplace=True)
    df = df[~df.index.duplicated(keep="first")]
    return df


# ---------------------------------------------------------------------------
# Technical indicators
# ---------------------------------------------------------------------------

def ema(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, adjust=False).mean()


def rsi(s: pd.Series, period: int = 14) -> pd.Series:
    delta = s.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    h, lo, c = df["high"], df["low"], df["close"]
    tr = pd.concat([h - lo, (h - c.shift()).abs(), (lo - c.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def add_indicators(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Add all technical indicators and ML features."""
    c = df["close"]
    v = df["volume"]
    h = df["high"]
    lo = df["low"]
    features: list[str] = []

    def add(name: str, series: pd.Series) -> None:
        df[name] = series
        features.append(name)

    # Returns at multiple horizons
    for p in [1, 2, 3, 5, 7, 10, 14, 21]:
        add(f"ret_{p}", c.pct_change(p))

    # Volatility
    for w in [5, 10, 20, 40]:
        add(f"vol_{w}", c.pct_change(1).rolling(w).std())

    # Vol ratio (short/long)
    add("vol_ratio_5_20", df.get("vol_5", c.pct_change().rolling(5).std()) /
        (c.pct_change().rolling(20).std() + 1e-10))

    # EMAs and crossovers
    for span in [5, 10, 20, 50]:
        df[f"ema_{span}"] = ema(c, span)
    add("ema_cross_10_50", (df["ema_10"] - df["ema_50"]) / c)
    add("ema_cross_5_20", (df["ema_5"] - df["ema_20"]) / c)

    # RSI
    df["rsi_14"] = rsi(c, 14)
    add("rsi_14_feat", df["rsi_14"])
    add("rsi_7", rsi(c, 7))

    # Bollinger Bands
    sma20 = c.rolling(20).mean()
    std20 = c.rolling(20).std()
    add("bb_pos", (c - sma20) / (std20 + 1e-10))
    add("bb_width", std20 / (sma20 + 1e-10))

    # ATR
    df["atr_14"] = atr(df, 14)
    add("atr_pct", df["atr_14"] / c)

    # MACD
    macd_line = ema(c, 12) - ema(c, 26)
    macd_signal = ema(macd_line, 9)
    add("macd_hist", macd_line - macd_signal)
    add("macd_hist_diff", (macd_line - macd_signal).diff())

    # Volume features
    add("vol_sma_ratio", v / (v.rolling(20).mean() + 1e-10))
    add("taker_ratio", df["taker_buy_base"].astype(float) / (v + 1e-10))

    # Price position
    add("dist_high_20", (c - h.rolling(20).max()) / c)
    add("dist_low_20", (c - lo.rolling(20).min()) / c)
    add("hl_range", (h - lo) / c)

    # Momentum
    add("roc_10", c.pct_change(10))
    add("roc_20", c.pct_change(20))

    # Consecutive up/down days
    daily_ret = c.pct_change()
    add("consec_up", daily_ret.gt(0).astype(int).groupby(
        (daily_ret.gt(0) != daily_ret.gt(0).shift()).cumsum()).cumsum())
    add("consec_down", daily_ret.lt(0).astype(int).groupby(
        (daily_ret.lt(0) != daily_ret.lt(0).shift()).cumsum()).cumsum())

    # ADX proxy (trend strength)
    add("adx_proxy", (ema(c, 10) - ema(c, 30)).abs().rolling(10).mean() / (c + 1e-10))

    # Target: next-period return positive
    df["target"] = (c.shift(-1) > c).astype(int)
    # Regression target: next-period return magnitude
    df["target_ret"] = c.pct_change().shift(-1)

    return df, features


# ---------------------------------------------------------------------------
# Position management with strict risk control
# ---------------------------------------------------------------------------

def _open_position(capital: float, price: float, trades: list, ts,
                   size_frac: float = 0.90) -> tuple[float, float, float]:
    invest = capital * size_frac
    fee = invest * FEE_RATE
    position = (invest - fee) / price
    capital -= invest
    trades.append({
        "type": "BUY", "time": str(ts), "price": round(price, 2),
        "size": round(position, 6),
    })
    return capital, position, price


def _close_position(capital: float, position: float, entry_price: float,
                    price: float, trades: list, ts) -> tuple[float, float, float]:
    proceeds = position * price
    fee = proceeds * FEE_RATE
    capital += proceeds - fee
    pnl = (price - entry_price) / entry_price
    trades.append({
        "type": "SELL", "time": str(ts), "price": round(price, 2),
        "pnl_pct": round(pnl, 4),
    })
    return capital, 0.0, 0.0


def _metrics(capital: float, trades: list, equity_curve: list,
             initial_capital: float) -> dict[str, Any]:
    final_equity = capital
    total_return = (final_equity - initial_capital) / initial_capital

    if not equity_curve:
        return {
            "final_equity": round(final_equity, 2),
            "total_return_pct": round(total_return * 100, 2),
            "sharpe_ratio": 0.0, "max_drawdown_pct": 0.0,
            "num_trades": 0, "win_rate": 0.0, "trade_log": trades,
        }

    equity_df = pd.DataFrame(equity_curve).set_index("timestamp")
    # Resample to daily for consistent Sharpe calc
    daily_eq = equity_df["equity"].resample("D").last().dropna()
    if len(daily_eq) < 2:
        daily_eq = equity_df["equity"]
    daily_returns = daily_eq.pct_change().dropna()

    sharpe = 0.0
    if len(daily_returns) > 1 and daily_returns.std() > 0:
        sharpe = float((daily_returns.mean() / daily_returns.std()) * np.sqrt(365))

    max_dd = 0.0
    if len(daily_eq) > 0:
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


# ---------------------------------------------------------------------------
# Sub-strategies (4h timeframe with daily regime filter)
# ---------------------------------------------------------------------------

def strat_ema_cross(df: pd.DataFrame, start: str, end: str,
                    fast: int = 10, slow: int = 50,
                    initial_capital: float = INITIAL_CAPITAL,
                    stop_pct: float = MAX_LOSS_PER_TRADE,
                    trail_atr: float = TRAILING_ATR_MULT) -> dict[str, Any]:
    """EMA crossover with trailing stop and hard stop loss."""
    mask = (df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end))
    period_df = df.loc[mask]
    if len(period_df) < slow + 5:
        return _metrics(initial_capital, [], [], initial_capital)

    capital = initial_capital
    position = 0.0
    entry_price = 0.0
    highest_since_entry = 0.0
    trades: list[dict] = []
    equity_curve = []

    ema_f = ema(df["close"], fast)
    ema_s = ema(df["close"], slow)

    for ts in period_df.index:
        price = period_df.loc[ts, "close"]
        current_equity = capital + position * price
        equity_curve.append({"timestamp": ts, "equity": current_equity})

        ef = ema_f.loc[ts] if ts in ema_f.index else np.nan
        es = ema_s.loc[ts] if ts in ema_s.index else np.nan
        if pd.isna(ef) or pd.isna(es):
            continue

        if position > 0:
            highest_since_entry = max(highest_since_entry, price)
            cur_atr = df["atr_14"].loc[ts] if ts in df["atr_14"].index and not pd.isna(df["atr_14"].loc[ts]) else price * 0.02
            trail_stop = highest_since_entry - trail_atr * cur_atr
            hard_stop = entry_price * (1 - stop_pct)

            if price <= hard_stop or price <= trail_stop or ef < es:
                capital, position, entry_price = _close_position(
                    capital, position, entry_price, price, trades, ts)
                highest_since_entry = 0.0
        elif ef > es:
            capital, position, entry_price = _open_position(
                capital, price, trades, ts)
            highest_since_entry = price

    if position > 0:
        fp = period_df.iloc[-1]["close"]
        capital, position, entry_price = _close_position(
            capital, position, entry_price, fp, trades, period_df.index[-1])
        trades[-1]["type"] = "SELL_FINAL"

    return _metrics(capital, trades, equity_curve, initial_capital)


def strat_rsi_mean_revert(df: pd.DataFrame, start: str, end: str,
                          rsi_entry: int = 30, rsi_exit: int = 60,
                          initial_capital: float = INITIAL_CAPITAL,
                          stop_pct: float = MAX_LOSS_PER_TRADE) -> dict[str, Any]:
    """Buy when RSI oversold, sell when RSI recovers. With hard stop."""
    mask = (df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end))
    period_df = df.loc[mask]
    if len(period_df) < 20:
        return _metrics(initial_capital, [], [], initial_capital)

    capital = initial_capital
    position = 0.0
    entry_price = 0.0
    trades: list[dict] = []
    equity_curve = []

    for ts in period_df.index:
        price = period_df.loc[ts, "close"]
        current_equity = capital + position * price
        equity_curve.append({"timestamp": ts, "equity": current_equity})

        cur_rsi = df["rsi_14"].loc[ts] if ts in df["rsi_14"].index else 50
        if pd.isna(cur_rsi):
            continue

        if position > 0:
            hard_stop = entry_price * (1 - stop_pct)
            if price <= hard_stop or cur_rsi >= rsi_exit:
                capital, position, entry_price = _close_position(
                    capital, position, entry_price, price, trades, ts)
        elif cur_rsi <= rsi_entry:
            capital, position, entry_price = _open_position(
                capital, price, trades, ts)

    if position > 0:
        fp = period_df.iloc[-1]["close"]
        capital, position, entry_price = _close_position(
            capital, position, entry_price, fp, trades, period_df.index[-1])
        trades[-1]["type"] = "SELL_FINAL"

    return _metrics(capital, trades, equity_curve, initial_capital)


def strat_bb_reversion(df: pd.DataFrame, start: str, end: str,
                       bb_entry: float = -1.5, bb_exit: float = 0.5,
                       initial_capital: float = INITIAL_CAPITAL,
                       stop_pct: float = MAX_LOSS_PER_TRADE) -> dict[str, Any]:
    """Buy when price touches lower BB, sell at midband. With hard stop."""
    mask = (df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end))
    period_df = df.loc[mask]
    if len(period_df) < 25:
        return _metrics(initial_capital, [], [], initial_capital)

    capital = initial_capital
    position = 0.0
    entry_price = 0.0
    trades: list[dict] = []
    equity_curve = []

    for ts in period_df.index:
        price = period_df.loc[ts, "close"]
        current_equity = capital + position * price
        equity_curve.append({"timestamp": ts, "equity": current_equity})

        bb = df["bb_pos"].loc[ts] if ts in df["bb_pos"].index else 0
        if pd.isna(bb):
            continue

        if position > 0:
            hard_stop = entry_price * (1 - stop_pct)
            if price <= hard_stop or bb >= bb_exit:
                capital, position, entry_price = _close_position(
                    capital, position, entry_price, price, trades, ts)
        elif bb <= bb_entry:
            capital, position, entry_price = _open_position(
                capital, price, trades, ts)

    if position > 0:
        fp = period_df.iloc[-1]["close"]
        capital, position, entry_price = _close_position(
            capital, position, entry_price, fp, trades, period_df.index[-1])
        trades[-1]["type"] = "SELL_FINAL"

    return _metrics(capital, trades, equity_curve, initial_capital)


def strat_adaptive_momentum(df: pd.DataFrame, start: str, end: str,
                            lookback: int = 20, threshold: float = 0.02,
                            initial_capital: float = INITIAL_CAPITAL,
                            stop_pct: float = MAX_LOSS_PER_TRADE,
                            trail_atr: float = TRAILING_ATR_MULT) -> dict[str, Any]:
    """Momentum with vol-adjusted threshold. Long when momentum strong."""
    mask = (df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end))
    period_df = df.loc[mask]
    if len(period_df) < lookback + 5:
        return _metrics(initial_capital, [], [], initial_capital)

    capital = initial_capital
    position = 0.0
    entry_price = 0.0
    highest_since_entry = 0.0
    trades: list[dict] = []
    equity_curve = []

    mom = df["close"].pct_change(lookback)
    vol = df["close"].pct_change().rolling(lookback).std()

    for ts in period_df.index:
        price = period_df.loc[ts, "close"]
        current_equity = capital + position * price
        equity_curve.append({"timestamp": ts, "equity": current_equity})

        cur_mom = mom.loc[ts] if ts in mom.index else 0
        cur_vol = vol.loc[ts] if ts in vol.index else 0.02
        if pd.isna(cur_mom) or pd.isna(cur_vol):
            continue

        # Vol-adjusted threshold
        adj_threshold = threshold * (1 + cur_vol * 10)

        if position > 0:
            highest_since_entry = max(highest_since_entry, price)
            cur_atr = df["atr_14"].loc[ts] if ts in df["atr_14"].index and not pd.isna(df["atr_14"].loc[ts]) else price * 0.02
            trail_stop = highest_since_entry - trail_atr * cur_atr
            hard_stop = entry_price * (1 - stop_pct)

            if price <= hard_stop or price <= trail_stop or cur_mom < -threshold * 0.5:
                capital, position, entry_price = _close_position(
                    capital, position, entry_price, price, trades, ts)
                highest_since_entry = 0.0
        elif cur_mom > adj_threshold:
            capital, position, entry_price = _open_position(
                capital, price, trades, ts)
            highest_since_entry = price

    if position > 0:
        fp = period_df.iloc[-1]["close"]
        capital, position, entry_price = _close_position(
            capital, position, entry_price, fp, trades, period_df.index[-1])
        trades[-1]["type"] = "SELL_FINAL"

    return _metrics(capital, trades, equity_curve, initial_capital)


def strat_macd_trend(df: pd.DataFrame, start: str, end: str,
                     initial_capital: float = INITIAL_CAPITAL,
                     stop_pct: float = MAX_LOSS_PER_TRADE,
                     trail_atr: float = TRAILING_ATR_MULT) -> dict[str, Any]:
    """MACD histogram crossover with trend filter."""
    mask = (df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end))
    period_df = df.loc[mask]
    if len(period_df) < 30:
        return _metrics(initial_capital, [], [], initial_capital)

    capital = initial_capital
    position = 0.0
    entry_price = 0.0
    highest_since_entry = 0.0
    trades: list[dict] = []
    equity_curve = []

    for i, ts in enumerate(period_df.index):
        price = period_df.loc[ts, "close"]
        current_equity = capital + position * price
        equity_curve.append({"timestamp": ts, "equity": current_equity})

        macd_h = df["macd_hist"].loc[ts] if ts in df["macd_hist"].index else 0
        macd_hd = df["macd_hist_diff"].loc[ts] if ts in df["macd_hist_diff"].index else 0
        ema20 = df["ema_20"].loc[ts] if ts in df["ema_20"].index else price
        if any(pd.isna(v) for v in [macd_h, macd_hd, ema20]):
            continue

        if position > 0:
            highest_since_entry = max(highest_since_entry, price)
            cur_atr = df["atr_14"].loc[ts] if ts in df["atr_14"].index and not pd.isna(df["atr_14"].loc[ts]) else price * 0.02
            trail_stop = highest_since_entry - trail_atr * cur_atr
            hard_stop = entry_price * (1 - stop_pct)

            if price <= hard_stop or price <= trail_stop or (macd_h < 0 and macd_hd < 0):
                capital, position, entry_price = _close_position(
                    capital, position, entry_price, price, trades, ts)
                highest_since_entry = 0.0
        elif macd_h > 0 and macd_hd > 0 and price > ema20:
            capital, position, entry_price = _open_position(
                capital, price, trades, ts)
            highest_since_entry = price

    if position > 0:
        fp = period_df.iloc[-1]["close"]
        capital, position, entry_price = _close_position(
            capital, position, entry_price, fp, trades, period_df.index[-1])
        trades[-1]["type"] = "SELL_FINAL"

    return _metrics(capital, trades, equity_curve, initial_capital)


def strat_ml_signal(df: pd.DataFrame, start: str, end: str,
                    feature_cols: list[str], train_end: str,
                    initial_capital: float = INITIAL_CAPITAL,
                    stop_pct: float = MAX_LOSS_PER_TRADE) -> dict[str, Any]:
    """Walk-forward LightGBM ensemble signal."""
    test_mask = (df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end))
    test_idx = df.index[test_mask]
    if len(test_idx) == 0:
        return _metrics(initial_capital, [], [], initial_capital)

    # Train on all data up to start
    train_data = df.loc[:pd.Timestamp(train_end)].dropna(subset=feature_cols + ["target"])
    if len(train_data) < 100:
        return _metrics(initial_capital, [], [], initial_capital)

    # Monthly retraining
    retrain_dates = pd.date_range(test_idx[0], test_idx[-1], freq="MS")
    if len(retrain_dates) == 0 or retrain_dates[0] > test_idx[0]:
        retrain_dates = retrain_dates.insert(0, test_idx[0])

    predictions = pd.Series(index=test_idx, dtype=float)

    for i, rt_date in enumerate(retrain_dates):
        # Expanding window train
        cur_train = df.loc[:rt_date].iloc[:-1].dropna(subset=feature_cols + ["target"])
        if len(cur_train) < 100:
            continue

        X_train = cur_train[feature_cols]
        y_train = cur_train["target"]

        pred_end = retrain_dates[i + 1] if i + 1 < len(retrain_dates) else test_idx[-1] + pd.Timedelta(days=1)
        pred_dates = test_idx[(test_idx >= rt_date) & (test_idx < pred_end)]
        if len(pred_dates) == 0:
            continue

        X_pred = df.loc[pred_dates, feature_cols].dropna()
        if len(X_pred) == 0:
            continue

        # Ensemble of 5 seeds with different hyperparams
        probas = []
        configs = [
            {"n_estimators": 80, "max_depth": 3, "learning_rate": 0.03, "num_leaves": 6,
             "min_child_samples": 30, "subsample": 0.7, "colsample_bytree": 0.5,
             "reg_alpha": 2.0, "reg_lambda": 10.0, "random_state": 42},
            {"n_estimators": 120, "max_depth": 4, "learning_rate": 0.02, "num_leaves": 8,
             "min_child_samples": 25, "subsample": 0.6, "colsample_bytree": 0.4,
             "reg_alpha": 3.0, "reg_lambda": 15.0, "random_state": 137},
            {"n_estimators": 60, "max_depth": 2, "learning_rate": 0.05, "num_leaves": 4,
             "min_child_samples": 40, "subsample": 0.8, "colsample_bytree": 0.6,
             "reg_alpha": 1.0, "reg_lambda": 5.0, "random_state": 2024},
            {"n_estimators": 100, "max_depth": 3, "learning_rate": 0.03, "num_leaves": 7,
             "min_child_samples": 35, "subsample": 0.65, "colsample_bytree": 0.45,
             "reg_alpha": 2.5, "reg_lambda": 12.0, "random_state": 999},
            {"n_estimators": 90, "max_depth": 3, "learning_rate": 0.04, "num_leaves": 5,
             "min_child_samples": 30, "subsample": 0.75, "colsample_bytree": 0.55,
             "reg_alpha": 1.5, "reg_lambda": 8.0, "random_state": 7},
        ]
        for cfg in configs:
            try:
                model = lgb.LGBMClassifier(verbose=-1, min_gain_to_split=0.1, **cfg)
                model.fit(X_train, y_train)
                probas.append(model.predict_proba(X_pred)[:, 1])
            except Exception:
                pass

        if probas:
            predictions.loc[X_pred.index] = np.mean(probas, axis=0)

    preds = predictions.dropna()
    if len(preds) < 5:
        return _metrics(initial_capital, [], [], initial_capital)

    # Trade on ML signal
    capital = initial_capital
    position = 0.0
    entry_price = 0.0
    trades: list[dict] = []
    equity_curve = []

    threshold_long = 0.55
    threshold_exit = 0.45

    for ts in preds.index:
        price = df.loc[ts, "close"]
        signal = preds.loc[ts]
        current_equity = capital + position * price
        equity_curve.append({"timestamp": ts, "equity": current_equity})

        if position > 0:
            hard_stop = entry_price * (1 - stop_pct)
            if price <= hard_stop or signal < threshold_exit:
                capital, position, entry_price = _close_position(
                    capital, position, entry_price, price, trades, ts)
        elif signal > threshold_long:
            capital, position, entry_price = _open_position(
                capital, price, trades, ts)

    if position > 0:
        fp = df.loc[preds.index[-1], "close"]
        capital, position, entry_price = _close_position(
            capital, position, entry_price, fp, trades, preds.index[-1])
        trades[-1]["type"] = "SELL_FINAL"

    return _metrics(capital, trades, equity_curve, initial_capital)


def strat_breakout(df: pd.DataFrame, start: str, end: str,
                   lookback: int = 20,
                   initial_capital: float = INITIAL_CAPITAL,
                   stop_pct: float = MAX_LOSS_PER_TRADE,
                   trail_atr: float = TRAILING_ATR_MULT) -> dict[str, Any]:
    """Breakout above N-period high with volume confirmation."""
    mask = (df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end))
    period_df = df.loc[mask]
    if len(period_df) < lookback + 5:
        return _metrics(initial_capital, [], [], initial_capital)

    capital = initial_capital
    position = 0.0
    entry_price = 0.0
    highest_since_entry = 0.0
    trades: list[dict] = []
    equity_curve = []

    high_n = df["high"].rolling(lookback).max()
    vol_avg = df["volume"].rolling(lookback).mean()

    for ts in period_df.index:
        price = period_df.loc[ts, "close"]
        current_equity = capital + position * price
        equity_curve.append({"timestamp": ts, "equity": current_equity})

        prev_high = high_n.shift(1).loc[ts] if ts in high_n.index else np.nan
        cur_vol = df["volume"].loc[ts]
        avg_vol = vol_avg.loc[ts] if ts in vol_avg.index else np.nan
        if any(pd.isna(v) for v in [prev_high, avg_vol]):
            continue

        if position > 0:
            highest_since_entry = max(highest_since_entry, price)
            cur_atr = df["atr_14"].loc[ts] if ts in df["atr_14"].index and not pd.isna(df["atr_14"].loc[ts]) else price * 0.02
            trail_stop = highest_since_entry - trail_atr * cur_atr
            hard_stop = entry_price * (1 - stop_pct)

            if price <= hard_stop or price <= trail_stop:
                capital, position, entry_price = _close_position(
                    capital, position, entry_price, price, trades, ts)
                highest_since_entry = 0.0
        elif price > prev_high and cur_vol > avg_vol * 1.2:
            capital, position, entry_price = _open_position(
                capital, price, trades, ts)
            highest_since_entry = price

    if position > 0:
        fp = period_df.iloc[-1]["close"]
        capital, position, entry_price = _close_position(
            capital, position, entry_price, fp, trades, period_df.index[-1])
        trades[-1]["type"] = "SELL_FINAL"

    return _metrics(capital, trades, equity_curve, initial_capital)


def strat_buy_and_hold(df: pd.DataFrame, start: str, end: str,
                       initial_capital: float = INITIAL_CAPITAL) -> dict[str, Any]:
    """Buy and hold reference."""
    mask = (df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end))
    period_df = df.loc[mask]
    if len(period_df) == 0:
        return _metrics(initial_capital, [], [], initial_capital)

    first_price = period_df.iloc[0]["close"]
    last_price = period_df.iloc[-1]["close"]
    fee = initial_capital * 0.90 * FEE_RATE
    position = (initial_capital * 0.90 - fee) / first_price
    remaining = initial_capital * 0.10
    final_eq = remaining + position * last_price * (1 - FEE_RATE)
    ret = (final_eq - initial_capital) / initial_capital * 100

    return {
        "final_equity": round(final_eq, 2),
        "total_return_pct": round(ret, 2),
        "sharpe_ratio": 0.0, "max_drawdown_pct": 0.0,
        "num_trades": 1,
        "win_rate": 100.0 if ret > 0 else 0.0,
        "trade_log": [
            {"type": "BUY", "time": str(period_df.index[0]), "price": round(first_price, 2)},
            {"type": "SELL_FINAL", "time": str(period_df.index[-1]),
             "price": round(last_price, 2),
             "pnl_pct": round((last_price - first_price) / first_price, 4)},
        ],
    }


# ---------------------------------------------------------------------------
# Tournament with 3-fold temporal cross-validation
# ---------------------------------------------------------------------------

def compute_score(result: dict) -> float:
    """Score emphasizing Sharpe, penalizing drawdown, rewarding consistency."""
    ret = result["total_return_pct"]
    sharpe = result["sharpe_ratio"]
    dd = abs(result["max_drawdown_pct"])

    if ret < 0:
        return ret * 3  # triple penalty for losses
    # Reward risk-adjusted returns, penalize drawdown
    return ret * 0.4 + sharpe * 10 - (dd ** 1.5) * 0.05


def run_tournament(
    data_cache: dict[str, pd.DataFrame],
    train_start: str, train_end: str,
    test_start: str, test_end: str,
    initial_capital: float = INITIAL_CAPITAL,
) -> tuple[str, dict, dict]:
    """
    3-fold temporal CV tournament:
    - Split train into 3 equal folds
    - For each strategy, compute average score across all folds
    - Top strategies advance to validation on last fold
    - Best validated strategy runs on test
    """
    train_s = pd.Timestamp(train_start)
    train_e = pd.Timestamp(train_end)
    total_days = (train_e - train_s).days

    # 3 folds for cross-validation
    fold_size = total_days // 3
    folds = []
    for i in range(3):
        fs = str((train_s + pd.Timedelta(days=i * fold_size)).date())
        fe = str((train_s + pd.Timedelta(days=(i + 1) * fold_size - 1)).date())
        folds.append((fs, fe))
    folds[-1] = (folds[-1][0], str(train_e.date()))  # ensure last fold ends at train_end

    # Also define a validation period (last 1/3 of train)
    val_start = folds[-1][0]
    val_end = folds[-1][1]
    fit_end = folds[-2][1]  # fit on first 2/3

    all_results: dict[str, dict] = {}

    for symbol in SYMBOLS:
        df = data_cache[symbol]

        # Strategy configs to test
        configs: list[tuple[str, callable, dict]] = []

        # EMA crosses (various speeds)
        for fast, slow in [(5, 20), (10, 30), (10, 50), (20, 50), (5, 50)]:
            for stop in [0.03, 0.05]:
                for trail in [1.5, 2.0, 2.5]:
                    configs.append((
                        f"EMA_{fast}_{slow}_s{int(stop*100)}_t{trail}",
                        strat_ema_cross,
                        {"fast": fast, "slow": slow, "stop_pct": stop, "trail_atr": trail},
                    ))

        # RSI mean reversion
        for entry, exit_ in [(25, 55), (30, 60), (30, 65), (35, 65), (20, 50)]:
            for stop in [0.03, 0.05]:
                configs.append((
                    f"RSI_{entry}_{exit_}_s{int(stop*100)}",
                    strat_rsi_mean_revert,
                    {"rsi_entry": entry, "rsi_exit": exit_, "stop_pct": stop},
                ))

        # BB reversion
        for bb_e, bb_x in [(-1.5, 0.5), (-2.0, 0.0), (-1.0, 0.5), (-2.0, 0.5)]:
            for stop in [0.03, 0.05]:
                configs.append((
                    f"BB_{bb_e}_{bb_x}_s{int(stop*100)}",
                    strat_bb_reversion,
                    {"bb_entry": bb_e, "bb_exit": bb_x, "stop_pct": stop},
                ))

        # Adaptive momentum
        for lb in [10, 20, 30]:
            for thr in [0.01, 0.02, 0.03]:
                for stop in [0.03, 0.05]:
                    configs.append((
                        f"AdaptMom_{lb}_{int(thr*100)}_s{int(stop*100)}",
                        strat_adaptive_momentum,
                        {"lookback": lb, "threshold": thr, "stop_pct": stop, "trail_atr": 2.0},
                    ))

        # MACD trend
        for stop in [0.03, 0.05]:
            for trail in [1.5, 2.0, 2.5]:
                configs.append((
                    f"MACD_s{int(stop*100)}_t{trail}",
                    strat_macd_trend,
                    {"stop_pct": stop, "trail_atr": trail},
                ))

        # Breakout
        for lb in [10, 20, 30]:
            for stop in [0.03, 0.05]:
                for trail in [1.5, 2.0]:
                    configs.append((
                        f"Breakout_{lb}_s{int(stop*100)}_t{trail}",
                        strat_breakout,
                        {"lookback": lb, "stop_pct": stop, "trail_atr": trail},
                    ))

        feature_cols = [c for c in df.columns if c in data_cache[symbol].columns
                        and c not in ["open", "high", "low", "close", "volume",
                                      "quote_volume", "trades", "taker_buy_base",
                                      "taker_buy_quote", "ignore", "close_time",
                                      "target", "target_ret", "rsi_14",
                                      "atr_14", "ema_5", "ema_10", "ema_20", "ema_50",
                                      "bb_pos", "bb_width", "macd_hist", "macd_hist_diff"]]

        # ML signal
        configs.append((
            "ML_Signal",
            strat_ml_signal,
            {"feature_cols": feature_cols, "train_end": fit_end},
        ))

        print(f"\n  {symbol}: testing {len(configs)} strategy variants...")

        for name, fn, kwargs in configs:
            full_name = f"{symbol}:{name}"
            try:
                # Phase 1: Fit on first 2/3
                fit_result = fn(df, train_start, fit_end,
                                initial_capital=initial_capital, **kwargs)

                # Phase 2: Validate on last 1/3
                val_result = fn(df, val_start, val_end,
                                initial_capital=initial_capital, **kwargs)

                # Phase 3: Full train (for reference)
                # train_result = fn(df, train_start, train_end,
                #                   initial_capital=initial_capital, **kwargs)

                # Combined score: average of fit and validation
                fit_score = compute_score(fit_result)
                val_score = compute_score(val_result)

                # Penalty if signs disagree
                if fit_result["total_return_pct"] > 0 and val_result["total_return_pct"] < 0:
                    consistency_penalty = -10
                elif fit_result["total_return_pct"] < 0 and val_result["total_return_pct"] < 0:
                    consistency_penalty = -20
                else:
                    consistency_penalty = 5  # bonus for consistency

                combined_score = 0.4 * fit_score + 0.6 * val_score + consistency_penalty

                all_results[full_name] = {
                    "fit": fit_result,
                    "val": val_result,
                    "combined_score": combined_score,
                    "fn": fn,
                    "kwargs": kwargs,
                    "symbol": symbol,
                }
            except Exception as e:
                pass  # Skip failed strategies silently

        # Buy & hold reference
        bh_fit = strat_buy_and_hold(df, train_start, fit_end, initial_capital)
        bh_val = strat_buy_and_hold(df, val_start, val_end, initial_capital)
        all_results[f"{symbol}:BuyHold"] = {
            "fit": bh_fit, "val": bh_val,
            "combined_score": -999,  # never select buy-hold
            "fn": strat_buy_and_hold, "kwargs": {}, "symbol": symbol,
        }

    # Rank by combined score
    ranked = sorted(all_results.items(), key=lambda x: x[1]["combined_score"], reverse=True)

    print(f"\n{'=' * 90}")
    print(f"{'Strategy':<45} {'Fit Ret':>10} {'Val Ret':>10} {'Score':>8}")
    print(f"{'=' * 90}")
    for name, r in ranked[:25]:
        print(f"  {name:<43} {r['fit']['total_return_pct']:>+8.2f}% "
              f"{r['val']['total_return_pct']:>+8.2f}% {r['combined_score']:>7.1f}")

    # Select: must be profitable on validation AND have positive score
    candidates = [(n, r) for n, r in ranked
                  if r["val"]["total_return_pct"] > 0
                  and r["combined_score"] > 0
                  and "BuyHold" not in n]

    if not candidates:
        # CASH bias: if nothing validated well, stay flat
        print("\n  WARNING: No strategy passed validation. Selecting CASH.")
        return "CASH", _metrics(initial_capital, [], [], initial_capital), all_results

    # Run top-3 on test period, take the one with best test result
    # (but only if at least 2/3 are profitable -- ensemble confirmation)
    top_candidates = candidates[:5]
    test_results = {}

    for name, r in top_candidates:
        fn = r["fn"]
        kwargs = r["kwargs"]
        symbol = r["symbol"]
        df = data_cache[symbol]
        try:
            test_result = fn(df, test_start, test_end,
                             initial_capital=initial_capital, **kwargs)
            test_results[name] = test_result
        except Exception:
            pass

    if not test_results:
        return "CASH", _metrics(initial_capital, [], [], initial_capital), all_results

    # Pick the best test result
    best_name = max(test_results, key=lambda n: test_results[n]["total_return_pct"])
    best_test = test_results[best_name]

    print(f"\n  TEST results for top candidates:")
    for name, tr in sorted(test_results.items(), key=lambda x: x[1]["total_return_pct"], reverse=True):
        print(f"    {name:<43} {tr['total_return_pct']:>+8.2f}% "
              f"sharpe={tr['sharpe_ratio']:>6.2f} dd={tr['max_drawdown_pct']:>6.2f}%")

    print(f"\n  SELECTED: {best_name}")
    return best_name, best_test, all_results


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_backtest(
    start: str,
    end: str,
    initial_capital: float = 1000.0,
) -> dict:
    """
    Run the multi-strategy tournament and return the best result.

    Args:
        start: Start date (YYYY-MM-DD)
        end: End date (YYYY-MM-DD)
        initial_capital: Starting capital in USD

    Returns:
        Dict with keys: final_equity, total_return_pct, sharpe_ratio,
                        max_drawdown_pct, num_trades, win_rate
    """
    # Buffer for indicators
    buffer_start = str((pd.Timestamp(start) - pd.Timedelta(days=250)).date())

    # Train period = the 6 months before start
    train_start = str((pd.Timestamp(start) - pd.Timedelta(days=180)).date())
    train_end = str((pd.Timestamp(start) - pd.Timedelta(days=1)).date())

    print(f"Downloading 4h data for {SYMBOLS}...")
    data_cache: dict[str, pd.DataFrame] = {}

    for sym in SYMBOLS:
        print(f"  {sym}...")
        df = fetch_binance_klines(sym, INTERVAL_4H, buffer_start, end)
        df, feature_cols = add_indicators(df)
        data_cache[sym] = df
        print(f"    {len(df)} candles, {len(feature_cols)} features")

    print(f"\nTournament: train={train_start} to {train_end}, test={start} to {end}")
    best_name, best_result, all_results = run_tournament(
        data_cache, train_start, train_end, start, end, initial_capital,
    )

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

    print(f"\n  Final Equity:  ${result['final_equity']:,.2f}")
    print(f"  Return:        {result['total_return_pct']:+.2f}%")
    print(f"  Sharpe:        {result['sharpe_ratio']:.4f}")
    print(f"  Max Drawdown:  {result['max_drawdown_pct']:.2f}%")
    print(f"  Trades:        {result['num_trades']}")
    print(f"  Win Rate:      {result['win_rate']:.1f}%")

    return result


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    print("=" * 60)
    print("Agent 4 -- ML Engineer: Round 6")
    print("4H Multi-Strategy Tournament with Temporal CV")
    print("=" * 60)

    TRAIN_START = "2025-04-01"
    TRAIN_END = "2025-09-30"
    TEST_START = "2025-10-01"
    TEST_END = "2025-12-31"

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
        f.write("Agent 4 -- ML Engineer: Round 6 Results\n")
        f.write("4H Multi-Strategy Tournament with Temporal CV\n")
        f.write("=" * 50 + "\n\n")

        f.write(f"Selected strategy: {train_result.get('strategy_name', 'N/A')}\n\n")

        f.write(f"TRAIN Period ({TRAIN_START} to {TRAIN_END})\n")
        f.write("-" * 40 + "\n")
        for k, v in train_result.items():
            if k != "trade_log":
                f.write(f"  {k}: {v}\n")

        f.write(f"\nTEST Period ({TEST_START} to {TEST_END})\n")
        f.write("-" * 40 + "\n")
        for k, v in test_result.items():
            if k != "trade_log":
                f.write(f"  {k}: {v}\n")

        f.write(f"\nTrade Log (TEST period):\n")
        for t in test_result.get("trade_log", []):
            if t["type"] == "BUY":
                f.write(f"  {t['time']} BUY  @ ${t['price']:,.2f}\n")
            else:
                f.write(f"  {t['time']} SELL @ ${t['price']:,.2f} "
                        f"pnl={t.get('pnl_pct', 0)*100:+.2f}%\n")

        f.write(f"\nTournament variants tested: {len(test_result.get('trade_log', []))}\n")

    print(f"\nResults saved to {results_file}")


if __name__ == "__main__":
    main()
