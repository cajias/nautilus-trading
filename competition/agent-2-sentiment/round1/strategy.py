"""
Agent 2: Sentiment Trader — Round 1
Strategy: Fear/Greed Regime Trading

Uses price-derived sentiment indicators to identify fear/greed cycles:
- RSI extremes (oversold = fear, overbought = greed)
- Volume-price divergences
- Volatility regime detection (ATR-based)
- Behavioral mean-reversion: buy fear, sell greed

Pairs: BTCUSDT, ETHUSDT (1h bars)
TRAIN: Jan 1, 2024 – Jun 30, 2024
TEST:  Jul 1, 2024 – Sep 30, 2024
"""

import json
import urllib.request
import time
from datetime import datetime, timezone
from dataclasses import dataclass

import numpy as np
import pandas as pd


# ── Data Download ─────────────────────────────────────────────────────────

def download_binance_klines(symbol: str, interval: str, start: str, end: str) -> pd.DataFrame:
    """Download klines from Binance public API."""
    base_url = "https://api.binance.com/api/v3/klines"
    start_ms = int(datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)
    end_ms = int(datetime.strptime(end, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)

    all_data = []
    current_ms = start_ms

    while current_ms < end_ms:
        url = f"{base_url}?symbol={symbol}&interval={interval}&startTime={current_ms}&endTime={end_ms}&limit=1000"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read())

        if not data:
            break

        all_data.extend(data)
        current_ms = data[-1][0] + 1  # next ms after last candle
        time.sleep(0.1)  # rate limit

    df = pd.DataFrame(all_data, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades", "taker_buy_base",
        "taker_buy_quote", "ignore"
    ])

    for col in ["open", "high", "low", "close", "volume", "quote_volume", "taker_buy_base", "taker_buy_quote"]:
        df[col] = df[col].astype(float)

    df["timestamp"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df = df.set_index("timestamp")
    return df


# ── Indicators ────────────────────────────────────────────────────────────

def compute_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0).ewm(alpha=1 / period, min_periods=period).mean()
    loss = (-delta.where(delta < 0, 0.0)).ewm(alpha=1 / period, min_periods=period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high_low = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift(1)).abs()
    low_close = (df["low"] - df["close"].shift(1)).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, min_periods=period).mean()


def compute_obv(df: pd.DataFrame) -> pd.Series:
    """On-Balance Volume."""
    sign = np.sign(df["close"].diff())
    return (sign * df["volume"]).cumsum()


