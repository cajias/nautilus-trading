"""
Agent 1 - Quantitative Trader | Round 1
Strategy: Multi-Factor Momentum + Mean Reversion on BTC/ETH/SOL

Approach:
- Dual-timeframe signals: fast momentum (5d ROC) and slow mean reversion (20d Bollinger Z-score)
- Combine into a composite signal with regime detection (volatility-based)
- High vol regime -> momentum weight up; Low vol regime -> mean reversion weight up
- Equal-weight allocation across 3 major coins, rebalanced daily
- Risk management: per-asset stop-loss at 5% drawdown from entry, max 33% per position
"""

import io
import time
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd
import requests


# ── Configuration ──────────────────────────────────────────────────────────
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
INTERVAL = "1d"
STARTING_CAPITAL = 1000.0

TRAIN_START = "2024-01-01"
TRAIN_END = "2024-06-30"
TEST_START = "2024-07-01"
TEST_END = "2024-09-30"

# Strategy parameters (tuned on TRAIN, validated on TEST)
MOMENTUM_WINDOW = 7       # days for rate-of-change momentum
MEAN_REV_WINDOW = 20      # days for Bollinger Band z-score
VOL_WINDOW = 14           # days for realized volatility (regime detection)
VOL_THRESHOLD = 0.6       # percentile: above = high vol (momentum), below = mean reversion
SIGNAL_THRESHOLD = 0.2    # minimum absolute signal to trade
STOP_LOSS_PCT = 0.03      # 3% stop-loss per position
TAKE_PROFIT_PCT = 0.06    # 6% take-profit per position
MAX_POSITION_WEIGHT = 0.40  # max 40% in one asset
TRANSACTION_COST_BPS = 10   # 10 bps round-trip
TREND_FILTER_WINDOW = 30  # SMA for trend filter
MAX_EXPOSURE = 0.70       # max total portfolio exposure (cash buffer)


# ── Data Download ──────────────────────────────────────────────────────────
def download_klines(symbol: str, interval: str, start: str, end: str) -> pd.DataFrame:
    """Download klines from Binance public API."""
    url = "https://api.binance.com/api/v3/klines"
    all_data: list[list[Any]] = []

    start_ms = int(datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)
    end_ms = int(datetime.strptime(end, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)

    current = start_ms
    while current < end_ms:
        params = {
            "symbol": symbol,
            "interval": interval,
            "startTime": current,
            "endTime": end_ms,
            "limit": 1000,
        }
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if not data:
            break
        all_data.extend(data)
        current = data[-1][6] + 1  # close time + 1ms
        time.sleep(0.1)  # rate limit courtesy

    df = pd.DataFrame(all_data, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades", "taker_buy_base",
        "taker_buy_quote", "ignore",
    ])
    df["date"] = pd.to_datetime(df["open_time"], unit="ms")
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)
    df = df.set_index("date")[["open", "high", "low", "close", "volume"]]
    return df


def get_all_data() -> dict[str, pd.DataFrame]:
    """Download data for all symbols covering both train and test periods.
    Include lookback buffer before train start."""
    # Need extra days before train start for indicator warmup
    buffer_start = (
        datetime.strptime(TRAIN_START, "%Y-%m-%d") - pd.Timedelta(days=MEAN_REV_WINDOW + 10)
    ).strftime("%Y-%m-%d")

    data = {}
    for sym in SYMBOLS:
        print(f"  Downloading {sym}...")
        df = download_klines(sym, INTERVAL, buffer_start, TEST_END)
        data[sym] = df
        print(f"    {len(df)} bars from {df.index[0].date()} to {df.index[-1].date()}")
    return data


# ── Signal Generation ──────────────────────────────────────────────────────
def compute_signals(prices: pd.DataFrame) -> pd.DataFrame:
    """Compute momentum, mean-reversion, and composite signals with trend filter."""
    close = prices["close"]

    # Momentum: normalized rate of change
    roc = close.pct_change(MOMENTUM_WINDOW)
    mom_signal = roc / roc.rolling(MEAN_REV_WINDOW).std()  # normalize by recent vol

    # Mean reversion: Bollinger Band z-score (inverted - buy when low, sell when high)
    bb_mid = close.rolling(MEAN_REV_WINDOW).mean()
    bb_std = close.rolling(MEAN_REV_WINDOW).std()
    z_score = (close - bb_mid) / bb_std
    mr_signal = -z_score  # inverted: buy when price is below mean

    # Regime detection: realized volatility percentile
    ret = close.pct_change()
    realized_vol = ret.rolling(VOL_WINDOW).std() * np.sqrt(365)
    vol_pctile = realized_vol.rolling(60, min_periods=20).rank(pct=True)

    # Trend filter: only go long when price > SMA(30), scale down when below
    sma_trend = close.rolling(TREND_FILTER_WINDOW).mean()
    trend_score = (close / sma_trend - 1).clip(-0.1, 0.1) / 0.1  # [-1, 1]
    # Convert to [0, 1] filter: 1 = fully above trend, 0 = well below
    trend_filter = (trend_score + 1) / 2  # maps [-1,1] -> [0,1]

    # Composite signal: weight by regime
    # High vol -> more momentum; Low vol -> more mean reversion
    mom_weight = vol_pctile.clip(0.3, 0.7)
    mr_weight = 1 - mom_weight

    composite = mom_weight * mom_signal + mr_weight * mr_signal

    # Apply trend filter: reduce signal when below trend
    composite = composite * trend_filter

    signals = pd.DataFrame({
        "close": close,
        "return": ret,
        "momentum": mom_signal,
        "mean_rev": mr_signal,
        "vol_pctile": vol_pctile,
        "trend_filter": trend_filter,
        "composite": composite,
    })
    return signals


