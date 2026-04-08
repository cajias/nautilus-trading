"""
Agent 5 — Round 4: Tournament-Selected Simple Strategy
=======================================================
Philosophy: Run 3 dead-simple sub-strategies on TRAIN, pick the best, deploy on TEST.
Sub-strategies:
  1. EMA Trend Follower (fast/slow crossover with ATR filter)
  2. Bollinger Band Mean Reversion (buy low band, sell high band)
  3. Momentum Breakout (N-day high breakout with volume confirm)

All use daily BTC bars, target 3-8 trades, 0.1% fees.
"""

import datetime as dt
import json
import os
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd
import requests


# ─── Data fetching ───────────────────────────────────────────────────────────

def fetch_btc_daily(start: str, end: str) -> pd.DataFrame:
    """Fetch BTC/USDT daily klines from Binance public API."""
    url = "https://api.binance.com/api/v3/klines"
    all_klines = []
    start_ms = int(pd.Timestamp(start).timestamp() * 1000)
    end_ms = int(pd.Timestamp(end).timestamp() * 1000)

    while start_ms < end_ms:
        params = {
            "symbol": "BTCUSDT",
            "interval": "1d",
            "startTime": start_ms,
            "endTime": end_ms,
            "limit": 1000,
        }
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if not data:
            break
        all_klines.extend(data)
        start_ms = data[-1][0] + 1

    df = pd.DataFrame(all_klines, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades", "taker_buy_base",
        "taker_buy_quote", "ignore",
    ])
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = df[c].astype(float)
    df["date"] = pd.to_datetime(df["open_time"], unit="ms")
    df = df.set_index("date").sort_index()
    return df


# ─── Position tracker ────────────────────────────────────────────────────────

FEE_RATE = 0.001  # 0.1%


@dataclass
class Position:
    side: Optional[str] = None  # "long" or None
    entry_price: float = 0.0
    size_usd: float = 0.0


@dataclass
class BacktestState:
    capital: float = 1000.0
    position: Position = field(default_factory=Position)
    trade_log: list = field(default_factory=list)
    equity_curve: list = field(default_factory=list)
    peak_equity: float = 1000.0
    max_drawdown_pct: float = 0.0

    def equity(self, price: float) -> float:
        if self.position.side == "long":
            pnl = (price / self.position.entry_price - 1) * self.position.size_usd
            return self.capital + self.position.size_usd + pnl
        return self.capital

    def buy(self, price: float, date, fraction: float = 0.95):
        if self.position.side is not None:
            return
        size = self.capital * fraction
        fee = size * FEE_RATE
        self.capital -= (size + fee)
        self.position = Position(side="long", entry_price=price, size_usd=size)
        self.trade_log.append({"date": str(date), "action": "BUY", "price": price, "size_usd": size, "fee": fee})

    def sell(self, price: float, date):
        if self.position.side != "long":
            return
        pnl = (price / self.position.entry_price - 1) * self.position.size_usd
        fee = (self.position.size_usd + pnl) * FEE_RATE
        self.capital += self.position.size_usd + pnl - fee
        self.trade_log.append({
            "date": str(date), "action": "SELL", "price": price,
            "pnl": round(pnl - fee, 2), "fee": round(fee, 2),
        })
        self.position = Position()

    def update_drawdown(self, price: float):
        eq = self.equity(price)
        self.equity_curve.append(eq)
        if eq > self.peak_equity:
            self.peak_equity = eq
        dd = (self.peak_equity - eq) / self.peak_equity * 100
        if dd > self.max_drawdown_pct:
            self.max_drawdown_pct = dd


# ─── Sub-strategy 1: EMA Trend Follower ──────────────────────────────────────

