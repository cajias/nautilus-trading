"""
Agent 1 - Quantitative Trader: Adaptive RSI Mean-Reversion + Trend Filter
==========================================================================
Trades BTC/USDT and ETH/USDT on 4-hour bars.

Core Idea:
  Mean-reversion using RSI oversold/overbought levels, confirmed by
  Bollinger Band position. A 100-bar EMA trend filter ensures we only
  take long mean-reversion trades in uptrends and short in downtrends
  (counter-trend entries with trend-aligned bias).

  Multi-asset diversification across BTC and ETH to smooth equity curve.

Entry (LONG):
  - RSI(14) < 30 (oversold)
  - Price below lower Bollinger Band (20, 2.0)
  - Close > EMA(100) (still in uptrend -- buying the dip)
  OR
  - RSI(14) < 25 regardless of trend (extreme oversold)

Entry (SHORT):
  - RSI(14) > 70 (overbought)
  - Price above upper Bollinger Band
  - Close < EMA(100) (downtrend -- selling the rip)
  OR
  - RSI(14) > 75 regardless of trend (extreme overbought)

Exit:
  - RSI returns to 50 (mean)
  - OR price returns to BB midline
  - OR stop loss at 2.5x ATR
  - Whichever comes first

Risk:
  - 1.5% of equity per trade per asset
  - Max 1 position per asset
"""

from pathlib import Path

import numpy as np
import pandas as pd
import requests


# ---------------------------------------------------------------------------
# DATA
# ---------------------------------------------------------------------------

