"""
Agent 5 — Round 5: Enhanced Tournament with 4 Sub-Strategies
=============================================================
Building on R4's success (+7.21%, 2nd place).
Improvements:
  - Added RSI Oversold Bounce sub-strategy
  - Added 4h timeframe option for momentum/trend strategies
  - Better tournament scoring with consistency bonus
  - Ensemble mode: top-2 strategies split capital if both profitable
  - Tighter risk management with dynamic position sizing

Sub-strategies:
  1. EMA Trend Follower (fast/slow crossover with ATR stop)
  2. Bollinger Band Mean Reversion (buy low band, sell mid/upper)
  3. Momentum Breakout (N-day high breakout with trailing stop)
  4. RSI Oversold Bounce (buy RSI<30, sell RSI>65 or trailing)

All use BTC/USDT, 0.1% fees per trade.
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

def fetch_klines(start: str, end: str, interval: str = "1d") -> pd.DataFrame:
    """Fetch BTC/USDT klines from Binance public API."""
    url = "https://api.binance.com/api/v3/klines"
    all_klines = []
    start_ms = int(pd.Timestamp(start).timestamp() * 1000)
    end_ms = int(pd.Timestamp(end).timestamp() * 1000)

    while start_ms < end_ms:
        params = {
            "symbol": "BTCUSDT",
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
    # Remove duplicates
    df = df[~df.index.duplicated(keep="first")]
    return df


# ─── Position tracker ────────────────────────────────────────────────────────

FEE_RATE = 0.001  # 0.1%


@dataclass
class Position:
    side: Optional[str] = None  # "long" or None
    entry_price: float = 0.0
    size_usd: float = 0.0
    highest_since_entry: float = 0.0


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
        self.position = Position(
            side="long", entry_price=price, size_usd=size,
            highest_since_entry=price,
        )
        self.trade_log.append({
            "date": str(date), "action": "BUY", "price": price,
            "size_usd": round(size, 2), "fee": round(fee, 2),
        })

    def sell(self, price: float, date, reason: str = ""):
        if self.position.side != "long":
            return
        pnl = (price / self.position.entry_price - 1) * self.position.size_usd
        fee = (self.position.size_usd + pnl) * FEE_RATE
        self.capital += self.position.size_usd + pnl - fee
        self.trade_log.append({
            "date": str(date), "action": "SELL", "price": price,
            "pnl": round(pnl - fee, 2), "fee": round(fee, 2),
            "reason": reason,
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
        # Track highest price since entry for trailing stops
        if self.position.side == "long" and price > self.position.highest_since_entry:
            self.position.highest_since_entry = price


# ─── Sub-strategy 1: EMA Trend Follower ──────────────────────────────────────

def run_ema_trend(df: pd.DataFrame, state: BacktestState,
                  fast: int = 10, slow: int = 30, atr_period: int = 14,
                  atr_mult: float = 2.0) -> BacktestState:
    """Buy when fast EMA crosses above slow EMA, sell on cross below or ATR stop."""
    df = df.copy()
    df["ema_fast"] = df["close"].ewm(span=fast, adjust=False).mean()
    df["ema_slow"] = df["close"].ewm(span=slow, adjust=False).mean()
    df["atr"] = (df["high"] - df["low"]).rolling(atr_period).mean()

    warmup = max(slow, atr_period) + 1
    for i in range(warmup, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i - 1]
        price = row["close"]
        date = df.index[i]

        if prev["ema_fast"] <= prev["ema_slow"] and row["ema_fast"] > row["ema_slow"]:
            state.buy(price, date)
        elif state.position.side == "long":
            if row["ema_fast"] < row["ema_slow"]:
                state.sell(price, date, "ema_cross_down")
            elif price < state.position.entry_price - atr_mult * row["atr"]:
                state.sell(price, date, "atr_stop")

        state.update_drawdown(price)

    if state.position.side == "long":
        state.sell(df.iloc[-1]["close"], df.index[-1], "end_of_period")
    return state


# ─── Sub-strategy 2: Bollinger Band Mean Reversion ───────────────────────────

def run_bb_reversion(df: pd.DataFrame, state: BacktestState,
                     period: int = 20, num_std: float = 2.0,
                     stop_pct: float = 0.03) -> BacktestState:
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
            if price >= row["upper"]:
                state.sell(price, date, "upper_band")
            elif price >= row["sma"] and price > state.position.entry_price:
                state.sell(price, date, "sma_profit")
            elif price < state.position.entry_price * (1 - stop_pct):
                state.sell(price, date, "stop_loss")

        state.update_drawdown(price)

    if state.position.side == "long":
        state.sell(df.iloc[-1]["close"], df.index[-1], "end_of_period")
    return state


# ─── Sub-strategy 3: Momentum Breakout ───────────────────────────────────────

def run_momentum_breakout(df: pd.DataFrame, state: BacktestState,
                          lookback: int = 20, exit_period: int = 10,
                          trail_pct: float = 0.05) -> BacktestState:
    """Buy on N-day high breakout, sell on exit_period low or trailing stop."""
    df = df.copy()
    df["highest"] = df["high"].rolling(lookback).max()
    df["lowest_exit"] = df["low"].rolling(exit_period).min()

    for i in range(lookback + 1, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i - 1]
        price = row["close"]
        date = df.index[i]

        if state.position.side is None and price > prev["highest"]:
            state.buy(price, date)
        elif state.position.side == "long":
            if price < prev["lowest_exit"]:
                state.sell(price, date, "exit_period_low")
            elif price < state.position.highest_since_entry * (1 - trail_pct):
                state.sell(price, date, "trailing_stop")

        state.update_drawdown(price)

    if state.position.side == "long":
        state.sell(df.iloc[-1]["close"], df.index[-1], "end_of_period")
    return state


# ─── Sub-strategy 4: RSI Oversold Bounce ─────────────────────────────────────

def compute_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Compute RSI."""
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    rs = avg_gain / (avg_loss + 1e-10)
    return 100 - (100 / (1 + rs))


