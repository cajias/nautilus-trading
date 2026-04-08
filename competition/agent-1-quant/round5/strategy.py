"""
Agent 1 -- Round 5: RSI Dip-Buyer with Walk-Forward Tournament
Refined from R4 winner (+15.39%). Uses Binance klines, 0.1% fees.
Train: Jan-Jun 2025, Test: Jul-Sep 2025.
"""

import itertools
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests
import numpy as np
import pandas as pd


# -- Data fetching -----------------------------------------------------------

def fetch_klines(
    symbol: str = "BTCUSDT",
    interval: str = "1d",
    start: str = "2024-07-01",
    end: str = "2025-10-01",
) -> pd.DataFrame:
    """Fetch Binance klines via public API."""
    url = "https://api.binance.com/api/v3/klines"
    start_ms = int(pd.Timestamp(start).timestamp() * 1000)
    end_ms = int(pd.Timestamp(end).timestamp() * 1000)
    all_rows: list = []

    while start_ms < end_ms:
        params = {
            "symbol": symbol,
            "interval": interval,
            "startTime": start_ms,
            "endTime": end_ms,
            "limit": 1000,
        }
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if not data:
            break
        all_rows.extend(data)
        start_ms = data[-1][0] + 1
        time.sleep(0.1)

    df = pd.DataFrame(all_rows, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_vol", "trades", "taker_buy_base",
        "taker_buy_quote", "ignore",
    ])
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)
    df["date"] = pd.to_datetime(df["open_time"], unit="ms")
    df = df.set_index("date").sort_index()
    return df


# -- Indicators --------------------------------------------------------------

def compute_rsi(series: pd.Series, period: int) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high_low = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift(1)).abs()
    low_close = (df["low"] - df["close"].shift(1)).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.rolling(period).mean()


# -- Strategy simulation -----------------------------------------------------

FEE_RATE = 0.001  # 0.1% per trade


@dataclass
class TradeRecord:
    entry_date: str
    exit_date: str
    side: str
    entry_price: float
    exit_price: float
    pnl_pct: float
    pnl_usd: float