def run_ema_trend(df: pd.DataFrame, state: BacktestState,
                  fast: int = 10, slow: int = 30, atr_period: int = 14) -> BacktestState:
    """Buy when fast EMA crosses above slow EMA, sell on cross below. ATR stop."""
    df = df.copy()
    df["ema_fast"] = df["close"].ewm(span=fast, adjust=False).mean()
    df["ema_slow"] = df["close"].ewm(span=slow, adjust=False).mean()
    df["atr"] = (df["high"] - df["low"]).rolling(atr_period).mean()

    for i in range(max(slow, atr_period) + 1, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i - 1]
        price = row["close"]
        date = df.index[i]

        # Buy signal: fast crosses above slow
        if prev["ema_fast"] <= prev["ema_slow"] and row["ema_fast"] > row["ema_slow"]:
            state.buy(price, date)

        # Sell signal: fast crosses below slow OR ATR stop
        elif state.position.side == "long":
            if row["ema_fast"] < row["ema_slow"]:
                state.sell(price, date)
            elif price < state.position.entry_price - 2 * row["atr"]:
                state.sell(price, date)

        state.update_drawdown(price)

    # Force close at end
    if state.position.side == "long":
        state.sell(df.iloc[-1]["close"], df.index[-1])
    return state


# ─── Sub-strategy 2: Bollinger Band Mean Reversion ───────────────────────────

def run_bb_reversion(df: pd.DataFrame, state: BacktestState,
                     period: int = 20, num_std: float = 2.0) -> BacktestState:
    """Buy at lower band, sell at upper band or middle."""
    df = df.copy()
    df["sma"] = df["close"].rolling(period).mean()
    df["std"] = df["close"].rolling(period).std()
    df["upper"] = df["sma"] + num_std * df["std"]
    df["lower"] = df["sma"] - num_std * df["std"]

    for i in range(period + 1, len(df)):
        row = df.iloc[i]
        price = row["close"]
        date = df.index[i]

        if state.position.side is None and price <= row["lower"]:
            state.buy(price, date)
        elif state.position.side == "long":
            if price >= row["upper"] or price >= row["sma"]:
                state.sell(price, date)
            # Stop loss: 3% below entry
            elif price < state.position.entry_price * 0.97:
                state.sell(price, date)

        state.update_drawdown(price)

    if state.position.side == "long":
        state.sell(df.iloc[-1]["close"], df.index[-1])
    return state


# ─── Sub-strategy 3: Momentum Breakout ───────────────────────────────────────

def run_momentum_breakout(df: pd.DataFrame, state: BacktestState,
                          lookback: int = 20, exit_period: int = 10) -> BacktestState:
    """Buy on N-day high breakout, sell on exit_period low or trailing stop."""
    df = df.copy()
    df["highest"] = df["high"].rolling(lookback).max()
    df["lowest_exit"] = df["low"].rolling(exit_period).min()

    for i in range(lookback + 1, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i - 1]
        price = row["close"]
        date = df.index[i]

        # Buy: close breaks above previous N-day high
        if state.position.side is None and price > prev["highest"]:
            state.buy(price, date)

        # Sell: close breaks below exit_period low
        elif state.position.side == "long":
            if price < prev["lowest_exit"]:
                state.sell(price, date)
            # Trailing stop: 5% from peak
            elif price < state.position.entry_price * 0.95:
                state.sell(price, date)

        state.update_drawdown(price)

    if state.position.side == "long":
        state.sell(df.iloc[-1]["close"], df.index[-1])
    return state


# ─── Tournament: grid search light configs ───────────────────────────────────

STRATEGY_CONFIGS = {
    "ema_trend": [
        {"fast": 8, "slow": 21, "atr_period": 14},
        {"fast": 10, "slow": 30, "atr_period": 14},
        {"fast": 12, "slow": 26, "atr_period": 14},
        {"fast": 5, "slow": 20, "atr_period": 10},
        {"fast": 7, "slow": 25, "atr_period": 14},
    ],
    "bb_reversion": [
        {"period": 20, "num_std": 2.0},
        {"period": 20, "num_std": 1.5},
        {"period": 15, "num_std": 2.0},
        {"period": 25, "num_std": 2.0},
        {"period": 20, "num_std": 2.5},
    ],
    "momentum_breakout": [
        {"lookback": 20, "exit_period": 10},
        {"lookback": 15, "exit_period": 7},
        {"lookback": 30, "exit_period": 15},
        {"lookback": 25, "exit_period": 10},
        {"lookback": 10, "exit_period": 5},
    ],
}

