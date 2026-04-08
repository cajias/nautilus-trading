"""
Agent 3 - Macro Strategist - Round 4
Daily Momentum with 50-SMA Filter (BTC, Long-Only)

Simple and stable:
- Entry: Close above 50-SMA AND RSI(7) < 70
- Exit: Close 2% below 50-SMA OR 8% hard stop below entry
- 95% position size, 0.1% fees, 5-day cooldown after exit
- The 50-SMA avoids whipsaws in choppy markets (June consolidation)
  while still catching major trends (Oct-Dec bull run, Apr-May recovery)
"""

import requests
import pandas as pd
import numpy as np
from dataclasses import dataclass


@dataclass
class Trade:
    entry_date: str
    entry_price: float
    side: str
    size: float
    exit_date: str = ""
    exit_price: float = 0.0
    pnl: float = 0.0
    pnl_pct: float = 0.0


def fetch_binance_klines(symbol: str, interval: str, start: str, end: str) -> pd.DataFrame:
    url = "https://api.binance.com/api/v3/klines"
    start_ms = int(pd.Timestamp(start).timestamp() * 1000)
    end_ms = int(pd.Timestamp(end).timestamp() * 1000)
    all_klines = []
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
    df = pd.DataFrame(all_klines, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades", "taker_buy_base",
        "taker_buy_quote", "ignore"])
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)
    df["date"] = pd.to_datetime(df["open_time"], unit="ms")
    df = df.set_index("date")
    return df


def compute_indicators(daily: pd.DataFrame) -> pd.DataFrame:
    df = daily.copy()
    df["sma_50"] = df["close"].rolling(50).mean()
    df["above_sma"] = df["close"] > df["sma_50"]
    df["below_sma_2pct"] = df["close"] < df["sma_50"] * 0.98  # Exit: 2% below SMA
    # RSI(7)
    delta = df["close"].diff()
    gain = delta.where(delta > 0, 0).ewm(span=7, adjust=False).mean()
    loss = (-delta.where(delta < 0, 0)).ewm(span=7, adjust=False).mean()
    rs = gain / loss
    df["rsi_7"] = 100 - (100 / (1 + rs))
    return df


def run_backtest(start: str, end: str, initial_capital: float = 1000.0) -> dict:
    buffer_start = (pd.Timestamp(start) - pd.Timedelta(days=120)).strftime("%Y-%m-%d")
    daily = fetch_binance_klines("BTCUSDT", "1d", buffer_start, end)
    df = compute_indicators(daily)
    df = df.loc[start:end]

    FEE_RATE = 0.001
    equity = initial_capital
    position = None
    trades: list[Trade] = []
    equity_curve = []
    cooldown_until = None

    for i in range(len(df)):
        row = df.iloc[i]
        date_str = str(df.index[i].date())
        cur_date = df.index[i]
        price = row["close"]
        above_sma = row["above_sma"]
        below_sma_2pct = row["below_sma_2pct"]
        rsi = row["rsi_7"]

        if pd.isna(above_sma) or pd.isna(rsi):
            equity_curve.append(equity)
            continue

        # --- Exit ---
        if position is not None:
            stop_price = position.entry_price * 0.92
            if below_sma_2pct or price < stop_price:
                exit_value = position.size * price * (1 - FEE_RATE)
                position.exit_date = date_str
                position.exit_price = price
                position.pnl = exit_value - (position.size * position.entry_price)
                position.pnl_pct = (price / position.entry_price - 1) * 100
                equity = exit_value
                trades.append(position)
                position = None
                cooldown_until = cur_date + pd.Timedelta(days=5)
            else:
                equity = position.size * price  # mark to market

        # --- Entry ---
        if position is None:
            if cooldown_until is not None and cur_date < cooldown_until:
                equity_curve.append(equity)
                continue
        if position is None and above_sma and rsi < 70:
            size_usd = equity * 0.95
            size_btc = size_usd / price * (1 - FEE_RATE)
            position = Trade(entry_date=date_str, entry_price=price,
                             side="long", size=size_btc)
            equity = size_btc * price

        equity_curve.append(equity)

    # Close open position
    if position is not None:
        price = df.iloc[-1]["close"]
        exit_value = position.size * price * (1 - FEE_RATE)
        position.pnl = exit_value - (position.size * position.entry_price)
        position.pnl_pct = (price / position.entry_price - 1) * 100
        position.exit_date = str(df.index[-1].date())
        position.exit_price = price
        equity = exit_value
        trades.append(position)

    # Metrics
    equity_series = pd.Series(equity_curve, index=df.index)
    total_return_pct = (equity / initial_capital - 1) * 100
    daily_returns = equity_series.pct_change().dropna()
    sharpe_ratio = ((daily_returns.mean() / daily_returns.std()) * np.sqrt(365)
                    if len(daily_returns) > 1 and daily_returns.std() > 0 else 0.0)
    rolling_max = equity_series.cummax()
    drawdown = (equity_series - rolling_max) / rolling_max
    max_drawdown_pct = abs(drawdown.min()) * 100
    winning = [t for t in trades if t.pnl > 0]
    win_rate = len(winning) / len(trades) * 100 if trades else 0.0

    trade_log = [
        {"entry_date": t.entry_date, "exit_date": t.exit_date, "side": t.side,
         "entry_price": round(t.entry_price, 2), "exit_price": round(t.exit_price, 2),
         "pnl": round(t.pnl, 4), "pnl_pct": round(t.pnl_pct, 2)}
        for t in trades
    ]
    return {
        "final_equity": round(equity, 2),
        "total_return_pct": round(total_return_pct, 2),
        "sharpe_ratio": round(sharpe_ratio, 2),
        "max_drawdown_pct": round(max_drawdown_pct, 2),
        "num_trades": len(trades),
        "win_rate": round(win_rate, 2),
        "trade_log": trade_log,
    }