def simulate_rsi_dip(
    df: pd.DataFrame,
    rsi_period: int = 14,
    rsi_buy: float = 30.0,
    rsi_sell: float = 70.0,
    atr_period: int = 14,
    atr_sl_mult: float = 2.0,
    use_trailing: bool = True,
    initial_capital: float = 1000.0,
    trade_start: str | None = None,
) -> dict[str, Any]:
    """
    Simulate RSI dip-buying strategy with ATR-based stop-loss.

    df: Full OHLCV data (includes warmup period for indicators).
    trade_start: If set, only allow new entries on or after this date.
                 Indicators are computed on the full df for warmup.
    """
    close = df["close"]
    rsi = compute_rsi(close, rsi_period)
    atr = compute_atr(df, atr_period)

    trade_start_ts = pd.Timestamp(trade_start) if trade_start else None

    cash = initial_capital
    btc = 0.0
    entry_price = 0.0
    stop_loss = 0.0
    highest_since_entry = 0.0
    entry_date = ""
    trades: list[TradeRecord] = []
    equity_curve: list[float] = []

    warmup = max(rsi_period, atr_period) + 1

    for i in range(warmup, len(df)):
        date_ts = df.index[i]
        date_str = str(date_ts.date())
        price = close.iloc[i]
        cur_rsi = rsi.iloc[i]
        cur_atr = atr.iloc[i]

        if np.isnan(cur_rsi) or np.isnan(cur_atr):
            equity_curve.append(cash + btc * price)
            continue

        # Exit logic (always active if we hold)
        if btc > 0:
            if use_trailing:
                highest_since_entry = max(highest_since_entry, price)
                trailing_stop = highest_since_entry - atr_sl_mult * cur_atr
                effective_stop = max(stop_loss, trailing_stop)
            else:
                effective_stop = stop_loss

            if price <= effective_stop or cur_rsi >= rsi_sell:
                sell_proceeds = btc * price * (1 - FEE_RATE)
                buy_cost = btc * entry_price * (1 + FEE_RATE)
                trade_pnl = sell_proceeds - buy_cost
                pnl_pct = (sell_proceeds / buy_cost - 1) * 100

                cash += sell_proceeds
                trades.append(TradeRecord(
                    entry_date=entry_date,
                    exit_date=date_str,
                    side="LONG",
                    entry_price=entry_price,
                    exit_price=price,
                    pnl_pct=pnl_pct,
                    pnl_usd=trade_pnl,
                ))
                btc = 0.0

        # Entry logic (only when flat, and only after trade_start)
        elif cur_rsi <= rsi_buy:
            if trade_start_ts is None or date_ts >= trade_start_ts:
                alloc = cash * 0.95
                buy_cost_per_btc = price * (1 + FEE_RATE)
                btc = alloc / buy_cost_per_btc
                cash -= btc * price * (1 + FEE_RATE)
                entry_price = price
                stop_loss = price - atr_sl_mult * cur_atr
                highest_since_entry = price
                entry_date = date_str

        # Mark to market
        equity_curve.append(cash + btc * price)

    # Close any open position at end
    if btc > 0:
        price = close.iloc[-1]
        date_str = str(df.index[-1].date())
        sell_proceeds = btc * price * (1 - FEE_RATE)
        buy_cost = btc * entry_price * (1 + FEE_RATE)
        trade_pnl = sell_proceeds - buy_cost
        pnl_pct = (sell_proceeds / buy_cost - 1) * 100
        cash += sell_proceeds
        btc = 0.0
        trades.append(TradeRecord(
            entry_date=entry_date,
            exit_date=date_str,
            side="LONG",
            entry_price=entry_price,
            exit_price=price,
            pnl_pct=pnl_pct,
            pnl_usd=trade_pnl,
        ))
        if equity_curve:
            equity_curve[-1] = cash

    final_equity = cash
    ec = pd.Series(equity_curve) if equity_curve else pd.Series([initial_capital])
    returns = ec.pct_change().dropna()
    sharpe = float(
        returns.mean() / returns.std() * np.sqrt(365)
        if len(returns) > 1 and returns.std() > 0
        else 0.0
    )
    peak = ec.cummax()
    drawdown = (ec - peak) / peak
    max_dd = float(drawdown.min() * 100)
    wins = [t for t in trades if t.pnl_usd > 0]
    win_rate = len(wins) / len(trades) * 100 if trades else 0.0

    return {
        "final_equity": round(final_equity, 2),
        "total_return_pct": round((final_equity / initial_capital - 1) * 100, 2),
        "sharpe_ratio": round(sharpe, 4),
        "max_drawdown_pct": round(max_dd, 2),
        "num_trades": len(trades),
        "win_rate": round(win_rate, 2),
        "trade_log": [
            {
                "entry_date": t.entry_date,
                "exit_date": t.exit_date,
                "side": t.side,
                "entry_price": round(t.entry_price, 2),
                "exit_price": round(t.exit_price, 2),
                "pnl_pct": round(t.pnl_pct, 2),
                "pnl_usd": round(t.pnl_usd, 2),
            }
            for t in trades
        ],
    }


# -- Walk-forward tournament -------------------------------------------------

def walk_forward_tournament(
    df: pd.DataFrame,
    train_start: str,
    train_end: str,
    initial_capital: float = 1000.0,
) -> dict[str, Any]:
    """Run parameter tournament on training data with walk-forward splits."""
    param_grid = {
        "rsi_period": [10, 14, 20],
        "rsi_buy": [25, 30, 35],
        "rsi_sell": [55, 60, 65, 70, 75],
        "atr_sl_mult": [1.5, 2.0, 2.5, 3.0],
        "use_trailing": [True, False],
    }

    keys = list(param_grid.keys())
    combos = list(itertools.product(*param_grid.values()))

    # Walk-forward: split training into 3 non-overlapping folds
    train_df = df[train_start:train_end]
    n = len(train_df)
    fold_size = n // 3

    best_score = -999.0
    best_params: dict[str, Any] = {}

    for combo in combos:
        params = dict(zip(keys, combo))
        fold_returns = []

        for fold in range(3):
            start_idx = fold * fold_size
            end_idx = (fold + 1) * fold_size if fold < 2 else n
            fold_df = train_df.iloc[start_idx:end_idx]

            if len(fold_df) < 30:
                continue

            result = simulate_rsi_dip(fold_df, initial_capital=initial_capital, **params)
            fold_returns.append(result["total_return_pct"])

        if not fold_returns:
            continue

        avg_ret = float(np.mean(fold_returns))
        std_ret = float(np.std(fold_returns)) if len(fold_returns) > 1 else 0.0
        # Score: average return penalized by volatility
        score = avg_ret - 0.5 * std_ret

        if score > best_score:
            best_score = score
            best_params = params

    return best_params


# -- Public API --------------------------------------------------------------

