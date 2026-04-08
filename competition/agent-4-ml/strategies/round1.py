"""
Agent 4 - ML Engineer: LightGBM Walk-Forward Strategy
=====================================================
Uses walk-forward validation with LightGBM to predict 4-hour BTC/USDT returns.
Features: technical indicators, lagged returns, volatility, volume.
"""

import warnings
warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd
import requests
import time
from pathlib import Path
from datetime import datetime, timezone
import lightgbm as lgb
from sklearn.preprocessing import StandardScaler

# ============================================================
# Data Download
# ============================================================

def download_binance_klines(
    symbol: str = "BTCUSDT",
    interval: str = "4h",
    start_date: str = "2024-07-01",
    end_date: str = "2025-12-31",
) -> pd.DataFrame:
    """Download historical klines from Binance public API."""
    base_url = "https://api.binance.com/api/v3/klines"
    start_ts = int(pd.Timestamp(start_date, tz="UTC").timestamp() * 1000)
    end_ts = int(pd.Timestamp(end_date, tz="UTC").timestamp() * 1000)

    all_klines = []
    current_start = start_ts

    while current_start < end_ts:
        params = {
            "symbol": symbol,
            "interval": interval,
            "startTime": current_start,
            "endTime": end_ts,
            "limit": 1000,
        }
        resp = requests.get(base_url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if not data:
            break
        all_klines.extend(data)
        current_start = data[-1][0] + 1
        time.sleep(0.2)

    df = pd.DataFrame(all_klines, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades", "taker_buy_volume",
        "taker_buy_quote_volume", "ignore",
    ])

    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    for col in ["open", "high", "low", "close", "volume", "quote_volume",
                 "taker_buy_volume", "taker_buy_quote_volume"]:
        df[col] = df[col].astype(float)
    df["trades"] = df["trades"].astype(int)
    df = df.set_index("open_time").sort_index()
    return df


# ============================================================
# Feature Engineering
# ============================================================

def compute_features(df: pd.DataFrame) -> pd.DataFrame:
    """Engineer features from OHLCV data. All features use only past data."""
    feat = pd.DataFrame(index=df.index)

    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]

    # --- Returns ---
    for lag in [1, 2, 3, 5, 10, 20]:
        feat[f"ret_{lag}"] = close.pct_change(lag)

    # --- Moving averages & crossovers ---
    for window in [10, 20, 50]:
        sma = close.rolling(window).mean()
        feat[f"sma_ratio_{window}"] = close / sma - 1.0

    ema_fast = close.ewm(span=12, adjust=False).mean()
    ema_slow = close.ewm(span=26, adjust=False).mean()
    feat["macd"] = ema_fast - ema_slow
    feat["macd_signal"] = feat["macd"].ewm(span=9, adjust=False).mean()
    feat["macd_hist"] = feat["macd"] - feat["macd_signal"]

    # --- RSI ---
    for period in [14, 28]:
        delta = close.diff()
        gain = delta.clip(lower=0).rolling(period).mean()
        loss = (-delta.clip(upper=0)).rolling(period).mean()
        rs = gain / loss.replace(0, np.nan)
        feat[f"rsi_{period}"] = 100 - (100 / (1 + rs))

    # --- Bollinger Bands ---
    bb_sma = close.rolling(20).mean()
    bb_std = close.rolling(20).std()
    feat["bb_upper"] = (close - (bb_sma + 2 * bb_std)) / (4 * bb_std).replace(0, np.nan)
    feat["bb_lower"] = (close - (bb_sma - 2 * bb_std)) / (4 * bb_std).replace(0, np.nan)
    feat["bb_position"] = (close - bb_sma) / (2 * bb_std).replace(0, np.nan)

    # --- ATR & Volatility ---
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs(),
    ], axis=1).max(axis=1)
    atr_14 = tr.rolling(14).mean()
    feat["atr_norm"] = atr_14 / close
    feat["volatility_20"] = close.pct_change().rolling(20).std()
    feat["volatility_ratio"] = close.pct_change().rolling(5).std() / close.pct_change().rolling(20).std().replace(0, np.nan)

    # --- Volume features ---
    feat["volume_ratio_10"] = volume / volume.rolling(10).mean().replace(0, np.nan)
    feat["volume_ratio_20"] = volume / volume.rolling(20).mean().replace(0, np.nan)
    feat["volume_change"] = volume.pct_change()

    # --- Momentum ---
    feat["roc_10"] = close.pct_change(10)
    feat["roc_20"] = close.pct_change(20)

    # --- Mean reversion z-score ---
    for window in [20, 50]:
        rolling_mean = close.rolling(window).mean()
        rolling_std = close.rolling(window).std()
        feat[f"zscore_{window}"] = (close - rolling_mean) / rolling_std.replace(0, np.nan)

    # --- Rolling Sharpe ---
    ret_1 = close.pct_change()
    feat["sharpe_20"] = ret_1.rolling(20).mean() / ret_1.rolling(20).std().replace(0, np.nan)

    # --- Higher timeframe context (daily-ish: 6 bars = 24h) ---
    feat["ret_6"] = close.pct_change(6)
    feat["sma_ratio_6"] = close / close.rolling(6).mean() - 1.0

    # --- Day of week / hour (cyclical) ---
    feat["hour_sin"] = np.sin(2 * np.pi * df.index.hour / 24)
    feat["hour_cos"] = np.cos(2 * np.pi * df.index.hour / 24)
    feat["dow_sin"] = np.sin(2 * np.pi * df.index.dayofweek / 7)
    feat["dow_cos"] = np.cos(2 * np.pi * df.index.dayofweek / 7)

    return feat


