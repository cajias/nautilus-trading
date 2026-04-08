"""
Agent 3 - Macro Strategist: Multi-Asset Regime Rotation v5
============================================================
Rotates capital across top crypto assets based on relative momentum
and regime detection. Long-only on the strongest asset in each regime.

Universe: BTC, ETH, BNB, SOL, XRP, LINK
Timeframe: Daily bars

Strategy:
1. Compute 20-day momentum (rate of change) for each asset
2. Regime filter: only go long assets above their 20-day SMA
3. Rank by momentum, allocate to top 1-2 assets
4. If no asset is in uptrend, go to cash (USDT)
5. Rebalance weekly (every 7 days)
6. Vol-target: scale total allocation to target 30% annualized vol

Edge: Rotates to wherever momentum is strongest, avoids losers.
"""

import os
import sys
from typing import Optional

import numpy as np
import pandas as pd
import requests


def fetch_klines(symbol: str, interval: str, start: str, end: str) -> pd.DataFrame:
    url = "https://api.binance.com/api/v3/klines"
    start_ts = int(pd.Timestamp(start).timestamp() * 1000)
    end_ts = int(pd.Timestamp(end).timestamp() * 1000)
    all_data = []
    current = start_ts
    while current < end_ts:
        params = {"symbol": symbol, "interval": interval, "startTime": current, "endTime": end_ts, "limit": 1000}
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if not data:
            break
        all_data.extend(data)
        current = data[-1][6] + 1
    if not all_data:
        return pd.DataFrame()
    df = pd.DataFrame(all_data, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades", "taker_buy_base", "taker_buy_quote", "ignore",
    ])
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)
    df["timestamp"] = pd.to_datetime(df["open_time"], unit="ms")
    df.set_index("timestamp", inplace=True)
    df.sort_index(inplace=True)
    return df