def run_backtest(
    start: str,
    end: str,
    initial_capital: float = 1000.0,
) -> dict[str, Any]:
    """
    Run backtest for given period. Fetches data with warmup,
    optimizes params on a prior 6-month window, then runs on [start, end].
    """
    # Fetch data: 6 months before start for optimization + warmup
    opt_start = (pd.Timestamp(start) - pd.Timedelta(days=210)).strftime("%Y-%m-%d")
    warmup_start = (pd.Timestamp(opt_start) - pd.Timedelta(days=60)).strftime("%Y-%m-%d")
    df = fetch_klines(start=warmup_start, end=end)

    # Optimize on the 6 months before the test period
    opt_end = (pd.Timestamp(start) - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    best_params = walk_forward_tournament(
        df, train_start=opt_start, train_end=opt_end, initial_capital=initial_capital
    )

    if not best_params:
        # Fallback to R4 winner params
        best_params = {
            "rsi_period": 10,
            "rsi_buy": 35,
            "rsi_sell": 55,
            "atr_sl_mult": 2.5,
            "use_trailing": True,
        }

    # Run on full df but only allow entries from start onwards
    result = simulate_rsi_dip(
        df[:end],
        initial_capital=initial_capital,
        trade_start=start,
        **best_params,
    )
    result["optimized_params"] = best_params
    return result


# -- Main --------------------------------------------------------------------

def main():
    print("=" * 60)
    print("Agent 1 -- Round 5: RSI Dip-Buyer Tournament")
    print("=" * 60)

    # Fetch all data
    print("\n[1] Fetching BTC/USDT daily klines...")
    df = fetch_klines(start="2024-07-01", end="2025-10-01")
    print(f"    Got {len(df)} candles: {df.index[0].date()} to {df.index[-1].date()}")

    # Tournament on TRAIN period (optimizes using Jan-Jun 2025)
    print("\n[2] Walk-forward tournament on TRAIN (Jan-Jun 2025)...")
    best_params = walk_forward_tournament(
        df, train_start="2025-01-01", train_end="2025-06-30", initial_capital=1000.0,
    )
    print(f"    Best params: {best_params}")

    # TRAIN backtest: entries only Jan-Jun 2025
    print("\n[3] TRAIN backtest (Jan-Jun 2025)...")
    train_result = simulate_rsi_dip(
        df[:"2025-06-30"],
        initial_capital=1000.0,
        trade_start="2025-01-01",
        **best_params,
    )
    print(f"    Return: {train_result['total_return_pct']}%")
    print(f"    Trades: {train_result['num_trades']}, Win rate: {train_result['win_rate']}%")
    print(f"    Sharpe: {train_result['sharpe_ratio']}, Max DD: {train_result['max_drawdown_pct']}%")

    # TEST backtest: entries only Jul-Sep 2025
    print("\n[4] TEST backtest (Jul-Sep 2025)...")
    test_result = simulate_rsi_dip(
        df[:"2025-09-30"],
        initial_capital=1000.0,
        trade_start="2025-07-01",
        **best_params,
    )
    print(f"    Return: {test_result['total_return_pct']}%")
    print(f"    Trades: {test_result['num_trades']}, Win rate: {test_result['win_rate']}%")
    print(f"    Sharpe: {test_result['sharpe_ratio']}, Max DD: {test_result['max_drawdown_pct']}%")

    # Save results
    output_dir = Path(__file__).parent
    results = {
        "agent": "Agent 1 -- Quantitative Trader",
        "round": 5,
        "strategy": "RSI Dip-Buyer with Walk-Forward Tournament + ATR trailing stop",
        "best_params": best_params,
        "train": {
            "period": "2025-01-01 to 2025-06-30",
            **{k: train_result[k] for k in [
                "final_equity", "total_return_pct", "sharpe_ratio",
                "max_drawdown_pct", "num_trades", "win_rate", "trade_log",
            ]},
        },
        "test": {
            "period": "2025-07-01 to 2025-09-30",
            **{k: test_result[k] for k in [
                "final_equity", "total_return_pct", "sharpe_ratio",
                "max_drawdown_pct", "num_trades", "win_rate", "trade_log",
            ]},
        },
    }

    results_path = output_dir / "results.txt"
    with open(results_path, "w") as f:
        f.write(json.dumps(results, indent=2))
    print(f"\n[5] Results saved to {results_path}")

    print("\n-- TRAIN Trade Log --")
    for t in train_result["trade_log"]:
        print(f"  {t['entry_date']} -> {t['exit_date']}: {t['pnl_pct']:+.2f}% (${t['pnl_usd']:+.2f})")

    print("\n-- TEST Trade Log --")
    for t in test_result["trade_log"]:
        print(f"  {t['entry_date']} -> {t['exit_date']}: {t['pnl_pct']:+.2f}% (${t['pnl_usd']:+.2f})")


if __name__ == "__main__":
    main()