# ── Backtester ─────────────────────────────────────────────────────────────
def backtest_period(
    all_signals: dict[str, pd.DataFrame],
    start: str,
    end: str,
) -> dict[str, Any]:
    """Run backtest on a specific period."""
    start_dt = pd.Timestamp(start)
    end_dt = pd.Timestamp(end)

    # Filter to period
    period_signals = {}
    for sym, sig in all_signals.items():
        mask = (sig.index >= start_dt) & (sig.index <= end_dt)
        period_signals[sym] = sig[mask].copy()

    # Get common dates
    common_dates = period_signals[SYMBOLS[0]].index
    for sym in SYMBOLS[1:]:
        common_dates = common_dates.intersection(period_signals[sym].index)
    common_dates = common_dates.sort_values()

    if len(common_dates) == 0:
        return {"total_return": 0, "sharpe": 0, "max_dd": 0, "win_rate": 0, "trades": 0}

    # Portfolio simulation
    cash = STARTING_CAPITAL
    positions: dict[str, float] = {sym: 0.0 for sym in SYMBOLS}  # units held
    entry_prices: dict[str, float] = {sym: 0.0 for sym in SYMBOLS}
    portfolio_values: list[float] = []
    trade_returns: list[float] = []
    total_trades = 0

    for date in common_dates:
        # Current portfolio value
        port_value = cash
        for sym in SYMBOLS:
            price = period_signals[sym].loc[date, "close"]
            port_value += positions[sym] * price

        # Check stop-losses and take-profits
        for sym in SYMBOLS:
            if positions[sym] > 0:
                price = period_signals[sym].loc[date, "close"]
                pnl_pct = (price - entry_prices[sym]) / entry_prices[sym]
                if pnl_pct < -STOP_LOSS_PCT or pnl_pct > TAKE_PROFIT_PCT:
                    # Stop-loss or take-profit hit: close position
                    proceeds = positions[sym] * price
                    cost = proceeds * (TRANSACTION_COST_BPS / 10000)
                    cash += proceeds - cost
                    trade_returns.append(pnl_pct)
                    positions[sym] = 0.0
                    entry_prices[sym] = 0.0
                    total_trades += 1

        # Recalculate after stop-losses
        port_value = cash
        for sym in SYMBOLS:
            price = period_signals[sym].loc[date, "close"]
            port_value += positions[sym] * price

        # Generate target weights from signals
        signals_today = {}
        for sym in SYMBOLS:
            sig_val = period_signals[sym].loc[date, "composite"]
            if pd.isna(sig_val):
                signals_today[sym] = 0.0
            else:
                signals_today[sym] = sig_val

        # Convert signals to target weights (long-only, proportional to signal)
        target_weights: dict[str, float] = {}
        positive_signals = {s: max(0, v) for s, v in signals_today.items()}
        total_pos_signal = sum(positive_signals.values())

        if total_pos_signal > 0:
            for sym in SYMBOLS:
                w = positive_signals[sym] / total_pos_signal
                # Apply threshold: only trade if signal is meaningful
                if signals_today[sym] < SIGNAL_THRESHOLD:
                    w = 0.0
                w = min(w, MAX_POSITION_WEIGHT)
                target_weights[sym] = w
        else:
            target_weights = {sym: 0.0 for sym in SYMBOLS}

        # Normalize weights and cap total exposure
        total_w = sum(target_weights.values())
        if total_w > MAX_EXPOSURE:
            scale = MAX_EXPOSURE / total_w
            target_weights = {s: v * scale for s, v in target_weights.items()}

        # Rebalance
        for sym in SYMBOLS:
            price = period_signals[sym].loc[date, "close"]
            target_value = port_value * target_weights[sym]
            current_value = positions[sym] * price
            delta_value = target_value - current_value

            # Only trade if change is > 1% of portfolio (avoid churn)
            if abs(delta_value) > port_value * 0.01:
                if delta_value > 0:
                    # Buy
                    cost = delta_value * (1 + TRANSACTION_COST_BPS / 10000)
                    if cost <= cash:
                        units = delta_value / price
                        if positions[sym] == 0:
                            entry_prices[sym] = price
                        else:
                            # Average entry
                            old_val = positions[sym] * entry_prices[sym]
                            entry_prices[sym] = (old_val + delta_value) / (positions[sym] + units)
                        positions[sym] += units
                        cash -= cost
                        total_trades += 1
                elif delta_value < 0:
                    # Sell
                    units_to_sell = min(abs(delta_value) / price, positions[sym])
                    proceeds = units_to_sell * price
                    cost = proceeds * (TRANSACTION_COST_BPS / 10000)
                    if positions[sym] > 0:
                        ret_pct = (price - entry_prices[sym]) / entry_prices[sym]
                        trade_returns.append(ret_pct)
                    positions[sym] -= units_to_sell
                    cash += proceeds - cost
                    total_trades += 1
                    if positions[sym] < 1e-8:
                        positions[sym] = 0.0
                        entry_prices[sym] = 0.0

        # Record portfolio value at end of day
        final_value = cash
        for sym in SYMBOLS:
            price = period_signals[sym].loc[date, "close"]
            final_value += positions[sym] * price
        portfolio_values.append(final_value)

    # Calculate metrics
    pv = pd.Series(portfolio_values, index=common_dates)
    daily_returns = pv.pct_change().dropna()

    total_return = (pv.iloc[-1] / pv.iloc[0] - 1) * 100
    sharpe = (daily_returns.mean() / daily_returns.std() * np.sqrt(365)) if daily_returns.std() > 0 else 0
    max_dd = ((pv / pv.cummax() - 1).min()) * 100
    win_rate = (sum(1 for r in trade_returns if r > 0) / len(trade_returns) * 100) if trade_returns else 0

    return {
        "total_return": total_return,
        "sharpe": sharpe,
        "max_dd": max_dd,
        "win_rate": win_rate,
        "trades": total_trades,
        "final_value": pv.iloc[-1],
        "portfolio_series": pv,
    }