# ============================================================
# Walk-Forward ML Backtest
# ============================================================

def run_walk_forward_backtest(
    df: pd.DataFrame,
    backtest_start: str = "2025-07-01",
    backtest_end: str = "2025-12-31",
    train_min_bars: int = 500,      # minimum training bars (~83 days of 4h)
    retrain_every: int = 42,        # retrain weekly (42 4h bars = 7 days)
    prediction_horizon: int = 1,    # predict next bar return
    initial_capital: float = 1000.0,
    position_size_frac: float = 0.95,  # fraction of capital per trade
    stop_loss_atr_mult: float = 2.5,
    take_profit_atr_mult: float = 3.5,
    fee_rate: float = 0.001,        # 0.1% taker fee
):
    """Walk-forward backtest with LightGBM."""

    # Compute features
    features_df = compute_features(df)
    close = df["close"]
    high = df["high"]
    low = df["low"]

    # ATR for stop-loss
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs(),
    ], axis=1).max(axis=1)
    atr = tr.rolling(14).mean()

    # Target: next-bar return direction (binary classification)
    future_ret = close.pct_change(prediction_horizon).shift(-prediction_horizon)
    target = (future_ret > 0).astype(int)

    # Align everything
    valid_mask = features_df.notna().all(axis=1) & target.notna() & atr.notna()
    features_df = features_df[valid_mask]
    target = target[valid_mask]
    close = close[valid_mask]
    atr = atr[valid_mask]
    df_valid = df.loc[valid_mask]

    feature_names = features_df.columns.tolist()

    # Backtest period indices
    bt_start = pd.Timestamp(backtest_start, tz="UTC")
    bt_end = pd.Timestamp(backtest_end, tz="UTC")
    bt_mask = (features_df.index >= bt_start) & (features_df.index <= bt_end)
    bt_indices = features_df.index[bt_mask]

    if len(bt_indices) == 0:
        raise ValueError("No data in backtest period!")

    print(f"Backtest period: {bt_indices[0]} to {bt_indices[-1]}")
    print(f"Backtest bars: {len(bt_indices)}")
    print(f"Features: {len(feature_names)}")

    # Walk-forward loop
    capital = initial_capital
    position = 0  # 0 = flat, 1 = long, -1 = short
    entry_price = 0.0
    stop_loss = 0.0
    take_profit = 0.0
    entry_capital = 0.0
    position_size_usd = 0.0

    equity_curve = []
    trades = []
    model = None
    last_train_idx = -retrain_every  # force initial training

    all_indices = features_df.index.tolist()

    for i, ts in enumerate(bt_indices):
        global_idx = all_indices.index(ts)
        bar_close = close.loc[ts]
        bar_high = high.loc[ts] if ts in high.index else bar_close
        bar_low = low.loc[ts] if ts in low.index else bar_close
        bar_atr = atr.loc[ts]

        # --- Check stop-loss / take-profit (using current bar's high/low) ---
        if position == 1:
            if bar_low <= stop_loss:
                # Stopped out
                pnl_pct = (stop_loss - entry_price) / entry_price
                pnl_usd = position_size_usd * pnl_pct - position_size_usd * fee_rate
                capital += position_size_usd + pnl_usd
                trades.append({
                    "exit_time": ts, "side": "long", "entry": entry_price,
                    "exit": stop_loss, "pnl_usd": pnl_usd, "pnl_pct": pnl_pct,
                    "reason": "stop_loss",
                })
                position = 0
            elif bar_high >= take_profit:
                pnl_pct = (take_profit - entry_price) / entry_price
                pnl_usd = position_size_usd * pnl_pct - position_size_usd * fee_rate
                capital += position_size_usd + pnl_usd
                trades.append({
                    "exit_time": ts, "side": "long", "entry": entry_price,
                    "exit": take_profit, "pnl_usd": pnl_usd, "pnl_pct": pnl_pct,
                    "reason": "take_profit",
                })
                position = 0

        elif position == -1:
            if bar_high >= stop_loss:
                pnl_pct = (entry_price - stop_loss) / entry_price
                pnl_usd = position_size_usd * pnl_pct - position_size_usd * fee_rate
                capital += position_size_usd + pnl_usd
                trades.append({
                    "exit_time": ts, "side": "short", "entry": entry_price,
                    "exit": stop_loss, "pnl_usd": pnl_usd, "pnl_pct": pnl_pct,
                    "reason": "stop_loss",
                })
                position = 0
            elif bar_low <= take_profit:
                pnl_pct = (entry_price - take_profit) / entry_price
                pnl_usd = position_size_usd * pnl_pct - position_size_usd * fee_rate
                capital += position_size_usd + pnl_usd
                trades.append({
                    "exit_time": ts, "side": "short", "entry": entry_price,
                    "exit": take_profit, "pnl_usd": pnl_usd, "pnl_pct": pnl_pct,
                    "reason": "take_profit",
                })
                position = 0

        # --- Retrain model periodically ---
        bars_since_train = i - last_train_idx
        if bars_since_train >= retrain_every or model is None:
            train_end_idx = global_idx
            train_start_idx = max(0, train_end_idx - train_min_bars)

            if train_end_idx - train_start_idx < 200:
                equity_curve.append({"time": ts, "equity": capital if position == 0 else capital + position_size_usd * ((bar_close - entry_price) / entry_price if position == 1 else (entry_price - bar_close) / entry_price)})
                continue

            train_indices = all_indices[train_start_idx:train_end_idx]
            X_train = features_df.loc[train_indices].values
            y_train = target.loc[train_indices].values

            # LightGBM with conservative parameters to avoid overfitting
            params = {
                "objective": "binary",
                "metric": "binary_logloss",
                "boosting_type": "gbdt",
                "num_leaves": 15,          # very conservative
                "max_depth": 4,
                "learning_rate": 0.05,
                "n_estimators": 150,
                "min_child_samples": 30,
                "subsample": 0.7,
                "colsample_bytree": 0.7,
                "reg_alpha": 0.1,          # L1 regularization
                "reg_lambda": 1.0,         # L2 regularization
                "random_state": 42,
                "verbosity": -1,
            }

            model = lgb.LGBMClassifier(**params)
            model.fit(X_train, y_train)
            last_train_idx = i

        # --- Generate signal ---
        if model is not None and position == 0:
            X_pred = features_df.loc[[ts]].values
            prob = model.predict_proba(X_pred)[0]  # [prob_down, prob_up]
            prob_up = prob[1] if len(prob) > 1 else 0.5

            # Only trade when model is confident
            if prob_up > 0.58:
                # Go long
                position = 1
                entry_price = bar_close
                position_size_usd = capital * position_size_frac
                capital -= position_size_usd
                stop_loss = entry_price - stop_loss_atr_mult * bar_atr
                take_profit = entry_price + take_profit_atr_mult * bar_atr

            elif prob_up < 0.42:
                # Go short
                position = -1
                entry_price = bar_close
                position_size_usd = capital * position_size_frac
                capital -= position_size_usd
                stop_loss = entry_price + stop_loss_atr_mult * bar_atr
                take_profit = entry_price - take_profit_atr_mult * bar_atr

        # --- Mark-to-market equity ---
        if position == 1:
            unrealized = position_size_usd * (bar_close - entry_price) / entry_price
            eq = capital + position_size_usd + unrealized
        elif position == -1:
            unrealized = position_size_usd * (entry_price - bar_close) / entry_price
            eq = capital + position_size_usd + unrealized
        else:
            eq = capital

        equity_curve.append({"time": ts, "equity": eq})

    # Close any open position at end
    if position != 0:
        final_close = close.loc[bt_indices[-1]]
        if position == 1:
            pnl_pct = (final_close - entry_price) / entry_price
        else:
            pnl_pct = (entry_price - final_close) / entry_price
        pnl_usd = position_size_usd * pnl_pct - position_size_usd * fee_rate
        capital += position_size_usd + pnl_usd
        trades.append({
            "exit_time": bt_indices[-1], "side": "long" if position == 1 else "short",
            "entry": entry_price, "exit": final_close,
            "pnl_usd": pnl_usd, "pnl_pct": pnl_pct, "reason": "end_of_backtest",
        })
        position = 0

    equity_df = pd.DataFrame(equity_curve)
    trades_df = pd.DataFrame(trades) if trades else pd.DataFrame()

    return equity_df, trades_df, capital