STRATEGY_RUNNERS = {
    "ema_trend": run_ema_trend,
    "bb_reversion": run_bb_reversion,
    "momentum_breakout": run_momentum_breakout,
}


def compute_metrics(state: BacktestState, initial_capital: float = 1000.0) -> dict:
    eq = state.equity_curve
    if not eq:
        return {"final_equity": initial_capital, "total_return_pct": 0, "sharpe_ratio": 0,
                "max_drawdown_pct": 0, "num_trades": 0, "win_rate": 0}

    final = eq[-1]
    ret_pct = (final - initial_capital) / initial_capital * 100

    # Daily returns for Sharpe
    eq_arr = np.array(eq)
    if len(eq_arr) > 1:
        daily_rets = np.diff(eq_arr) / eq_arr[:-1]
        sharpe = (np.mean(daily_rets) / (np.std(daily_rets) + 1e-10)) * np.sqrt(365)
    else:
        sharpe = 0.0

    sells = [t for t in state.trade_log if t["action"] == "SELL"]
    wins = sum(1 for t in sells if t.get("pnl", 0) > 0)
    win_rate = wins / len(sells) * 100 if sells else 0

    return {
        "final_equity": round(final, 2),
        "total_return_pct": round(ret_pct, 2),
        "sharpe_ratio": round(sharpe, 4),
        "max_drawdown_pct": round(state.max_drawdown_pct, 2),
        "num_trades": len(sells),
        "win_rate": round(win_rate, 1),
        "trade_log": state.trade_log,
    }


def run_tournament(df_train: pd.DataFrame, initial_capital: float = 1000.0) -> tuple:
    """Run all configs on training data, return best (strategy_name, config, metrics)."""
    best = None
    best_score = -999

    for strat_name, configs in STRATEGY_CONFIGS.items():
        runner = STRATEGY_RUNNERS[strat_name]
        for cfg in configs:
            state = BacktestState(capital=initial_capital, peak_equity=initial_capital)
            try:
                state = runner(df_train, state, **cfg)
            except Exception as e:
                print(f"  FAILED {strat_name} {cfg}: {e}")
                continue
            metrics = compute_metrics(state, initial_capital)

            # Score: return_pct weighted by sharpe, penalize drawdown
            score = metrics["total_return_pct"] * 0.5 + metrics["sharpe_ratio"] * 20 - metrics["max_drawdown_pct"] * 0.3
            if metrics["num_trades"] == 0:
                score = -999

            print(f"  {strat_name} {cfg} -> ret={metrics['total_return_pct']:.1f}% "
                  f"sharpe={metrics['sharpe_ratio']:.2f} dd={metrics['max_drawdown_pct']:.1f}% "
                  f"trades={metrics['num_trades']} score={score:.2f}")

            if score > best_score:
                best_score = score
                best = (strat_name, cfg, metrics)

    return best


def run_backtest(start: str, end: str, initial_capital: float = 1000.0,
                 strategy_name: str = None, strategy_config: dict = None) -> dict:
    """Run a single backtest with specified or auto-selected strategy."""
    df = fetch_btc_daily(start, end)
    print(f"Data: {df.index[0].date()} to {df.index[-1].date()}, {len(df)} bars")

    if strategy_name is None or strategy_config is None:
        raise ValueError("Must specify strategy_name and strategy_config")

    runner = STRATEGY_RUNNERS[strategy_name]
    state = BacktestState(capital=initial_capital, peak_equity=initial_capital)
    state = runner(df, state, **strategy_config)
    return compute_metrics(state, initial_capital)


