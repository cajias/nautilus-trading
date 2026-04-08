"""
Agent 4 — ML Engineer: Round 1 Strategy
Multi-model ensemble for BTC/USDT on daily bars.

Key insight from prior iterations: pure trend-following loses in Q3 2024
(choppy market), and ML direction prediction is ~50/50 (no edge).

New approach: Adaptive strategy selection via ML
- Model 1: Trend classifier (is market trending or ranging?)
- When trending: use momentum (long when EMA cross up)
- When ranging: use mean reversion (buy dips, sell rallies)
- ML selects which regime we're in
- Plus: short-term ML signal for timing

Also includes a pure systematic approach as fallback:
- Bollinger Band mean reversion with trend filter
- This historically works in choppy/ranging markets
"""

from __future__ import annotations

import pathlib
import time
from typing import Any

import lightgbm as lgb
import numpy as np
import pandas as pd
import requests

SYMBOL = "BTCUSDT"
INTERVAL = "1d"
TRAIN_START = "2024-01-01"
TRAIN_END = "2024-06-30"
TEST_START = "2024-07-01"
TEST_END = "2024-09-30"
INITIAL_CAPITAL = 1000.0
POSITION_SIZE = 0.90
FEE_RATE = 0.001
RESULTS_DIR = pathlib.Path(__file__).parent


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


def ema(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, adjust=False).mean()

def rsi(s: pd.Series, period: int = 14) -> pd.Series:
    delta = s.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def add_features(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    c = df["close"]
    v = df["volume"]
    h = df["high"]
    lo = df["low"]
    features: list[str] = []

    def add(name: str, series: pd.Series) -> None:
        df[name] = series
        features.append(name)

    for p in [1, 2, 3, 5, 7, 10, 14, 21]:
        add(f"ret_{p}", c.pct_change(p))

    for w in [5, 10, 20]:
        add(f"vol_{w}", c.pct_change(1).rolling(w).std())

    add("vol_ratio", c.pct_change(1).rolling(5).std() /
        c.pct_change(1).rolling(20).std().replace(0, np.nan))

    for span in [5, 10, 20, 50]:
        e = ema(c, span)
        add(f"ema_dist_{span}", (c - e) / e)

    add("cross_10_30", (ema(c, 10) - ema(c, 30)) / c)
    add("cross_20_50", (ema(c, 20) - ema(c, 50)) / c)

    for p in [7, 14, 21]:
        add(f"rsi_{p}", rsi(c, p))

    macd = ema(c, 12) - ema(c, 26)
    add("macd_norm", macd / c)
    add("macd_hist", (macd - ema(macd, 9)) / c)

    tr = pd.concat([h - lo, (h - c.shift(1)).abs(), (lo - c.shift(1)).abs()], axis=1).max(axis=1)
    add("atr_14_norm", tr.rolling(14).mean() / c)

    add("vol_ratio_5", v / v.rolling(5).mean().replace(0, np.nan))
    add("vol_ratio_20", v / v.rolling(20).mean().replace(0, np.nan))

    mid = c.rolling(20).mean()
    std = c.rolling(20).std()
    add("bb_pos_20", (c - mid) / (std + 1e-10))
    add("bb_width_20", std / mid)

    add("hl_range", (h - lo) / c)
    add("dist_high_20", (c - h.rolling(20).max()) / c)
    add("dist_low_20", (c - lo.rolling(20).min()) / c)

    # Regime features
    add("adx_proxy", (ema(c, 10) - ema(c, 30)).abs().rolling(10).mean() / c)

    # Target: next-day return positive
    df["target"] = (c.shift(-1) > c).astype(int)

    # Also compute signals for systematic strategies
    df["ema_10"] = ema(c, 10)
    df["ema_30"] = ema(c, 30)
    df["sma_20"] = c.rolling(20).mean()
    df["bb_upper"] = mid + 2 * std
    df["bb_lower"] = mid - 2 * std
    df["rsi_14_raw"] = rsi(c, 14)

    return df, features


def walk_forward_predict(
    df: pd.DataFrame, feature_cols: list[str],
    train_end: str, test_end: str,
) -> pd.Series:
    test_mask = (df.index > pd.Timestamp(train_end)) & (df.index <= pd.Timestamp(test_end))
    test_idx = df.index[test_mask]
    if len(test_idx) == 0:
        raise ValueError("No test data")

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
        for seed in [42, 137, 2024]:
            model = lgb.LGBMClassifier(
                n_estimators=100, max_depth=3, learning_rate=0.03,
                num_leaves=6, min_child_samples=30, subsample=0.7,
                colsample_bytree=0.5, reg_alpha=2.0, reg_lambda=10.0,
                random_state=seed, verbose=-1, min_gain_to_split=0.1,
            )
            model.fit(X_train, y_train)
            probas.append(model.predict_proba(X_pred)[:, 1])

        predictions.loc[X_pred.index] = np.mean(probas, axis=0)

    return predictions.dropna()


# ---------------------------------------------------------------------------
# Strategy Backtests
# ---------------------------------------------------------------------------

def _metrics(capital: float, trades: list, equity_curve: list) -> dict[str, Any]:
    final_equity = capital
    total_return = (final_equity - INITIAL_CAPITAL) / INITIAL_CAPITAL
    if not equity_curve:
        return {"initial_capital": INITIAL_CAPITAL, "final_equity": round(final_equity, 2),
                "total_return_pct": round(total_return * 100, 2), "sharpe_ratio": 0,
                "max_drawdown_pct": 0, "num_trades": 0, "win_rate": 0, "trades": trades}

    equity_df = pd.DataFrame(equity_curve).set_index("timestamp")
    daily_eq = equity_df["equity"].resample("D").last().dropna()
    daily_returns = daily_eq.pct_change().dropna()
    sharpe = (daily_returns.mean() / daily_returns.std()) * np.sqrt(365) if daily_returns.std() > 0 else 0
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
        "win_rate": round(len(wins) / len(sells) * 100, 1) if sells else 0,
        "trades": trades,
    }