def run_backtest(
    symbols: list[str],
    start_data: str = "2025-04-01",
    end_data: str = "2026-01-02",
    bt_start: str = "2025-07-01",
    bt_end: str = "2025-12-31",
    initial_capital: float = 1000.0,
    rebal_days: int = 5,
    top_n: int = 2,
    mom_period: int = 20,
    sma_period: int = 20,
    target_vol: float = 0.30,
) -> dict:
    # Download data for all symbols
    print(f"Downloading data for {len(symbols)} symbols...")
    closes = {}
    for sym in symbols:
        df = fetch_klines(sym, "1d", start_data, end_data)
        if df.empty:
            print(f"  {sym}: NO DATA - skipping")
            continue
        closes[sym] = df["close"]
        print(f"  {sym}: {len(df)} bars ({df.index[0].date()} to {df.index[-1].date()})")

    # Align all close prices
    price_df = pd.DataFrame(closes)
    price_df = price_df.dropna()
    print(f"\nAligned {len(price_df)} daily bars across {len(price_df.columns)} assets")

    # Returns
    returns_df = price_df.pct_change()

    # Momentum (20-day rate of change)
    momentum_df = price_df.pct_change(mom_period)

    # SMA filter
    sma_df = price_df.rolling(sma_period).mean()

    # Portfolio vol (equal-weight of all assets for scaling)
    port_vol = returns_df.mean(axis=1).rolling(30).std() * np.sqrt(365)

    # Backtest period
    bt_mask = (price_df.index >= bt_start) & (price_df.index <= bt_end)
    bt_dates = price_df.index[bt_mask]

    if len(bt_dates) == 0:
        print("ERROR: No data in backtest period")
        return {}

    # Run simulation
    equity = initial_capital
    equity_curve = []
    holdings = {}  # symbol -> fraction of equity
    last_rebal = None
    total_fees = 0.0
    fee_rate = 0.001
    trade_log = []

    for date in bt_dates:
        # Mark to market
        if holdings:
            daily_ret = sum(
                frac * returns_df.loc[date, sym]
                for sym, frac in holdings.items()
                if not pd.isna(returns_df.loc[date, sym])
            )
        else:
            daily_ret = 0.0

        equity *= (1 + daily_ret)
        equity_curve.append({"date": date, "equity": equity})

        # Rebalance check
        if last_rebal is None or (date - last_rebal).days >= rebal_days:
            # Rank assets by momentum, filtered by SMA
            candidates = {}
            for sym in price_df.columns:
                mom = momentum_df.loc[date, sym] if date in momentum_df.index else np.nan
                price = price_df.loc[date, sym]
                sma_val = sma_df.loc[date, sym] if date in sma_df.index else np.nan

                if pd.isna(mom) or pd.isna(sma_val):
                    continue

                # Only consider assets above their SMA (uptrend)
                if price > sma_val and mom > 0:
                    candidates[sym] = mom

            # Sort by momentum descending, pick top N
            sorted_cands = sorted(candidates.items(), key=lambda x: x[1], reverse=True)
            top = sorted_cands[:top_n]

            # Vol scaling
            pv = port_vol.loc[date] if date in port_vol.index and not pd.isna(port_vol.loc[date]) else 0.5
            vol_scalar = min(1.5, target_vol / pv) if pv > 0 else 1.0

            # New allocations
            new_holdings = {}
            if top:
                # Equal weight among top picks, vol-scaled
                weight = vol_scalar / len(top)
                weight = min(weight, 0.95 / len(top))  # cap total at 95%
                for sym, mom in top:
                    new_holdings[sym] = weight
            # else: all cash (new_holdings stays empty)

            # Compute turnover for fees
            all_syms = set(list(holdings.keys()) + list(new_holdings.keys()))
            turnover = sum(
                abs(new_holdings.get(s, 0) - holdings.get(s, 0))
                for s in all_syms
            )
            fee = turnover * fee_rate
            equity *= (1 - fee)
            total_fees += fee * equity

            if new_holdings != holdings:
                trade_log.append({
                    "date": date,
                    "old": dict(holdings),
                    "new": dict(new_holdings),
                    "equity": equity,
                })

            holdings = new_holdings
            last_rebal = date

    # Metrics
    eq_df = pd.DataFrame(equity_curve)
    eq_df["returns"] = eq_df["equity"].pct_change()

    final_eq = equity
    total_ret = (final_eq / initial_capital - 1) * 100

    rolling_max = eq_df["equity"].cummax()
    drawdown = (rolling_max - eq_df["equity"]) / rolling_max
    max_dd = drawdown.max() * 100

    daily_rets = eq_df["returns"].dropna()
    sharpe = (daily_rets.mean() / daily_rets.std() * np.sqrt(365)) if daily_rets.std() > 0 else 0

    # Buy and hold BTC comparison
    btc_start = price_df.loc[bt_dates[0], "BTCUSDT"]
    btc_end = price_df.loc[bt_dates[-1], "BTCUSDT"]
    bnh_ret = (btc_end / btc_start - 1) * 100

    # Win rate (positive rebalance periods)
    period_returns = []
    for i in range(1, len(trade_log)):
        start_eq = trade_log[i-1]["equity"]
        end_eq = trade_log[i]["equity"]
        period_returns.append((end_eq / start_eq - 1) * 100)
    if period_returns:
        win_rate = sum(1 for r in period_returns if r > 0) / len(period_returns) * 100
    else:
        win_rate = 0

    return {
        "total_return_pct": round(total_ret, 2),
        "sharpe_ratio": round(sharpe, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "win_rate_pct": round(win_rate, 1),
        "num_trades": len(trade_log),
        "final_equity": round(final_eq, 2),
        "buy_hold_btc_pct": round(bnh_ret, 2),
        "total_fees": round(total_fees, 2),
        "trade_log": trade_log,
    }


def format_report(m: dict) -> str:
    return "\n".join([
        "=" * 60,
        "AGENT 3 - MACRO STRATEGIST: MULTI-ASSET REGIME ROTATION",
        "=" * 60,
        f"Universe:         BTC, ETH, BNB, SOL, XRP, LINK",
        f"Timeframe:        Daily, rebalance every 5 days",
        f"Period:           2025-07-01 to 2025-12-31",
        f"Initial Capital:  $1,000.00",
        f"Final Equity:     ${m['final_equity']:,.2f}",
        "-" * 60,
        f"Total Return:     {m['total_return_pct']:+.2f}%",
        f"BTC Buy & Hold:   {m['buy_hold_btc_pct']:+.2f}%",
        f"Sharpe Ratio:     {m['sharpe_ratio']:.2f}",
        f"Max Drawdown:     {m['max_drawdown_pct']:.2f}%",
        f"Win Rate:         {m['win_rate_pct']:.1f}%",
        f"Rebalance Events: {m['num_trades']}",
        f"Total Fees:       ${m['total_fees']:.2f}",
        "=" * 60,
    ])


def main():
    symbols = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "LINKUSDT"]

    print("=" * 60)
    print("Agent 3 - Macro Strategist: Multi-Asset Regime Rotation")
    print("=" * 60)

    metrics = run_backtest(symbols)
    if not metrics:
        return 1

    trade_log = metrics.pop("trade_log", [])
    report = format_report(metrics)
    print(report)

    # Show allocation changes
    print("\nAllocation changes (sample):")
    for t in trade_log[:10]:
        old = ", ".join(f"{k}:{v:.0%}" for k, v in t["old"].items()) or "CASH"
        new = ", ".join(f"{k}:{v:.0%}" for k, v in t["new"].items()) or "CASH"
        print(f"  {t['date'].date()} eq=${t['equity']:.2f}: {old} -> {new}")
    if len(trade_log) > 10:
        print(f"  ... and {len(trade_log) - 10} more")

    # Save results
    results_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "results")
    os.makedirs(results_dir, exist_ok=True)
    path = os.path.join(results_dir, "round1_results.txt")
    with open(path, "w") as f:
        f.write(report)
        f.write("\n\nAllocation Changes:\n" + "-" * 80 + "\n")
        for t in trade_log:
            old = ", ".join(f"{k}:{v:.0%}" for k, v in t["old"].items()) or "CASH"
            new = ", ".join(f"{k}:{v:.0%}" for k, v in t["new"].items()) or "CASH"
            f.write(f"  {t['date'].date()} eq=${t['equity']:.2f}: {old} -> {new}\n")
    print(f"\nResults saved to {path}")

    if metrics["total_return_pct"] <= 0:
        print("\nWARNING: NOT profitable - needs iteration.")
        return 1
    print("\nStrategy is PROFITABLE.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