def main():
    TRAIN_START, TRAIN_END = "2024-10-01", "2025-03-31"
    TEST_START, TEST_END = "2025-04-01", "2025-06-30"

    print("=" * 60)
    print("PHASE 1: Tournament on TRAIN data")
    print("=" * 60)
    df_train = fetch_btc_daily(TRAIN_START, TRAIN_END)
    print(f"Train data: {df_train.index[0].date()} to {df_train.index[-1].date()}, {len(df_train)} bars\n")

    best = run_tournament(df_train)
    if best is None:
        print("ERROR: No viable strategy found!")
        return

    strat_name, strat_cfg, train_metrics = best
    print(f"\n*** WINNER: {strat_name} with config {strat_cfg} ***")
    print(f"    Train return: {train_metrics['total_return_pct']:.2f}%")
    print(f"    Sharpe: {train_metrics['sharpe_ratio']:.2f}")
    print(f"    Max DD: {train_metrics['max_drawdown_pct']:.2f}%")
    print(f"    Trades: {train_metrics['num_trades']}")

    print("\n" + "=" * 60)
    print("PHASE 2: Validate on TEST data")
    print("=" * 60)
    test_metrics = run_backtest(TEST_START, TEST_END, initial_capital=1000.0,
                                strategy_name=strat_name, strategy_config=strat_cfg)

    print(f"\nTEST Results ({strat_name} {strat_cfg}):")
    print(f"  Final equity: ${test_metrics['final_equity']:.2f}")
    print(f"  Return: {test_metrics['total_return_pct']:.2f}%")
    print(f"  Sharpe: {test_metrics['sharpe_ratio']:.2f}")
    print(f"  Max DD: {test_metrics['max_drawdown_pct']:.2f}%")
    print(f"  Trades: {test_metrics['num_trades']}")
    print(f"  Win rate: {test_metrics['win_rate']:.1f}%")

    # Save results
    results = {
        "strategy": strat_name,
        "config": strat_cfg,
        "train": train_metrics,
        "test": test_metrics,
    }
    # Remove trade_log from top-level for readability
    results_file = os.path.join(os.path.dirname(__file__), "results.txt")
    with open(results_file, "w") as f:
        f.write(f"Agent 5 — Round 4 Results\n")
        f.write(f"========================\n\n")
        f.write(f"Selected Strategy: {strat_name}\n")
        f.write(f"Config: {json.dumps(strat_cfg)}\n\n")
        f.write(f"TRAIN ({TRAIN_START} to {TRAIN_END}):\n")
        f.write(f"  Final equity: ${train_metrics['final_equity']:.2f}\n")
        f.write(f"  Return: {train_metrics['total_return_pct']:.2f}%\n")
        f.write(f"  Sharpe: {train_metrics['sharpe_ratio']:.4f}\n")
        f.write(f"  Max DD: {train_metrics['max_drawdown_pct']:.2f}%\n")
        f.write(f"  Trades: {train_metrics['num_trades']}\n")
        f.write(f"  Win rate: {train_metrics['win_rate']:.1f}%\n\n")
        f.write(f"TEST ({TEST_START} to {TEST_END}):\n")
        f.write(f"  Final equity: ${test_metrics['final_equity']:.2f}\n")
        f.write(f"  Return: {test_metrics['total_return_pct']:.2f}%\n")
        f.write(f"  Sharpe: {test_metrics['sharpe_ratio']:.4f}\n")
        f.write(f"  Max DD: {test_metrics['max_drawdown_pct']:.2f}%\n")
        f.write(f"  Trades: {test_metrics['num_trades']}\n")
        f.write(f"  Win rate: {test_metrics['win_rate']:.1f}%\n\n")
        f.write(f"TRAIN Trade Log:\n")
        for t in train_metrics.get("trade_log", []):
            f.write(f"  {t}\n")
        f.write(f"\nTEST Trade Log:\n")
        for t in test_metrics.get("trade_log", []):
            f.write(f"  {t}\n")

    print(f"\nResults saved to {results_file}")
    return results


if __name__ == "__main__":
    main()