def download_binance_klines(
    symbol: str = "BTCUSDT",
    interval: str = "4h",
    start: str = "2025-05-01",
    end: str = "2026-01-02",
) -> pd.DataFrame:
    """Download klines from Binance public API."""
    url = "https://api.binance.com/api/v3/klines"
    start_ms = int(pd.Timestamp(start).timestamp() * 1000)
    end_ms = int(pd.Timestamp(end).timestamp() * 1000)

    all_data = []
    current = start_ms

    print(f"  Downloading {symbol} {interval} from {start} to {end}...")
    while current < end_ms:
        params = {
            "symbol": symbol, "interval": interval,
            "startTime": current, "endTime": end_ms, "limit": 1000,
        }
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if not data:
            break
        all_data.extend(data)
        current = data[-1][6] + 1

    cols = [
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades", "taker_buy_base",
        "taker_buy_quote", "ignore",
    ]
    df = pd.DataFrame(all_data, columns=cols)
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = df[c].astype(float)
    df["timestamp"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df = df.set_index("timestamp")[["open", "high", "low", "close", "volume"]]
    return df[~df.index.duplicated(keep="first")].sort_index()


# ---------------------------------------------------------------------------
# INDICATORS
# ---------------------------------------------------------------------------

def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()

    # RSI (14)
    delta = d["close"].diff()
    gain = delta.where(delta > 0, 0.0).ewm(alpha=1/14, adjust=False).mean()
    loss = (-delta.where(delta < 0, 0.0)).ewm(alpha=1/14, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    d["rsi"] = 100 - (100 / (1 + rs))

    # Bollinger Bands (20, 2.0)
    d["bb_mid"] = d["close"].rolling(20).mean()
    d["bb_std"] = d["close"].rolling(20).std()
    d["bb_upper"] = d["bb_mid"] + 2.0 * d["bb_std"]
    d["bb_lower"] = d["bb_mid"] - 2.0 * d["bb_std"]

    # Trend filter: EMA 100
    d["ema_100"] = d["close"].ewm(span=100, adjust=False).mean()

    # ATR (20)
    tr = pd.concat([
        d["high"] - d["low"],
        (d["high"] - d["close"].shift(1)).abs(),
        (d["low"] - d["close"].shift(1)).abs(),
    ], axis=1).max(axis=1)
    d["atr"] = tr.rolling(20).mean()

    # Volume filter
    d["vol_sma"] = d["volume"].rolling(20).mean()

    return d


# ---------------------------------------------------------------------------
# BACKTEST (per-asset)
# ---------------------------------------------------------------------------

COMMISSION = 0.001  # 0.1% per side


def backtest_asset(
    df: pd.DataFrame,
    symbol: str,
    start_date: str = "2025-07-01",
    end_date: str = "2025-12-31",
    capital_allocation: float = 500.0,
    risk_per_trade: float = 0.015,
    atr_stop_mult: float = 2.5,
    rsi_oversold: float = 30.0,
    rsi_overbought: float = 70.0,
    rsi_extreme_oversold: float = 25.0,
    rsi_extreme_overbought: float = 75.0,
    rsi_exit_target: float = 50.0,
) -> dict:
    """Run mean-reversion backtest on a single asset."""
    d = compute_indicators(df)
    bt = d.loc[start_date:end_date].copy()
    if bt.empty:
        raise ValueError(f"No data for {symbol} in {start_date} to {end_date}")

    equity = capital_allocation
    peak_equity = capital_allocation
    max_dd = 0.0
    position = None  # dict with side, entry, size, stop, entry_time
    trades = []
    equity_curve = []

    for ts, row in bt.iterrows():
        if pd.isna(row["atr"]) or pd.isna(row["rsi"]) or row["atr"] == 0:
            equity_curve.append(equity)
            continue

        # --- Manage position ---
        if position is not None:
            closed = False
            side = position["side"]

            if side == "long":
                # Stop loss
                if row["low"] <= position["stop"]:
                    exit_p = position["stop"]
                    pnl = (exit_p - position["entry"]) * position["size"]
                    pnl -= exit_p * position["size"] * COMMISSION
                    equity += pnl
                    trades.append({**position, "exit_time": ts, "exit": exit_p, "pnl": pnl, "reason": "stop"})
                    position = None
                    closed = True
                # Take profit: RSI back to mean OR price at BB mid
                elif row["rsi"] >= rsi_exit_target or row["close"] >= row["bb_mid"]:
                    exit_p = row["close"]
                    pnl = (exit_p - position["entry"]) * position["size"]
                    pnl -= exit_p * position["size"] * COMMISSION
                    equity += pnl
                    trades.append({**position, "exit_time": ts, "exit": exit_p, "pnl": pnl, "reason": "target"})
                    position = None
                    closed = True

            elif side == "short":
                if row["high"] >= position["stop"]:
                    exit_p = position["stop"]
                    pnl = (position["entry"] - exit_p) * position["size"]
                    pnl -= exit_p * position["size"] * COMMISSION
                    equity += pnl
                    trades.append({**position, "exit_time": ts, "exit": exit_p, "pnl": pnl, "reason": "stop"})
                    position = None
                    closed = True
                elif row["rsi"] <= rsi_exit_target or row["close"] <= row["bb_mid"]:
                    exit_p = row["close"]
                    pnl = (position["entry"] - exit_p) * position["size"]
                    pnl -= exit_p * position["size"] * COMMISSION
                    equity += pnl
                    trades.append({**position, "exit_time": ts, "exit": exit_p, "pnl": pnl, "reason": "target"})
                    position = None
                    closed = True

        # --- Entry signals ---
        if position is None:
            atr = row["atr"]
            stop_dist = atr_stop_mult * atr
            if stop_dist <= 0:
                equity_curve.append(equity)
                continue
            size = (risk_per_trade * equity) / stop_dist

            # LONG: oversold + below lower BB
            long_signal = False
            if row["rsi"] < rsi_extreme_oversold and row["close"] < row["bb_lower"]:
                long_signal = True  # Extreme oversold -- take it regardless of trend
            elif row["rsi"] < rsi_oversold and row["close"] < row["bb_lower"] and row["close"] > row["ema_100"]:
                long_signal = True  # Oversold in uptrend

            if long_signal:
                entry_p = row["close"]
                sl = entry_p - stop_dist
                position = {"side": "long", "entry": entry_p, "size": size,
                            "stop": sl, "entry_time": ts, "symbol": symbol}
                equity -= entry_p * size * COMMISSION

            # SHORT: overbought + above upper BB
            elif position is None:
                short_signal = False
                if row["rsi"] > rsi_extreme_overbought and row["close"] > row["bb_upper"]:
                    short_signal = True
                elif row["rsi"] > rsi_overbought and row["close"] > row["bb_upper"] and row["close"] < row["ema_100"]:
                    short_signal = True

                if short_signal:
                    entry_p = row["close"]
                    sl = entry_p + stop_dist
                    position = {"side": "short", "entry": entry_p, "size": size,
                                "stop": sl, "entry_time": ts, "symbol": symbol}
                    equity -= entry_p * size * COMMISSION

        equity_curve.append(equity)
        peak_equity = max(peak_equity, equity)
        dd = (peak_equity - equity) / peak_equity
        max_dd = max(max_dd, dd)

    # Close open position at end
    if position is not None:
        last = bt.iloc[-1]
        if position["side"] == "long":
            pnl = (last["close"] - position["entry"]) * position["size"]
        else:
            pnl = (position["entry"] - last["close"]) * position["size"]
        pnl -= last["close"] * position["size"] * COMMISSION
        equity += pnl
        trades.append({**position, "exit_time": bt.index[-1], "exit": last["close"], "pnl": pnl, "reason": "eod"})

    return {
        "symbol": symbol,
        "equity": equity,
        "max_dd": max_dd,
        "trades": trades,
        "equity_curve": equity_curve,
    }


# ---------------------------------------------------------------------------
# MULTI-ASSET ORCHESTRATOR
# ---------------------------------------------------------------------------

def run_multi_asset_backtest(
    assets: dict[str, pd.DataFrame],
    start_date: str = "2025-07-01",
    end_date: str = "2025-12-31",
    initial_capital: float = 1000.0,
) -> dict:
    """Run backtest across multiple assets with equal capital allocation."""
    n = len(assets)
    alloc = initial_capital / n

    all_trades = []
    total_equity = 0.0
    worst_dd = 0.0

    for symbol, df in assets.items():
        result = backtest_asset(df, symbol, start_date, end_date, alloc)
        total_equity += result["equity"]
        worst_dd = max(worst_dd, result["max_dd"])
        all_trades.extend(result["trades"])

    trades_df = pd.DataFrame(all_trades) if all_trades else pd.DataFrame()
    n_trades = len(trades_df)
    total_return = ((total_equity - initial_capital) / initial_capital) * 100

    win_rate = avg_win = avg_loss = profit_factor = 0.0
    if n_trades > 0:
        winners = trades_df["pnl"] > 0
        win_rate = winners.sum() / n_trades * 100
        avg_win = trades_df.loc[winners, "pnl"].mean() if winners.any() else 0
        avg_loss = trades_df.loc[~winners, "pnl"].mean() if (~winners).any() else 0
        tw = trades_df.loc[winners, "pnl"].sum()
        tl = abs(trades_df.loc[~winners, "pnl"].sum()) if (~winners).any() else 0.01
        profit_factor = tw / tl if tl > 0 else float("inf")

    # Approximate Sharpe from trade returns
    if n_trades > 1:
        trade_rets = trades_df["pnl"] / (initial_capital / n)
        # Annualize: assume ~6 months, scale to yearly
        periods_per_year = n_trades * 2  # rough annualization
        sharpe = (trade_rets.mean() / trade_rets.std()) * np.sqrt(periods_per_year) if trade_rets.std() > 0 else 0
    else:
        sharpe = 0.0

    return {
        "strategy_name": "Adaptive RSI Mean-Reversion (Multi-Asset)",
        "description": (
            "RSI(14) oversold/overbought mean-reversion on 4h BTC/USDT and ETH/USDT. "
            "Entries at RSI extremes with Bollinger Band confirmation and EMA(100) trend filter. "
            "Exits at RSI=50 or BB midline. ATR-based stops. Equal capital split across assets."
        ),
        "pairs": list(assets.keys()),
        "period": f"{start_date} to {end_date}",
        "initial_capital": initial_capital,
        "final_equity": round(total_equity, 2),
        "total_return_pct": round(total_return, 2),
        "sharpe_ratio": round(sharpe, 2),
        "max_drawdown_pct": round(worst_dd * 100, 2),
        "n_trades": n_trades,
        "win_rate_pct": round(win_rate, 2),
        "profit_factor": round(profit_factor, 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "trades": trades_df,
    }


# ---------------------------------------------------------------------------
# OUTPUT
# ---------------------------------------------------------------------------

def format_results(r: dict) -> str:
    lines = [
        "=" * 65,
        f"  STRATEGY: {r['strategy_name']}",
        "=" * 65,
        f"  {r['description']}",
        "",
        f"  Pairs:             {', '.join(r['pairs'])}",
        f"  Period:            {r['period']}",
        f"  Initial Capital:   ${r['initial_capital']:,.2f}",
        f"  Final Equity:      ${r['final_equity']:,.2f}",
        "-" * 65,
        f"  Total Return:      {r['total_return_pct']:+.2f}%",
        f"  Sharpe Ratio:      {r['sharpe_ratio']:.2f}",
        f"  Max Drawdown:      {r['max_drawdown_pct']:.2f}%",
        f"  Number of Trades:  {r['n_trades']}",
        f"  Win Rate:          {r['win_rate_pct']:.1f}%",
        f"  Profit Factor:     {r['profit_factor']:.2f}",
        f"  Avg Win:           ${r['avg_win']:.2f}",
        f"  Avg Loss:          ${r['avg_loss']:.2f}",
        "=" * 65,
        "",
        "  EDGE: Exploits short-term RSI extremes in liquid crypto markets.",
        "  Bollinger Band confirmation ensures entries at statistical extremes.",
        "  EMA trend filter prevents fighting the dominant trend.",
        "  Multi-asset diversification (BTC + ETH) smooths returns.",
        "  Quick exits at mean (RSI=50 / BB mid) capture reversion profit",
        "  before momentum resumes.",
        "=" * 65,
    ]
    if not r["trades"].empty:
        lines.append("\n  All Trades:")
        cols = ["symbol", "entry_time", "side", "entry", "exit", "pnl", "reason"]
        available = [c for c in cols if c in r["trades"].columns]
        lines.append(r["trades"][available].to_string())
    return "\n".join(lines)


if __name__ == "__main__":
    assets = {}
    for sym in ["BTCUSDT", "ETHUSDT"]:
        assets[sym] = download_binance_klines(
            symbol=sym, interval="4h", start="2025-05-01", end="2026-01-02",
        )
        print(f"  {sym}: {len(assets[sym])} bars")

    results = run_multi_asset_backtest(assets)
    output = format_results(results)
    print(output)

    out_dir = Path(__file__).parent.parent / "results"
    out_dir.mkdir(exist_ok=True)
    with open(out_dir / "round1_results.txt", "w") as f:
        f.write(output)
    print(f"\nResults saved to {out_dir / 'round1_results.txt'}")