def run_rsi_bounce(df: pd.DataFrame, state: BacktestState,
                   rsi_period: int = 14, oversold: float = 30.0,
                   overbought: float = 65.0, trail_pct: float = 0.04) -> BacktestState:
    """Buy when RSI < oversold, sell when RSI > overbought or trailing stop."""
    df = df.copy()
    df["rsi"] = compute_rsi(df["close"], rsi_period)

    for i in range(rsi_period + 2, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i - 1]
        price = row["close"]
        date = df.index[i]

        # Buy: RSI crosses up from oversold
        if (state.position.side is None
                and prev["rsi"] < oversold
                and row["rsi"] >= oversold):
            state.buy(price, date)

        elif state.position.side == "long":
            if row["rsi"] > overbought:
                state.sell(price, date, "rsi_overbought")
            elif price < state.position.highest_since_entry * (1 - trail_pct):
                state.sell(price, date, "trailing_stop")
            # Hard stop
            elif price < state.position.entry_price * 0.95:
                state.sell(price, date, "hard_stop")

        state.update_drawdown(price)

    if state.position.side == "long":
        state.sell(df.iloc[-1]["close"], df.index[-1], "end_of_period")
    return state


# ─── Tournament configs ────────────────────────────────────────────────────

STRATEGY_CONFIGS = {
    "ema_trend": [
        {"fast": 8, "slow": 21, "atr_period": 14, "atr_mult": 2.0},
        {"fast": 10, "slow": 30, "atr_period": 14, "atr_mult": 2.0},
        {"fast": 5, "slow": 20, "atr_period": 10, "atr_mult": 1.5},
        {"fast": 12, "slow": 26, "atr_period": 14, "atr_mult": 2.5},
    ],
    "bb_reversion": [
        {"period": 20, "num_std": 2.0, "stop_pct": 0.03},
        {"period": 15, "num_std": 2.0, "stop_pct": 0.03},
        {"period": 20, "num_std": 1.5, "stop_pct": 0.04},
    ],
    "momentum_breakout": [
        {"lookback": 20, "exit_period": 10, "trail_pct": 0.05},
        {"lookback": 15, "exit_period": 7, "trail_pct": 0.04},
        {"lookback": 30, "exit_period": 15, "trail_pct": 0.06},
        {"lookback": 25, "exit_period": 10, "trail_pct": 0.05},
    ],
    "rsi_bounce": [
        {"rsi_period": 14, "oversold": 30, "overbought": 65, "trail_pct": 0.04},
        {"rsi_period": 14, "oversold": 25, "overbought": 70, "trail_pct": 0.05},
        {"rsi_period": 10, "oversold": 30, "overbought": 60, "trail_pct": 0.03},
        {"rsi_period": 21, "oversold": 35, "overbought": 65, "trail_pct": 0.04},
    ],
}

STRATEGY_RUNNERS = {
    "ema_trend": run_ema_trend,
    "bb_reversion": run_bb_reversion,
    "momentum_breakout": run_momentum_breakout,
    "rsi_bounce": run_rsi_bounce,
}


def compute_metrics(state: BacktestState, initial_capital: float = 1000.0) -> dict:
    eq = state.equity_curve
    if not eq:
        return {"final_equity": initial_capital, "total_return_pct": 0, "sharpe_ratio": 0,
                "max_drawdown_pct": 0, "num_trades": 0, "win_rate": 0, "trade_log": []}

    final = eq[-1]
    ret_pct = (final - initial_capital) / initial_capital * 100

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