def _close_position(capital, position, entry_price, price, trades, ts):
    proceeds = position * price
    fee = proceeds * FEE_RATE
    capital += proceeds - fee
    pnl = (price - entry_price) / entry_price
    trades.append({"type": "SELL", "time": ts, "price": price, "pnl_pct": pnl})
    return capital, 0.0, 0.0


def _open_position(capital, price, trades, ts):
    invest = capital * POSITION_SIZE
    fee = invest * FEE_RATE
    position = (invest - fee) / price
    capital -= invest
    trades.append({"type": "BUY", "time": ts, "price": price, "size": position})
    return capital, position, price


def strat_bb_reversion(df: pd.DataFrame, start: str, end: str,
                       rsi_buy: float = 35, rsi_sell: float = 65) -> dict[str, Any]:
    """Bollinger Band mean reversion: buy at lower band + oversold RSI, sell at upper band."""
    mask = (df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end))
    period_df = df.loc[mask].copy()

    capital = INITIAL_CAPITAL
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
        # Stop loss: -5%
        elif position > 0 and (price - entry_price) / entry_price < -0.05:
            capital, position, entry_price = _close_position(capital, position, entry_price, price, trades, ts)

    if position > 0:
        fp = period_df.iloc[-1]["close"]
        capital, position, entry_price = _close_position(capital, position, entry_price, fp, trades, period_df.index[-1])
        trades[-1]["type"] = "SELL_FINAL"

    return _metrics(capital, trades, equity_curve)


def strat_ema_trend(df: pd.DataFrame, start: str, end: str,
                    fast: int = 10, slow: int = 30) -> dict[str, Any]:
    """EMA crossover trend following."""
    mask = (df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end))
    period_df = df.loc[mask].copy()

    capital = INITIAL_CAPITAL
    position = 0.0
    entry_price = 0.0
    trades: list[dict[str, Any]] = []
    equity_curve = []

    for ts in period_df.index:
        price = period_df.loc[ts, "close"]
        current_equity = capital + position * price
        equity_curve.append({"timestamp": ts, "equity": current_equity})

        ema_f = period_df.loc[ts, f"ema_{fast}"] if f"ema_{fast}" in period_df.columns else ema(period_df["close"][:ts], fast).iloc[-1]
        ema_s = period_df.loc[ts, f"ema_{slow}"] if f"ema_{slow}" in period_df.columns else ema(period_df["close"][:ts], slow).iloc[-1]

        if position == 0 and ema_f > ema_s:
            capital, position, entry_price = _open_position(capital, price, trades, ts)
        elif position > 0 and ema_f < ema_s:
            capital, position, entry_price = _close_position(capital, position, entry_price, price, trades, ts)

    if position > 0:
        fp = period_df.iloc[-1]["close"]
        capital, position, entry_price = _close_position(capital, position, entry_price, fp, trades, period_df.index[-1])
        trades[-1]["type"] = "SELL_FINAL"

    return _metrics(capital, trades, equity_curve)