def compute_mfi(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Money Flow Index — volume-weighted RSI."""
    typical = (df["high"] + df["low"] + df["close"]) / 3
    mf = typical * df["volume"]
    delta = typical.diff()
    pos_mf = mf.where(delta > 0, 0.0).rolling(period).sum()
    neg_mf = mf.where(delta < 0, 0.0).rolling(period).sum()
    ratio = pos_mf / neg_mf.replace(0, np.nan)
    return 100 - (100 / (1 + ratio))


def compute_bb_width(series: pd.Series, period: int = 20) -> pd.Series:
    """Bollinger Band width as % of middle band."""
    mid = series.rolling(period).mean()
    std = series.rolling(period).std()
    return (2 * std / mid) * 100


# ── Sentiment Composite ──────────────────────────────────────────────────

def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """Build sentiment-derived features."""
    feat = pd.DataFrame(index=df.index)

    # RSI
    feat["rsi_14"] = compute_rsi(df["close"], 14)
    feat["rsi_7"] = compute_rsi(df["close"], 7)

    # MFI (volume-weighted RSI)
    feat["mfi_14"] = compute_mfi(df, 14)

    # Volatility regime
    feat["atr_14"] = compute_atr(df, 14)
    feat["atr_norm"] = feat["atr_14"] / df["close"]  # normalized ATR
    feat["bb_width"] = compute_bb_width(df["close"], 20)
    feat["vol_regime"] = feat["atr_norm"].rolling(48).rank(pct=True)  # percentile rank

    # Volume analysis
    feat["vol_sma"] = df["volume"].rolling(24).mean()
    feat["vol_ratio"] = df["volume"] / feat["vol_sma"]
    feat["vol_spike"] = (feat["vol_ratio"] > 2.0).astype(float)

    # Price-volume divergence
    feat["price_chg_12"] = df["close"].pct_change(12)
    obv = compute_obv(df)
    feat["obv_chg_12"] = obv.pct_change(12)
    # Divergence: price up but OBV down, or vice versa
    feat["pv_divergence"] = np.sign(feat["price_chg_12"]) != np.sign(feat["obv_chg_12"])

    # Taker buy ratio (proxy for buy/sell pressure)
    feat["taker_buy_ratio"] = df["taker_buy_base"] / df["volume"].replace(0, np.nan)

    # Returns
    feat["ret_1h"] = df["close"].pct_change(1)
    feat["ret_4h"] = df["close"].pct_change(4)
    feat["ret_24h"] = df["close"].pct_change(24)

    # Fear/Greed composite score (0-100)
    # Components: RSI, MFI, vol regime (inverted), taker buy ratio
    rsi_score = feat["rsi_14"].clip(0, 100)
    mfi_score = feat["mfi_14"].clip(0, 100)
    vol_score = (1 - feat["vol_regime"]) * 100  # low vol = greed
    buy_score = feat["taker_buy_ratio"].rolling(24).mean() * 100

    feat["fear_greed"] = (
        0.30 * rsi_score +
        0.25 * mfi_score +
        0.20 * vol_score +
        0.25 * buy_score.clip(0, 100)
    )

    feat["close"] = df["close"]
    return feat


# ── Strategy Logic ────────────────────────────────────────────────────────

@dataclass
class StrategyParams:
    """Tunable parameters."""
    fear_threshold: float = 30.0       # Buy when fear_greed < this
    greed_threshold: float = 70.0      # Sell when fear_greed > this
    rsi_oversold: float = 30.0         # RSI buy confirmation
    rsi_overbought: float = 70.0       # RSI sell confirmation
    vol_spike_boost: float = 1.5       # Scale signal on volume spikes
    atr_stop_mult: float = 2.0         # ATR multiplier for stop loss
    atr_tp_mult: float = 3.0           # ATR multiplier for take profit
    max_holding_hours: int = 72        # Max hold time
    position_size: float = 0.25        # Fraction of capital per trade
    cooldown_hours: int = 6            # Min hours between trades


def run_backtest(feat: pd.DataFrame, params: StrategyParams, capital: float = 1000.0) -> dict:
    """Run vectorized backtest with fear/greed regime signals."""
    feat = feat.dropna().copy()

    cash = capital
    position = 0.0  # units held
    entry_price = 0.0
    entry_idx = 0
    stop_loss = 0.0
    take_profit = 0.0
    last_trade_idx = -params.cooldown_hours

    trades = []
    equity_curve = []

    for i in range(len(feat)):
        row = feat.iloc[i]
        price = row["close"]
        current_equity = cash + position * price
        equity_curve.append({"timestamp": feat.index[i], "equity": current_equity})

        # Check exits first
        if position != 0:
            holding_hours = i - entry_idx
            exit_reason = None

            if position > 0:  # Long position
                if price <= stop_loss:
                    exit_reason = "stop_loss"
                elif price >= take_profit:
                    exit_reason = "take_profit"
                elif row["fear_greed"] > params.greed_threshold and row["rsi_14"] > params.rsi_overbought:
                    exit_reason = "greed_exit"
                elif holding_hours >= params.max_holding_hours:
                    exit_reason = "timeout"

            elif position < 0:  # Short position
                if price >= stop_loss:
                    exit_reason = "stop_loss"
                elif price <= take_profit:
                    exit_reason = "take_profit"
                elif row["fear_greed"] < params.fear_threshold and row["rsi_14"] < params.rsi_oversold:
                    exit_reason = "fear_exit"
                elif holding_hours >= params.max_holding_hours:
                    exit_reason = "timeout"

            if exit_reason:
                pnl = position * (price - entry_price)
                cash += position * price
                trades.append({
                    "entry_time": feat.index[entry_idx],
                    "exit_time": feat.index[i],
                    "side": "long" if position > 0 else "short",
                    "entry_price": entry_price,
                    "exit_price": price,
                    "pnl": pnl,
                    "reason": exit_reason,
                    "holding_hours": holding_hours,
                })
                position = 0.0
                last_trade_idx = i

        # Check entries (only if flat and past cooldown)
        if position == 0 and (i - last_trade_idx) >= params.cooldown_hours:
            atr = row["atr_14"]
            signal_strength = 1.0

            # Volume spike boosts signal
            if row["vol_spike"]:
                signal_strength *= params.vol_spike_boost

            # BUY FEAR: fear_greed low + RSI oversold + price-volume divergence bonus
            if row["fear_greed"] < params.fear_threshold and row["rsi_14"] < params.rsi_oversold + 10:
                size_usd = cash * params.position_size * min(signal_strength, 2.0)
                units = size_usd / price
                position = units
                entry_price = price
                entry_idx = i
                cash -= size_usd
                stop_loss = price - params.atr_stop_mult * atr
                take_profit = price + params.atr_tp_mult * atr

            # SELL GREED: fear_greed high + RSI overbought
            elif row["fear_greed"] > params.greed_threshold and row["rsi_14"] > params.rsi_overbought - 10:
                size_usd = cash * params.position_size * min(signal_strength, 2.0)
                units = size_usd / price
                position = -units
                entry_price = price
                entry_idx = i
                cash -= 0  # margin-free for simplicity; track PnL on close
                stop_loss = price + params.atr_stop_mult * atr
                take_profit = price - params.atr_tp_mult * atr

    # Close any open position at end
    if position != 0:
        price = feat.iloc[-1]["close"]
        pnl = position * (price - entry_price)
        cash += position * price
        trades.append({
            "entry_time": feat.index[entry_idx],
            "exit_time": feat.index[-1],
            "side": "long" if position > 0 else "short",
            "entry_price": entry_price,
            "exit_price": price,
            "pnl": pnl,
            "reason": "end_of_period",
            "holding_hours": len(feat) - entry_idx,
        })
        position = 0.0

    # Compute metrics
    final_equity = cash
    total_return = (final_equity - capital) / capital * 100

    eq_df = pd.DataFrame(equity_curve).set_index("timestamp")
    eq_df["returns"] = eq_df["equity"].pct_change()

    sharpe = 0.0
    if len(eq_df["returns"].dropna()) > 10:
        mean_ret = eq_df["returns"].mean()
        std_ret = eq_df["returns"].std()
        if std_ret > 0:
            # Annualized from hourly
            sharpe = (mean_ret / std_ret) * np.sqrt(24 * 365)

    # Max drawdown
    peak = eq_df["equity"].cummax()
    dd = (eq_df["equity"] - peak) / peak
    max_dd = dd.min() * 100

    # Win rate
    trade_df = pd.DataFrame(trades)
    win_rate = 0.0
    n_trades = len(trades)
    if n_trades > 0:
        win_rate = (trade_df["pnl"] > 0).mean() * 100

    return {
        "final_equity": final_equity,
        "total_return_pct": total_return,
        "sharpe": sharpe,
        "max_drawdown_pct": max_dd,
        "win_rate_pct": win_rate,
        "n_trades": n_trades,
        "trades": trades,
        "equity_curve": eq_df,
    }


# ── Parameter Optimization on TRAIN ──────────────────────────────────────

def optimize_params(train_feat: pd.DataFrame) -> StrategyParams:
    """Grid search over key parameters on training data."""
    best_sharpe = -999
    best_params = StrategyParams()

    fear_range = [25, 30, 35]
    greed_range = [65, 70, 75]
    rsi_os_range = [25, 30, 35]
    rsi_ob_range = [65, 70, 75]
    atr_stop_range = [1.5, 2.0, 2.5]
    atr_tp_range = [2.5, 3.0, 4.0]

    total = len(fear_range) * len(greed_range) * len(rsi_os_range) * len(rsi_ob_range) * len(atr_stop_range) * len(atr_tp_range)
    print(f"  Optimizing over {total} parameter combinations...")

    count = 0
    for fear in fear_range:
        for greed in greed_range:
            for rsi_os in rsi_os_range:
                for rsi_ob in rsi_ob_range:
                    for atr_s in atr_stop_range:
                        for atr_t in atr_tp_range:
                            if atr_t <= atr_s:
                                continue
                            p = StrategyParams(
                                fear_threshold=fear,
                                greed_threshold=greed,
                                rsi_oversold=rsi_os,
                                rsi_overbought=rsi_ob,
                                atr_stop_mult=atr_s,
                                atr_tp_mult=atr_t,
                            )
                            result = run_backtest(train_feat, p)
                            count += 1

                            # Optimize for Sharpe with minimum trade count
                            if result["n_trades"] >= 5 and result["sharpe"] > best_sharpe:
                                best_sharpe = result["sharpe"]
                                best_params = p

    print(f"  Evaluated {count} combos. Best train Sharpe: {best_sharpe:.2f}")
    return best_params


# ── Main ──────────────────────────────────────────────────────────────────

def main():
    print("=" * 70)
    print("AGENT 2: SENTIMENT TRADER — ROUND 1")
    print("Strategy: Fear/Greed Regime Trading")
    print("=" * 70)

    symbols = ["BTCUSDT", "ETHUSDT"]
    train_start, train_end = "2024-01-01", "2024-06-30"
    test_start, test_end = "2024-07-01", "2024-09-30"

    all_results = {}

    for symbol in symbols:
        print(f"\n{'─' * 50}")
        print(f"Processing {symbol}")
        print(f"{'─' * 50}")

        # Download data
        print(f"  Downloading TRAIN data ({train_start} to {train_end})...")
        train_df = download_binance_klines(symbol, "1h", train_start, train_end)
        print(f"  Got {len(train_df)} candles")

        print(f"  Downloading TEST data ({test_start} to {test_end})...")
        test_df = download_binance_klines(symbol, "1h", test_start, test_end)
        print(f"  Got {len(test_df)} candles")

        # Build features
        print("  Building sentiment features...")
        train_feat = build_features(train_df)
        test_feat = build_features(test_df)

        # Optimize on TRAIN
        print("  Optimizing parameters on TRAIN...")
        best_params = optimize_params(train_feat)
        print(f"  Best params: fear={best_params.fear_threshold}, greed={best_params.greed_threshold}, "
              f"rsi_os={best_params.rsi_oversold}, rsi_ob={best_params.rsi_overbought}, "
              f"atr_stop={best_params.atr_stop_mult}, atr_tp={best_params.atr_tp_mult}")

        # Run on TRAIN
        train_result = run_backtest(train_feat, best_params)
        print(f"\n  TRAIN Results:")
        print(f"    Return: {train_result['total_return_pct']:.2f}%")
        print(f"    Sharpe: {train_result['sharpe']:.2f}")
        print(f"    MaxDD:  {train_result['max_drawdown_pct']:.2f}%")
        print(f"    WinRate:{train_result['win_rate_pct']:.1f}%")
        print(f"    Trades: {train_result['n_trades']}")

        # Run on TEST
        test_result = run_backtest(test_feat, best_params)
        print(f"\n  TEST Results:")
        print(f"    Return: {test_result['total_return_pct']:.2f}%")
        print(f"    Sharpe: {test_result['sharpe']:.2f}")
        print(f"    MaxDD:  {test_result['max_drawdown_pct']:.2f}%")
        print(f"    WinRate:{test_result['win_rate_pct']:.1f}%")
        print(f"    Trades: {test_result['n_trades']}")

        all_results[symbol] = {
            "params": best_params,
            "train": train_result,
            "test": test_result,
        }

    # Combined portfolio results
    print(f"\n{'=' * 70}")
    print("COMBINED PORTFOLIO (equal weight)")
    print(f"{'=' * 70}")

    # Each symbol gets $500
    per_symbol_capital = 500.0
    total_final = 0
    total_trades = 0

    for symbol in symbols:
        r = all_results[symbol]
        symbol_final = per_symbol_capital * (1 + r["test"]["total_return_pct"] / 100)
        total_final += symbol_final
        total_trades += r["test"]["n_trades"]
        print(f"  {symbol}: ${symbol_final:.2f} (return: {r['test']['total_return_pct']:.2f}%)")

    portfolio_return = (total_final - 1000) / 1000 * 100
    print(f"\n  Portfolio final: ${total_final:.2f}")
    print(f"  Portfolio return: {portfolio_return:.2f}%")
    print(f"  Total trades: {total_trades}")

    # Save results
    results_path = "/Users/rc/Projects/workspace/nautilus-trading/competition/agent-2-sentiment/round1/results.txt"
    with open(results_path, "w") as f:
        f.write("AGENT 2: SENTIMENT TRADER — ROUND 1 RESULTS\n")
        f.write("=" * 50 + "\n\n")
        f.write("Strategy: Fear/Greed Regime Trading\n")
        f.write("Description: Behavioral mean-reversion using price-derived sentiment\n")
        f.write("  indicators (RSI, MFI, ATR regime, volume spikes, OBV divergence)\n")
        f.write("  to identify fear/greed cycles. Buys fear, sells greed.\n\n")

        for symbol in symbols:
            r = all_results[symbol]
            p = r["params"]
            f.write(f"{'─' * 50}\n")
            f.write(f"{symbol}\n")
            f.write(f"{'─' * 50}\n")
            f.write(f"Parameters:\n")
            f.write(f"  fear_threshold: {p.fear_threshold}\n")
            f.write(f"  greed_threshold: {p.greed_threshold}\n")
            f.write(f"  rsi_oversold: {p.rsi_oversold}\n")
            f.write(f"  rsi_overbought: {p.rsi_overbought}\n")
            f.write(f"  atr_stop_mult: {p.atr_stop_mult}\n")
            f.write(f"  atr_tp_mult: {p.atr_tp_mult}\n\n")

            f.write(f"TRAIN (Jan-Jun 2024):\n")
            f.write(f"  Return: {r['train']['total_return_pct']:.2f}%\n")
            f.write(f"  Sharpe: {r['train']['sharpe']:.2f}\n")
            f.write(f"  MaxDD:  {r['train']['max_drawdown_pct']:.2f}%\n")
            f.write(f"  WinRate:{r['train']['win_rate_pct']:.1f}%\n")
            f.write(f"  Trades: {r['train']['n_trades']}\n\n")

            f.write(f"TEST (Jul-Sep 2024):\n")
            f.write(f"  Return: {r['test']['total_return_pct']:.2f}%\n")
            f.write(f"  Sharpe: {r['test']['sharpe']:.2f}\n")
            f.write(f"  MaxDD:  {r['test']['max_drawdown_pct']:.2f}%\n")
            f.write(f"  WinRate:{r['test']['win_rate_pct']:.1f}%\n")
            f.write(f"  Trades: {r['test']['n_trades']}\n\n")

        f.write(f"{'=' * 50}\n")
        f.write(f"COMBINED PORTFOLIO ($1000 starting, equal weight)\n")
        f.write(f"{'=' * 50}\n")
        f.write(f"  Final equity: ${total_final:.2f}\n")
        f.write(f"  Return: {portfolio_return:.2f}%\n")
        f.write(f"  Total trades: {total_trades}\n")

    print(f"\nResults saved to {results_path}")


if __name__ == "__main__":
    main()
