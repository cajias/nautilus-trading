"""
Agent 2 — Sentiment Trader | Round 3
Dual-mode BTC strategy: Breakout + Panic-Buy.

Mode 1 (Breakout): Catch big trend moves when everything aligns.
Mode 2 (Panic Buy): Buy extreme fear dips for quick mean-reversion bounces.

Key design: very few trades, high conviction, BTC only, long only.
"""

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import requests


def fetch_binance_klines(
    symbol: str,
    interval: str,
    start: str,
    end: str,
) -> pd.DataFrame:
    """Fetch klines from Binance public API."""
    url = "https://api.binance.com/api/v3/klines"
    start_ms = int(pd.Timestamp(start).timestamp() * 1000)
    end_ms = int(pd.Timestamp(end).timestamp() * 1000)

    all_klines = []
    current_start = start_ms

    while current_start < end_ms:
        params = {
            "symbol": symbol,
            "interval": interval,
            "startTime": current_start,
            "endTime": end_ms,
            "limit": 1000,
        }
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if not data:
            break
        all_klines.extend(data)
        current_start = data[-1][6] + 1

    df = pd.DataFrame(
        all_klines,
        columns=[
            "open_time", "open", "high", "low", "close", "volume",
            "close_time", "quote_volume", "trades", "taker_buy_base",
            "taker_buy_quote", "ignore",
        ],
    )
    for col in ["open", "high", "low", "close", "volume", "quote_volume"]:
        df[col] = df[col].astype(float)
    df["date"] = pd.to_datetime(df["open_time"], unit="ms")
    df = df.set_index("date")
    return df