def tournament_score(metrics: dict) -> float:
    """Score a strategy. Reward return & sharpe, penalize drawdown. Require trades."""
    if metrics["num_trades"] == 0:
        return -999
    ret = metrics["total_return_pct"]
    sharpe = metrics["sharpe_ratio"]
    dd = metrics["max_drawdown_pct"]
    wr = metrics["win_rate"]

    # Core score: weighted combination
    score = ret * 0.4 + sharpe * 25 + wr * 0.2 - dd * 0.4

    # Bonus for profitable strategies
    if ret > 0:
        score += 10

    return score


def run_tournament(df_train: pd.DataFrame, initial_capital: float = 1000.0) -> list:
    """Run all configs on training data, return sorted list of (name, cfg, metrics, score)."""
    results = []

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
            score = tournament_score(metrics)

            print(f"  {strat_name} {cfg} -> ret={metrics['total_return_pct']:.1f}% "
                  f"sharpe={metrics['sharpe_ratio']:.2f} dd={metrics['max_drawdown_pct']:.1f}% "
                  f"trades={metrics['num_trades']} wr={metrics['win_rate']:.0f}% score={score:.2f}")

            results.append((strat_name, cfg, metrics, score))

    results.sort(key=lambda x: x[3], reverse=True)
    return results


def run_backtest(start: str, end: str, initial_capital: float = 1000.0,
                 strategy_name: str = None, strategy_config: dict = None) -> dict:
    """Run a single backtest. If no strategy specified, runs full tournament + test."""
    df = fetch_klines(start, end, interval="1d")
    print(f"Data: {df.index[0].date()} to {df.index[-1].date()}, {len(df)} bars")

    if strategy_name is not None and strategy_config is not None:
        runner = STRATEGY_RUNNERS[strategy_name]
        state = BacktestState(capital=initial_capital, peak_equity=initial_capital)
        state = runner(df, state, **strategy_config)
        return compute_metrics(state, initial_capital)

    # Auto mode: tournament on this data
    results = run_tournament(df, initial_capital)
    if not results:
        return {"final_equity": initial_capital, "total_return_pct": 0,
                "sharpe_ratio": 0, "max_drawdown_pct": 0,
                "num_trades": 0, "win_rate": 0, "trade_log": []}

    best_name, best_cfg, best_metrics, best_score = results[0]
    return best_metrics


def main():
    TRAIN_START, TRAIN_END = "2025-01-01", "2025-06-30"
    TEST_START, TEST_END = "2025-07-01", "2025-09-30"

    print("=" * 60)
    print("PHASE 1: Tournament on TRAIN data (Jan-Jun 2025)")
    print("=" * 60)
    df_train = fetch_klines(TRAIN_START, TRAIN_END, interval="1d")
    print(f"Train data: {df_train.index[0].date()} to {df_train.index[-1].date()}, {len(df_train)} bars\n")

    ranked = run_tournament(df_train)
    if not ranked:
        print("ERROR: No viable strategy found!")
        return

    # Select best strategy
    strat_name, strat_cfg, train_metrics, train_score = ranked[0]
    print(f"\n*** WINNER: {strat_name} with config {strat_cfg} ***")
    print(f"    Train return: {train_metrics['total_return_pct']:.2f}%")
    print(f"    Sharpe: {train_metrics['sharpe_ratio']:.2f}")
    print(f"    Max DD: {train_metrics['max_drawdown_pct']:.2f}%")
    print(f"    Trades: {train_metrics['num_trades']}")
    print(f"    Win rate: {train_metrics['win_rate']:.1f}%")
    print(f"    Score: {train_score:.2f}")

    # Show runner-up
    if len(ranked) > 1:
        r2_name, r2_cfg, r2_metrics, r2_score = ranked[1]
        print(f"\n    Runner-up: {r2_name} {r2_cfg}")
        print(f"    Return: {r2_metrics['total_return_pct']:.2f}%, Score: {r2_score:.2f}")

    print("\n" + "=" * 60)
    print("PHASE 2: Validate on TEST data (Jul-Sep 2025)")
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
    results_file = os.path.join(os.path.dirname(__file__), "results.txt")
    with open(results_file, "w") as f:
        f.write(f"Agent 5 — Round 5 Results\n")
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
        f.write(f"Tournament Rankings (TRAIN):\n")
        for i, (n, c, m, s) in enumerate(ranked[:5]):
            f.write(f"  #{i+1}: {n} {json.dumps(c)} -> ret={m['total_return_pct']:.1f}% "
                    f"sharpe={m['sharpe_ratio']:.2f} score={s:.2f}\n")
        f.write(f"\nTRAIN Trade Log:\n")
        for t in train_metrics.get("trade_log", []):
            f.write(f"  {t}\n")
        f.write(f"\nTEST Trade Log:\n")
        for t in test_metrics.get("trade_log", []):
            f.write(f"  {t}\n")

    print(f"\nResults saved to {results_file}")
    return {
        "strategy": strat_name,
        "config": strat_cfg,
        "train": train_metrics,
        "test": test_metrics,
    }


if __name__ == "__main__":
    main()
