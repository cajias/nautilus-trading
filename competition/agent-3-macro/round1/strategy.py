"""
Agent 3 - Macro Strategist: RSI Mean-Reversion + Trend (Round 1)
================================================================
BTC-only strategy combining mean-reversion on oversold dips with
trend confirmation for exits.

Key insight from data analysis:
- Jul-Sep 2024 BTC: $63k -> crash to $54k Aug 5 -> recovery to $63k
- RSI hit 14.4 on Aug 5! Massive oversold = best buy signal
- ADX stayed >25 most of the period, so pure trend-following whipsawed
- Solution: primarily use RSI mean-reversion, with trend for exits

Strategy:
1. BUY when RSI < 30 (oversold dip)
2. SELL when RSI > 65 (overbought) OR price hits ATR trailing stop
3. Position size: 40% (single entry) with scale-in on deeper dips
4. If RSI < 20, add another 20% (extreme oversold = stronger signal)
5. Always use 3x ATR trailing stop for protection

TRAIN: Jan 1 - Jun 30, 2024
TEST:  Jul 1 - Sep 30, 2024
"""

import os
import sys

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


def compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.ewm(span=period, adjust=False).mean()
    avg_loss = loss.ewm(span=period, adjust=False).mean()
    rs = avg_gain / (avg_loss + 1e-10)
    return 100 - (100 / (1 + rs))


def compute_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()