# ── Main ───────────────────────────────────────────────────────────────────
def main() -> None:
    print("=" * 60)
    print("Agent 1: Quantitative Trader - Round 1")
    print("Strategy: Multi-Factor Momentum + Mean Reversion")
    print(f"Symbols: {', '.join(SYMBOLS)}")
    print(f"Capital: ${STARTING_CAPITAL:,.0f}")
    print("=" * 60)

    print("\n[1/3] Downloading data...")
    data = get_all_data()

    print("\n[2/3] Computing signals...")
    all_signals = {}
    for sym in SYMBOLS:
        all_signals[sym] = compute_signals(data[sym])

    print("\n[3/3] Running backtests...")

    print(f"\n── TRAIN Period: {TRAIN_START} to {TRAIN_END} ──")
    train_results = backtest_period(all_signals, TRAIN_START, TRAIN_END)
    print(f"  Return:   {train_results['total_return']:+.2f}%")
    print(f"  Sharpe:   {train_results['sharpe']:.2f}")
    print(f"  Max DD:   {train_results['max_dd']:.2f}%")
    print(f"  Win Rate: {train_results['win_rate']:.1f}%")
    print(f"  Trades:   {train_results['trades']}")
    print(f"  Final:    ${train_results['final_value']:,.2f}")

    print(f"\n── TEST Period: {TEST_START} to {TEST_END} ──")
    test_results = backtest_period(all_signals, TEST_START, TEST_END)
    print(f"  Return:   {test_results['total_return']:+.2f}%")
    print(f"  Sharpe:   {test_results['sharpe']:.2f}")
    print(f"  Max DD:   {test_results['max_dd']:.2f}%")
    print(f"  Win Rate: {test_results['win_rate']:.1f}%")
    print(f"  Trades:   {test_results['trades']}")
    print(f"  Final:    ${test_results['final_value']:,.2f}")

    # Save results
    results_text = f"""Strategy: Multi-Factor Momentum + Mean Reversion
Description: Dual-signal approach combining 7-day momentum ROC and 20-day Bollinger z-score mean reversion, with volatility-based regime switching and 30-day trend filter. Long-only, daily rebalance across BTC/ETH/SOL with 3% stop-losses, 6% take-profits, and 70% max exposure cap.
Pairs: BTCUSDT, ETHUSDT, SOLUSDT
TRAIN Return: {train_results['total_return']:+.2f}%  |  TEST Return: {test_results['total_return']:+.2f}%
TEST Sharpe: {test_results['sharpe']:.2f}  |  TEST Max DD: {test_results['max_dd']:.2f}%  |  TEST Win Rate: {test_results['win_rate']:.1f}%
TEST Trades: {test_results['trades']}
"""
    results_path = "/Users/rc/Projects/workspace/nautilus-trading/competition/agent-1-quant/round1/results.txt"
    with open(results_path, "w") as f:
        f.write(results_text)
    print(f"\nResults saved to {results_path}")


if __name__ == "__main__":
    main()