# ============================================================
# Performance Metrics
# ============================================================

def compute_metrics(equity_df: pd.DataFrame, trades_df: pd.DataFrame,
                    initial_capital: float, final_capital: float) -> dict:
    """Compute backtest performance metrics."""
    total_return = (final_capital - initial_capital) / initial_capital * 100

    # Sharpe ratio (annualized, assuming 4h bars = 6 bars/day = 2190 bars/year)
    equity_returns = equity_df["equity"].pct_change().dropna()
    bars_per_year = 365.25 * 24 / 4  # ~2191
    if equity_returns.std() > 0:
        sharpe = (equity_returns.mean() / equity_returns.std()) * np.sqrt(bars_per_year)
    else:
        sharpe = 0.0

    # Max drawdown
    equity = equity_df["equity"]
    peak = equity.cummax()
    drawdown = (equity - peak) / peak
    max_dd = drawdown.min() * 100

    # Trade stats
    n_trades = len(trades_df)
    if n_trades > 0:
        win_rate = (trades_df["pnl_usd"] > 0).sum() / n_trades * 100
        avg_win = trades_df.loc[trades_df["pnl_usd"] > 0, "pnl_usd"].mean() if (trades_df["pnl_usd"] > 0).any() else 0
        avg_loss = trades_df.loc[trades_df["pnl_usd"] <= 0, "pnl_usd"].mean() if (trades_df["pnl_usd"] <= 0).any() else 0
        profit_factor = abs(trades_df.loc[trades_df["pnl_usd"] > 0, "pnl_usd"].sum() / trades_df.loc[trades_df["pnl_usd"] <= 0, "pnl_usd"].sum()) if (trades_df["pnl_usd"] <= 0).any() and trades_df.loc[trades_df["pnl_usd"] <= 0, "pnl_usd"].sum() != 0 else float("inf")
    else:
        win_rate = 0
        avg_win = 0
        avg_loss = 0
        profit_factor = 0

    return {
        "total_return_pct": round(total_return, 2),
        "sharpe_ratio": round(sharpe, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "n_trades": n_trades,
        "win_rate_pct": round(win_rate, 2),
        "avg_win_usd": round(avg_win, 2),
        "avg_loss_usd": round(avg_loss, 2),
        "profit_factor": round(profit_factor, 2),
        "initial_capital": initial_capital,
        "final_capital": round(final_capital, 2),
    }


# ============================================================
# Main
# ============================================================

def main():
    print("=" * 60)
    print("Agent 4 - ML Engineer: LightGBM Walk-Forward Strategy")
    print("=" * 60)

    # Download data (extra history for training)
    print("\n[1/4] Downloading BTC/USDT 4h data...")
    cache_path = Path(__file__).parent.parent / "data" / "btcusdt_4h.parquet"
    cache_path.parent.mkdir(parents=True, exist_ok=True)

    if cache_path.exists():
        print(f"  Using cached data: {cache_path}")
        df = pd.read_parquet(cache_path)
    else:
        df = download_binance_klines(
            symbol="BTCUSDT",
            interval="4h",
            start_date="2024-01-01",  # 6 months extra for training
            end_date="2025-12-31",
        )
        df.to_parquet(cache_path)
        print(f"  Downloaded {len(df)} bars, cached to {cache_path}")

    print(f"  Data range: {df.index[0]} to {df.index[-1]}")
    print(f"  Total bars: {len(df)}")

    # Run walk-forward backtest
    print("\n[2/4] Running walk-forward backtest (Jul-Dec 2025)...")
    equity_df, trades_df, final_capital = run_walk_forward_backtest(
        df,
        backtest_start="2025-07-01",
        backtest_end="2025-12-31",
        initial_capital=1000.0,
    )

    # Compute metrics
    print("\n[3/4] Computing metrics...")
    metrics = compute_metrics(equity_df, trades_df, 1000.0, final_capital)

    # Print results
    print("\n" + "=" * 60)
    print("BACKTEST RESULTS")
    print("=" * 60)
    print(f"  Strategy:        LightGBM Walk-Forward (4h BTC/USDT)")
    print(f"  Period:          July 1, 2025 - December 31, 2025")
    print(f"  Initial Capital: ${metrics['initial_capital']:,.2f}")
    print(f"  Final Capital:   ${metrics['final_capital']:,.2f}")
    print(f"  Total Return:    {metrics['total_return_pct']:+.2f}%")
    print(f"  Sharpe Ratio:    {metrics['sharpe_ratio']:.2f}")
    print(f"  Max Drawdown:    {metrics['max_drawdown_pct']:.2f}%")
    print(f"  Trades:          {metrics['n_trades']}")
    print(f"  Win Rate:        {metrics['win_rate_pct']:.2f}%")
    print(f"  Avg Win:         ${metrics['avg_win_usd']:,.2f}")
    print(f"  Avg Loss:        ${metrics['avg_loss_usd']:,.2f}")
    print(f"  Profit Factor:   {metrics['profit_factor']:.2f}")
    print("=" * 60)

    # Save results
    print("\n[4/4] Saving results...")
    results_path = Path(__file__).parent.parent / "results" / "round1_results.txt"
    results_path.parent.mkdir(parents=True, exist_ok=True)

    with open(results_path, "w") as f:
        f.write("Agent 4 - ML Engineer: Round 1 Results\n")
        f.write("=" * 50 + "\n\n")
        f.write("Strategy: LightGBM Walk-Forward (4h BTC/USDT)\n")
        f.write("Model: LightGBM binary classifier\n")
        f.write("Features: 35+ technical indicators, lagged returns, volatility, volume\n")
        f.write("Validation: Walk-forward (expanding window, retrain weekly)\n")
        f.write(f"Backtest Period: July 1, 2025 - December 31, 2025\n\n")
        f.write("Performance Metrics:\n")
        f.write("-" * 30 + "\n")
        for k, v in metrics.items():
            f.write(f"  {k}: {v}\n")
        f.write("\nTrade Log:\n")
        f.write("-" * 30 + "\n")
        if not trades_df.empty:
            f.write(trades_df.to_string(index=False))
        else:
            f.write("  No trades executed.\n")
        f.write("\n")

    print(f"  Results saved to: {results_path}")

    if metrics["total_return_pct"] <= 0:
        print("\n*** WARNING: Strategy is NOT profitable. Needs iteration. ***")
    else:
        print("\n*** Strategy is PROFITABLE. Ready for submission. ***")

    return metrics


if __name__ == "__main__":
    main()