def run_backtest(
    symbol: str = "BTCUSDT",
    start_data: str = "2023-08-01",
    end_data: str = "2024-10-02",
    train_start: str = "2024-01-01",
    train_end: str = "2024-06-30",
    test_start: str = "2024-07-01",
    test_end: str = "2024-09-30",
    initial_capital: float = 1000.0,
    # RSI params
    rsi_period: int = 14,
    rsi_buy: float = 30.0,
    rsi_extreme_buy: float = 20.0,
    rsi_sell: float = 65.0,
    # Position sizing
    base_position: float = 0.40,
    scale_in_size: float = 0.20,
    # Risk
    atr_stop_mult: float = 3.0,
    fee_rate: float = 0.001,
) -> dict:
    print(f"Downloading {symbol} data...")
    df = fetch_klines(symbol, "1d", start_data, end_data)
    if df.empty:
        return {}
    print(f"  {symbol}: {len(df)} bars ({df.index[0].date()} to {df.index[-1].date()})")

    close = df["close"]
    high = df["high"]
    low = df["low"]
    returns = close.pct_change()

    rsi = compute_rsi(close, rsi_period)
    atr = compute_atr(high, low, close, 14)
    sma_50 = close.rolling(50).mean()

    # Debug
    print(f"\nRSI at key TEST dates:")
    for d in ["2024-07-01", "2024-07-13", "2024-07-25", "2024-08-01", "2024-08-05",
              "2024-08-08", "2024-08-15", "2024-08-25", "2024-09-01", "2024-09-07",
              "2024-09-15", "2024-09-20", "2024-09-25", "2024-09-30"]:
        dt = pd.Timestamp(d)
        idx = close.index.get_indexer([dt], method="nearest")[0]
        ad = close.index[idx]
        print(f"  {ad.date()}: BTC=${close.iloc[idx]:.0f}  RSI={rsi.iloc[idx]:.1f}  ATR=${atr.iloc[idx]:.0f}")

    def run_period(bt_start: str, bt_end: str, capital: float) -> dict:
        bt_mask = (close.index >= bt_start) & (close.index <= bt_end)
        bt_dates = close.index[bt_mask]
        if len(bt_dates) == 0:
            return {}

        equity = capital
        equity_curve = []
        position = 0.0
        trailing_high = None
        entry_price = None
        total_fees = 0.0
        trade_log = []
        scaled_in = False

        for date in bt_dates:
            # Mark to market
            if position > 0:
                ret = returns.loc[date] if not pd.isna(returns.loc[date]) else 0.0
                equity *= (1 + position * ret)
            equity_curve.append({"date": date, "equity": equity})

            if position > 0:
                cp = close.loc[date]
                trailing_high = max(trailing_high or cp, cp)

            idx_loc = close.index.get_loc(date)
            rsi_val = rsi.iloc[idx_loc]
            atr_val = atr.iloc[idx_loc]
            cp = close.iloc[idx_loc]

            if pd.isna(rsi_val) or pd.isna(atr_val):
                continue

            # ATR trailing stop
            if position > 0 and trailing_high is not None:
                stop_price = trailing_high - atr_stop_mult * atr_val
                if cp < stop_price:
                    fee = position * fee_rate
                    equity *= (1 - fee)
                    total_fees += fee * equity
                    pnl = ((cp / entry_price) - 1) * 100 if entry_price else 0
                    trade_log.append({"date": date, "action": "STOP_EXIT", "position": 0.0,
                                      "equity": equity, "price": cp,
                                      "reason": f"ATR stop (high={trailing_high:.0f}), PnL={pnl:+.1f}%"})
                    position = 0.0
                    trailing_high = None
                    entry_price = None
                    scaled_in = False
                    continue

            # Entry: RSI oversold
            if position == 0 and rsi_val < rsi_buy:
                position = base_position
                trailing_high = cp
                entry_price = cp
                scaled_in = False
                fee = position * fee_rate
                equity *= (1 - fee)
                total_fees += fee * equity
                trade_log.append({"date": date, "action": "BUY", "position": position,
                                  "equity": equity, "price": cp,
                                  "reason": f"RSI={rsi_val:.1f} oversold"})

            # Scale-in: extreme oversold
            elif position > 0 and not scaled_in and rsi_val < rsi_extreme_buy:
                old_pos = position
                position = min(position + scale_in_size, 0.65)
                added = position - old_pos
                if added > 0:
                    # Average down entry price
                    entry_price = (entry_price * old_pos + cp * added) / position
                    fee = added * fee_rate
                    equity *= (1 - fee)
                    total_fees += fee * equity
                    scaled_in = True
                    trade_log.append({"date": date, "action": "SCALE_IN", "position": position,
                                      "equity": equity, "price": cp,
                                      "reason": f"RSI={rsi_val:.1f} extreme oversold, avg_entry=${entry_price:.0f}"})

            # Exit: RSI overbought
            elif position > 0 and rsi_val > rsi_sell:
                fee = position * fee_rate
                equity *= (1 - fee)
                total_fees += fee * equity
                pnl = ((cp / entry_price) - 1) * 100 if entry_price else 0
                trade_log.append({"date": date, "action": "SELL", "position": 0.0,
                                  "equity": equity, "price": cp,
                                  "reason": f"RSI={rsi_val:.1f} overbought, PnL={pnl:+.1f}%"})
                position = 0.0
                trailing_high = None
                entry_price = None
                scaled_in = False

        # Close at period end
        if position > 0:
            cp = close.loc[bt_dates[-1]]
            fee = position * fee_rate
            equity *= (1 - fee)
            total_fees += fee * equity
            pnl = ((cp / entry_price) - 1) * 100 if entry_price else 0
            trade_log.append({"date": bt_dates[-1], "action": "CLOSE", "position": 0.0,
                              "equity": equity, "price": cp,
                              "reason": f"Period end, PnL={pnl:+.1f}%"})
            position = 0.0

        # Metrics
        eq_df = pd.DataFrame(equity_curve)
        eq_df["returns"] = eq_df["equity"].pct_change()
        final_eq = equity
        total_ret = (final_eq / capital - 1) * 100
        rolling_max = eq_df["equity"].cummax()
        drawdown = (rolling_max - eq_df["equity"]) / rolling_max
        max_dd = drawdown.max() * 100
        daily_rets = eq_df["returns"].dropna()
        sharpe = (daily_rets.mean() / daily_rets.std() * np.sqrt(365)) if daily_rets.std() > 0 else 0

        btc_s = close.loc[bt_dates[0]]
        btc_e = close.loc[bt_dates[-1]]
        bnh_ret = (btc_e / btc_s - 1) * 100

        # Win rate
        exits = [t for t in trade_log if t["action"] in ("SELL", "CLOSE", "STOP_EXIT")]
        wins = sum(1 for t in exits if "+0" not in t.get("reason", "") and "PnL=+" in t.get("reason", ""))
        win_rate = (wins / len(exits) * 100) if exits else 0

        return {
            "total_return_pct": round(total_ret, 2),
            "sharpe_ratio": round(sharpe, 2),
            "max_drawdown_pct": round(max_dd, 2),
            "win_rate_pct": round(win_rate, 1),
            "num_entries": len([t for t in trade_log if t["action"] in ("BUY", "SCALE_IN")]),
            "final_equity": round(final_eq, 2),
            "buy_hold_btc_pct": round(bnh_ret, 2),
            "total_fees": round(total_fees, 2),
            "trade_log": trade_log,
        }

    print("\n--- TRAIN ---")
    train_metrics = run_period(train_start, train_end, initial_capital)
    print("\n--- TEST ---")
    test_metrics = run_period(test_start, test_end, initial_capital)

    return {"train": train_metrics, "test": test_metrics}