@dataclass
class Trade:
    entry_date: str
    entry_price: float
    direction: str
    size_btc: float
    mode: str  # "breakout" or "panic_buy"
    trailing_stop: float = 0.0
    highest_price: float = 0.0
    target_price: float = 0.0
    exit_date: str = ""
    exit_price: float = 0.0
    pnl: float = 0.0
    exit_reason: str = ""


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Compute all indicators."""
    # Trend
    df["ema_20"] = df["close"].ewm(span=20, adjust=False).mean()
    df["ema_50"] = df["close"].ewm(span=50, adjust=False).mean()
    df["sma_200"] = df["close"].rolling(200).mean()

    # Volume
    df["vol_ma_20"] = df["volume"].rolling(20).mean()
    df["vol_ratio"] = df["volume"] / df["vol_ma_20"]

    # Momentum
    df["roc_10"] = df["close"].pct_change(10) * 100
    df["roc_5"] = df["close"].pct_change(5) * 100

    # ATR
    high_low = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift(1)).abs()
    low_close = (df["low"] - df["close"].shift(1)).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    df["atr_14"] = tr.rolling(14).mean()

    # RSI (14)
    delta = df["close"].diff()
    gain = delta.where(delta > 0, 0.0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(14).mean()
    rs = gain / loss.clip(lower=1e-10)
    df["rsi_14"] = 100 - (100 / (1 + rs))

    # Breakout detection
    df["high_20"] = df["high"].rolling(20).max()
    df["is_breakout"] = df["close"] >= df["high_20"].shift(1)

    # Taker buy ratio
    df["taker_buy_ratio"] = df["taker_buy_base"].astype(float) / df["volume"].clip(lower=1e-10)
    df["tbr_ma_20"] = df["taker_buy_ratio"].rolling(20).mean()
    df["tbr_std_20"] = df["taker_buy_ratio"].rolling(20).std()
    df["tbr_zscore"] = (df["taker_buy_ratio"] - df["tbr_ma_20"]) / df["tbr_std_20"].clip(lower=1e-10)

    # Bollinger Bands for panic buy
    df["bb_mid"] = df["close"].rolling(20).mean()
    df["bb_std"] = df["close"].rolling(20).std()
    df["bb_lower"] = df["bb_mid"] - 2.0 * df["bb_std"]
    df["bb_pct"] = (df["close"] - df["bb_lower"]) / (4.0 * df["bb_std"]).clip(lower=1e-10)

    # Distance from 20-day low (for panic detection)
    df["low_20"] = df["low"].rolling(20).min()
    df["dist_from_low"] = (df["close"] - df["low_20"]) / df["low_20"] * 100

    return df


def run_backtest(
    start: str,
    end: str,
    initial_capital: float = 1000.0,
    fee_rate: float = 0.001,
) -> dict:
    """Run the dual-mode strategy."""
    buffer_start = (pd.Timestamp(start) - pd.Timedelta(days=280)).strftime("%Y-%m-%d")
    df = fetch_binance_klines("BTCUSDT", "1d", buffer_start, end)
    df = compute_indicators(df)

    test_start = pd.Timestamp(start)
    test_end = pd.Timestamp(end)

    equity = initial_capital
    position: Trade | None = None
    trades: list[Trade] = []
    peak_equity = initial_capital
    max_drawdown_pct = 0.0
    equity_curve = []
    cooldown_until = None

    for i in range(len(df)):
        row = df.iloc[i]
        date = df.index[i]

        if date < test_start or date >= test_end:
            continue

        # Mark to market
        if position is not None:
            unrealized = (row["close"] - position.entry_price) * position.size_btc
            current_equity = equity + unrealized
        else:
            current_equity = equity

        equity_curve.append({"date": str(date.date()), "equity": current_equity})
        peak_equity = max(peak_equity, current_equity)
        dd = (peak_equity - current_equity) / peak_equity * 100
        max_drawdown_pct = max(max_drawdown_pct, dd)

        if pd.isna(row.get("sma_200")) or pd.isna(row.get("rsi_14")) or pd.isna(row.get("tbr_zscore")):
            continue

        # --- EXIT LOGIC ---
        if position is not None:
            days_held = (date - pd.Timestamp(position.entry_date)).days
            atr = row["atr_14"]

            if position.mode == "breakout":
                # Update trailing stop
                if row["high"] > position.highest_price:
                    position.highest_price = row["high"]
                    position.trailing_stop = position.highest_price - 3.0 * atr

                exit_reason = None
                if row["close"] <= position.trailing_stop:
                    exit_reason = "trailing_stop"
                elif row["roc_10"] < -8 and row["close"] < row["ema_20"]:
                    exit_reason = "momentum_collapse"
                elif days_held >= 60:
                    exit_reason = "time_exit"

            elif position.mode == "panic_buy":
                # Quick exit: take profit at target or cut loss tight
                exit_reason = None
                profit_pct = (row["close"] - position.entry_price) / position.entry_price * 100

                if row["close"] >= position.target_price:
                    exit_reason = "take_profit"
                elif profit_pct < -3.0:
                    exit_reason = "stop_loss"
                elif days_held >= 10:
                    exit_reason = "time_exit"

            if exit_reason:
                exit_price = row["close"]
                fee = exit_price * position.size_btc * fee_rate
                pnl = (exit_price - position.entry_price) * position.size_btc - fee
                position.exit_date = str(date.date())
                position.exit_price = exit_price
                position.pnl = pnl
                position.exit_reason = exit_reason
                trades.append(position)
                equity += pnl
                if pnl < 0:
                    cooldown_until = date + pd.Timedelta(days=10)
                position = None

        # --- ENTRY LOGIC ---
        if position is None:
            if cooldown_until is not None and date < cooldown_until:
                continue

            atr = row["atr_14"]

            # MODE 1: BREAKOUT (trend-following, big position)
            cond_trend = (row["close"] > row["ema_50"]) and (row["ema_50"] > row["sma_200"])
            cond_breakout = row["is_breakout"]
            cond_volume = row["vol_ratio"] > 2.0
            cond_sentiment = row["tbr_zscore"] > 0.3
            cond_momentum = row["roc_10"] > 3.0

            if cond_trend and cond_breakout and cond_volume and cond_sentiment and cond_momentum:
                entry_price = row["close"]
                invest = equity * 0.90
                size_btc = invest / entry_price
                fee = entry_price * size_btc * fee_rate
                equity -= fee
                position = Trade(
                    entry_date=str(date.date()),
                    entry_price=entry_price,
                    direction="long",
                    size_btc=size_btc,
                    mode="breakout",
                    highest_price=entry_price,
                    trailing_stop=entry_price - 3.0 * atr,
                )

            # MODE 2: PANIC BUY (mean-reversion, smaller position)
            # Conditions: RSI < 30, price near Bollinger lower band,
            # volume spike (panic selling), still above SMA200 (not broken trend)
            elif (
                row["rsi_14"] < 30
                and row["bb_pct"] < 0.15
                and row["vol_ratio"] > 1.5
                and row["close"] > row["sma_200"]
                and row["roc_5"] < -5
            ):
                entry_price = row["close"]
                invest = equity * 0.50  # smaller position for mean reversion
                size_btc = invest / entry_price
                fee = entry_price * size_btc * fee_rate
                equity -= fee
                # Target: bounce back to 20-day moving average
                target = row["bb_mid"]
                position = Trade(
                    entry_date=str(date.date()),
                    entry_price=entry_price,
                    direction="long",
                    size_btc=size_btc,
                    mode="panic_buy",
                    target_price=target,
                )

    # Close any open position at period end
    if position is not None:
        last_row = df.iloc[-1]
        exit_price = last_row["close"]
        fee = exit_price * position.size_btc * fee_rate
        pnl = (exit_price - position.entry_price) * position.size_btc - fee
        position.exit_date = str(df.index[-1].date())
        position.exit_price = exit_price
        position.pnl = pnl
        position.exit_reason = "period_end"
        trades.append(position)
        equity += pnl
        position = None

    # Metrics
    total_return_pct = (equity - initial_capital) / initial_capital * 100
    num_trades = len(trades)
    wins = [t for t in trades if t.pnl > 0]
    win_rate = len(wins) / num_trades * 100 if num_trades > 0 else 0.0

    if len(equity_curve) > 1:
        eq_series = pd.Series([e["equity"] for e in equity_curve])
        daily_returns = eq_series.pct_change().dropna()
        if daily_returns.std() > 0:
            sharpe_ratio = (daily_returns.mean() / daily_returns.std()) * np.sqrt(365)
        else:
            sharpe_ratio = 0.0
    else:
        sharpe_ratio = 0.0

    trade_log = []
    for t in trades:
        trade_log.append({
            "entry_date": t.entry_date,
            "exit_date": t.exit_date,
            "direction": t.direction,
            "mode": t.mode,
            "entry_price": round(t.entry_price, 2),
            "exit_price": round(t.exit_price, 2),
            "size_btc": round(t.size_btc, 6),
            "pnl": round(t.pnl, 2),
            "exit_reason": t.exit_reason,
        })

    return {
        "final_equity": round(equity, 2),
        "total_return_pct": round(total_return_pct, 2),
        "sharpe_ratio": round(sharpe_ratio, 2),
        "max_drawdown_pct": round(max_drawdown_pct, 2),
        "num_trades": num_trades,
        "win_rate": round(win_rate, 2),
        "trade_log": trade_log,
    }


def main():
    print("=" * 60)
    print("Agent 2 — Sentiment Trader | Round 3")
    print("Dual-Mode: Breakout + Panic-Buy (BTC Only, Long Only)")
    print("=" * 60)

    results_path = Path(__file__).parent / "results.txt"

    print("\n--- TRAIN: 2024-07-01 to 2024-12-31 ---")
    train = run_backtest("2024-07-01", "2024-12-31")
    print(f"  Final equity:  ${train['final_equity']:,.2f}")
    print(f"  Return:        {train['total_return_pct']:+.2f}%")
    print(f"  Sharpe:        {train['sharpe_ratio']:.2f}")
    print(f"  Max drawdown:  {train['max_drawdown_pct']:.2f}%")
    print(f"  Trades:        {train['num_trades']}")
    print(f"  Win rate:      {train['win_rate']:.1f}%")
    for t in train["trade_log"]:
        print(f"    [{t['mode']:9s}] {t['direction']:5s} {t['entry_date']} -> {t['exit_date']}  "
              f"${t['entry_price']:>10,.2f} -> ${t['exit_price']:>10,.2f}  "
              f"PnL: ${t['pnl']:>8,.2f}  ({t['exit_reason']})")

    print("\n--- TEST: 2025-01-01 to 2025-03-31 ---")
    test = run_backtest("2025-01-01", "2025-03-31")
    print(f"  Final equity:  ${test['final_equity']:,.2f}")
    print(f"  Return:        {test['total_return_pct']:+.2f}%")
    print(f"  Sharpe:        {test['sharpe_ratio']:.2f}")
    print(f"  Max drawdown:  {test['max_drawdown_pct']:.2f}%")
    print(f"  Trades:        {test['num_trades']}")
    print(f"  Win rate:      {test['win_rate']:.1f}%")
    for t in test["trade_log"]:
        print(f"    [{t['mode']:9s}] {t['direction']:5s} {t['entry_date']} -> {t['exit_date']}  "
              f"${t['entry_price']:>10,.2f} -> ${t['exit_price']:>10,.2f}  "
              f"PnL: ${t['pnl']:>8,.2f}  ({t['exit_reason']})")

    output = {
        "agent": "Agent 2 — Sentiment Trader",
        "round": 3,
        "strategy": "Dual-Mode: Breakout + Panic-Buy (BTC Only, Long Only)",
        "description": (
            "Two entry modes. BREAKOUT: all 5 conditions (trend, breakout, volume 2x, "
            "taker z>0.3, ROC>3%) with 90% equity, 3x ATR trailing stop, 60-day max hold. "
            "PANIC BUY: RSI<30, near BB lower, volume spike, above SMA200, 5-day ROC<-5%, "
            "50% equity, target=BB midline, 3% stop, 10-day max hold. 10-day cooldown after loss."
        ),
        "train": train,
        "test": test,
    }

    with open(results_path, "w") as f:
        f.write(json.dumps(output, indent=2))

    print(f"\nResults saved to {results_path}")
    return output


if __name__ == "__main__":
    main()