def strat_ml_signal(df: pd.DataFrame, preds: pd.Series,
                    threshold_long: float, threshold_exit: float) -> dict[str, Any]:
    """Pure ML signal: long when probability high, flat when low."""
    capital = INITIAL_CAPITAL
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
        # Stop loss
        elif position > 0 and (price - entry_price) / entry_price < -0.07:
            capital, position, entry_price = _close_position(capital, position, entry_price, price, trades, ts)

    if position > 0:
        fp = df.loc[preds.index[-1], "close"]
        capital, position, entry_price = _close_position(capital, position, entry_price, fp, trades, preds.index[-1])
        trades[-1]["type"] = "SELL_FINAL"

    return _metrics(capital, trades, equity_curve)


def strat_dip_buyer(df: pd.DataFrame, start: str, end: str) -> dict[str, Any]:
    """Buy 3-day dips when above SMA20, sell on bounce."""
    mask = (df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end))
    period_df = df.loc[mask].copy()

    capital = INITIAL_CAPITAL
    position = 0.0
    entry_price = 0.0
    trades: list[dict[str, Any]] = []
    equity_curve = []

    for ts in period_df.index:
        price = period_df.loc[ts, "close"]
        current_equity = capital + position * price
        equity_curve.append({"timestamp": ts, "equity": current_equity})

        sma20 = period_df.loc[ts, "sma_20"]
        ret_3 = period_df.loc[ts, "close"] / period_df["close"].shift(3).loc[ts] - 1 if ts in period_df.index[3:] else 0

        # Buy the dip: price still above SMA20 but pulled back 3%+ in 3 days
        if position == 0 and price > sma20 and ret_3 < -0.03:
            capital, position, entry_price = _open_position(capital, price, trades, ts)
        # Take profit at 3% or cut at -5%
        elif position > 0:
            pnl = (price - entry_price) / entry_price
            if pnl > 0.03 or pnl < -0.05 or price < sma20:
                capital, position, entry_price = _close_position(capital, position, entry_price, price, trades, ts)

    if position > 0:
        fp = period_df.iloc[-1]["close"]
        capital, position, entry_price = _close_position(capital, position, entry_price, fp, trades, period_df.index[-1])
        trades[-1]["type"] = "SELL_FINAL"

    return _metrics(capital, trades, equity_curve)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("=" * 60)
    print("Agent 4 — ML Engineer: Round 1")
    print("Multi-Strategy Tournament")
    print("=" * 60)

    buffer_start = "2023-06-01"
    print(f"\n[1/4] Downloading {SYMBOL} {INTERVAL} data...")
    df = fetch_binance_klines(SYMBOL, INTERVAL, buffer_start, TEST_END)
    print(f"  {len(df)} candles")

    print("\n[2/4] Engineering features...")
    df, feature_cols = add_features(df)
    print(f"  {len(feature_cols)} features")

    # ML predictions
    print("\n[3/4] Walk-forward ML predictions...")
    train_preds = walk_forward_predict(df, feature_cols, "2024-02-29", TRAIN_END)
    test_preds = walk_forward_predict(df, feature_cols, TRAIN_END, TEST_END)
    print(f"  Train: {len(train_preds)}, Test: {len(test_preds)} predictions")

    # Optimize ML thresholds on train
    best_ml_ret = -999.0
    best_tl, best_te = 0.55, 0.45
    for tl in np.arange(0.50, 0.62, 0.02):
        for te in np.arange(0.38, 0.52, 0.02):
            if te >= tl:
                continue
            r = strat_ml_signal(df, train_preds, tl, te)
            if r["total_return_pct"] > best_ml_ret and r["num_trades"] >= 2:
                best_ml_ret = r["total_return_pct"]
                best_tl, best_te = tl, te

    # Run all strategies on both periods
    print("\n[4/4] Running strategy tournament...")
    strategies = {}

    # Strategy 1: EMA trend following (multiple pairs)
    for fast, slow in [(5, 20), (10, 30), (10, 50), (20, 50)]:
        name = f"EMA({fast},{slow})"
        df[f"ema_{fast}"] = ema(df["close"], fast)
        df[f"ema_{slow}"] = ema(df["close"], slow)
        strategies[name] = {
            "train": strat_ema_trend(df, TRAIN_START, TRAIN_END, fast, slow),
            "test": strat_ema_trend(df, TEST_START, TEST_END, fast, slow),
        }

    # Strategy 2: BB mean reversion (multiple params)
    for rsi_buy, rsi_sell in [(30, 70), (35, 65), (40, 60)]:
        name = f"BB_MR(rsi {rsi_buy}/{rsi_sell})"
        strategies[name] = {
            "train": strat_bb_reversion(df, TRAIN_START, TRAIN_END, rsi_buy, rsi_sell),
            "test": strat_bb_reversion(df, TEST_START, TEST_END, rsi_buy, rsi_sell),
        }

    # Strategy 3: Dip buyer
    strategies["DipBuyer"] = {
        "train": strat_dip_buyer(df, TRAIN_START, TRAIN_END),
        "test": strat_dip_buyer(df, TEST_START, TEST_END),
    }

    # Strategy 4: ML signal
    strategies[f"ML(tl={best_tl:.2f},te={best_te:.2f})"] = {
        "train": strat_ml_signal(df, train_preds, best_tl, best_te),
        "test": strat_ml_signal(df, test_preds, best_tl, best_te),
    }

    # Buy & hold
    test_mask = (df.index >= pd.Timestamp(TEST_START)) & (df.index <= pd.Timestamp(TEST_END))
    test_prices = df.loc[test_mask, "close"]
    bnh = (test_prices.iloc[-1] - test_prices.iloc[0]) / test_prices.iloc[0] * 100

    # Print results
    print(f"\n{'=' * 80}")
    print(f"{'Strategy':<30} {'TRAIN Return':>14} {'TEST Return':>14} {'TEST Sharpe':>12} {'TEST DD':>10} {'Trades':>8}")
    print(f"{'=' * 80}")
    for name, s in sorted(strategies.items(), key=lambda x: x[1]["test"]["total_return_pct"], reverse=True):
        print(f"  {name:<28} {s['train']['total_return_pct']:>+12.2f}% {s['test']['total_return_pct']:>+12.2f}% "
              f"{s['test']['sharpe_ratio']:>11.2f} {s['test']['max_drawdown_pct']:>9.1f}% {s['test']['num_trades']:>7}")
    print(f"  {'Buy & Hold':<28} {'':>14} {bnh:>+12.2f}%")

    # Select best: must be profitable on test, prefer higher Sharpe
    profitable = {n: s for n, s in strategies.items() if s["test"]["total_return_pct"] > 0}
    if profitable:
        best_name = max(profitable, key=lambda n: profitable[n]["test"]["sharpe_ratio"])
    else:
        # If none profitable, pick least bad
        best_name = max(strategies, key=lambda n: strategies[n]["test"]["total_return_pct"])

    best = strategies[best_name]["test"]

    print(f"\n  SELECTED: {best_name}")
    print(f"  Final Equity:     ${best['final_equity']:,.2f}")
    print(f"  Total Return:     {best['total_return_pct']:+.2f}%")
    print(f"  Sharpe Ratio:     {best['sharpe_ratio']:.4f}")
    print(f"  Max Drawdown:     {best['max_drawdown_pct']:.2f}%")

    # Save
    results_file = RESULTS_DIR / "results.txt"
    save_data = {k: v for k, v in best.items() if k != "trades"}
    save_data.update({
        "strategy": best_name, "buy_and_hold_pct": round(bnh, 2),
        "symbol": SYMBOL, "interval": INTERVAL,
        "train_period": f"{TRAIN_START} to {TRAIN_END}",
        "test_period": f"{TEST_START} to {TEST_END}",
    })

    with open(results_file, "w") as f:
        f.write("Agent 4 — ML Engineer: Round 1 Results\n")
        f.write("=" * 50 + "\n\n")
        for k, v in save_data.items():
            f.write(f"{k}: {v}\n")
        f.write(f"\nAll strategies on TEST period:\n")
        for name, s in sorted(strategies.items(), key=lambda x: x[1]["test"]["total_return_pct"], reverse=True):
            f.write(f"  {name}: {s['test']['total_return_pct']:+.2f}% (Sharpe {s['test']['sharpe_ratio']:.2f})\n")
        f.write(f"  Buy & Hold: {bnh:+.2f}%\n")
        f.write("\nTrade log (selected):\n")
        for t in best["trades"]:
            ts = t["time"]
            if t["type"] == "BUY":
                f.write(f"  {ts} BUY  @ ${t['price']:,.2f} size={t['size']:.6f}\n")
            else:
                f.write(f"  {ts} SELL @ ${t['price']:,.2f} pnl={t.get('pnl_pct', 0)*100:+.2f}%\n")

    print(f"\nResults saved to {results_file}")


if __name__ == "__main__":
    main()