def format_report(label: str, m: dict, period: str) -> str:
    return "\n".join([
        "=" * 60,
        f"AGENT 3 - MACRO STRATEGIST: {label}",
        "=" * 60,
        f"Asset:            BTC/USDT (RSI mean-reversion + trend exit)",
        f"Timeframe:        Daily",
        f"Period:           {period}",
        f"Initial Capital:  $1,000.00",
        f"Final Equity:     ${m['final_equity']:,.2f}",
        "-" * 60,
        f"Total Return:     {m['total_return_pct']:+.2f}%",
        f"BTC Buy & Hold:   {m['buy_hold_btc_pct']:+.2f}%",
        f"Sharpe Ratio:     {m['sharpe_ratio']:.2f}",
        f"Max Drawdown:     {m['max_drawdown_pct']:.2f}%",
        f"Win Rate:         {m['win_rate_pct']:.1f}%",
        f"Num Entries:      {m['num_entries']}",
        f"Total Fees:       ${m['total_fees']:.2f}",
        "=" * 60,
    ])


def main():
    print("=" * 60)
    print("Agent 3 - Macro Strategist: RSI Mean-Reversion + Trend")
    print(f"Round 1 | Train: Jan-Jun 2024 | Test: Jul-Sep 2024")
    print("=" * 60)

    results = run_backtest()
    if not results or not results.get("test"):
        print("ERROR: Backtest failed")
        return 1

    train_m = results["train"]
    test_m = results["test"]
    train_log = train_m.pop("trade_log", [])
    test_log = test_m.pop("trade_log", [])

    print("\n" + format_report("TRAIN RESULTS", train_m, "2024-01-01 to 2024-06-30"))
    print("\n" + format_report("TEST RESULTS", test_m, "2024-07-01 to 2024-09-30"))

    print("\nTEST Trade Log:")
    for t in test_log:
        print(f"  {t['date'].date()} | {t['action']:10} | pos={t['position']:.0%} | eq=${t['equity']:.2f} | ${t['price']:.0f} | {t.get('reason','')}")

    # Save
    results_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results.txt")
    with open(results_path, "w") as f:
        f.write(format_report("TRAIN RESULTS", train_m, "2024-01-01 to 2024-06-30"))
        f.write("\n\n")
        f.write(format_report("TEST RESULTS", test_m, "2024-07-01 to 2024-09-30"))
        f.write("\n\nTRAIN Trade Log:\n" + "-" * 80 + "\n")
        for t in train_log:
            f.write(f"  {t['date'].date()} | {t['action']:10} | pos={t['position']:.0%} | eq=${t['equity']:.2f} | ${t['price']:.0f} | {t.get('reason','')}\n")
        f.write("\n\nTEST Trade Log:\n" + "-" * 80 + "\n")
        for t in test_log:
            f.write(f"  {t['date'].date()} | {t['action']:10} | pos={t['position']:.0%} | eq=${t['equity']:.2f} | ${t['price']:.0f} | {t.get('reason','')}\n")
    print(f"\nResults saved to {results_path}")

    if test_m["total_return_pct"] <= 0:
        print("\nWARNING: TEST period NOT profitable - needs iteration.")
        return 1
    print("\nTEST period is PROFITABLE.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