if __name__ == "__main__":
    print("=" * 60)
    print("AGENT 3 - MACRO STRATEGIST - ROUND 4")
    print("Daily Momentum (20-SMA + RSI Filter) BTC Long-Only")
    print("=" * 60)

    results = {}
    for label, s, e in [("TRAIN Oct 2024 - Mar 2025", "2024-10-01", "2025-03-31"),
                         ("TEST Apr - Jun 2025", "2025-04-01", "2025-06-30")]:
        print(f"\n--- {label} ---")
        r = run_backtest(s, e, 1000)
        results[label] = r
        print(f"Final Equity:    ${r['final_equity']:.2f}")
        print(f"Total Return:    {r['total_return_pct']:.2f}%")
        print(f"Sharpe Ratio:    {r['sharpe_ratio']:.2f}")
        print(f"Max Drawdown:    {r['max_drawdown_pct']:.2f}%")
        print(f"Num Trades:      {r['num_trades']}")
        print(f"Win Rate:        {r['win_rate']:.2f}%")
        print("\nTrade Log:")
        for t in r["trade_log"]:
            print(f"  {t['side']:5s} {t['entry_date']} -> {t['exit_date']} | "
                  f"${t['entry_price']:>10,.2f} -> ${t['exit_price']:>10,.2f} | "
                  f"PnL: {t['pnl_pct']:+.2f}%")

    rp = "/Users/rc/Projects/workspace/nautilus-trading/competition/agent-3-macro/round4/results.txt"
    with open(rp, "w") as f:
        f.write("AGENT 3 - MACRO STRATEGIST - ROUND 4\n")
        f.write("Daily Momentum (50-SMA + RSI Filter) BTC Long-Only\n")
        f.write("=" * 50 + "\n\n")
        f.write("Strategy:\n")
        f.write("  Entry: Close > 20-SMA AND RSI(7) < 70\n")
        f.write("  Exit: Close < 20-SMA OR 8% hard stop\n")
        f.write("  95% position, 0.1% fees\n\n")
        for label, res in results.items():
            f.write(f"{label}:\n")
            for k, v in res.items():
                if k != "trade_log":
                    f.write(f"  {k}: {v}\n")
            f.write("\n  Trades:\n")
            for t in res["trade_log"]:
                f.write(f"    {t['side']:5s} {t['entry_date']} -> {t['exit_date']} | "
                        f"${t['entry_price']:>10,.2f} -> ${t['exit_price']:>10,.2f} | "
                        f"PnL: {t['pnl_pct']:+.2f}%\n")
            f.write("\n")
    print(f"\nResults saved to {rp}")
