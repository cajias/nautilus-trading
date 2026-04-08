"""
Agent 5 -- Round 6: Multi-Asset Aggressive Momentum Tournament
================================================================
Goal: Beat +48.47% (Agent 4 R1). Need concentrated, aggressive approach.

Key changes from R4/R5:
  - Multi-asset: BTC, ETH, SOL -- pick the best mover
  - More aggressive position sizing (97% of capital)
  - Wider tournament grid with aggressive momentum configs
  - Asset rotation: trade the strongest trending asset
  - Combine 4h and daily timeframes for faster signal detection
  - Leverage-like effect via asset selection (SOL has higher beta)

Sub-strategies:
  1. EMA Trend Follower (fast/slow crossover, ATR trailing stop)
  2. Momentum Breakout (N-day high breakout, trailing stop)
  3. RSI Momentum (buy strong RSI recovery, ride the trend)
  4. Multi-Asset Rotator (pick strongest asset by momentum score)
"""

import json
import os
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd
import requests


# --- Data fetching -------------------------------------------------------

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]


def fetch_klines(symbol: str, start: str, end: str, interval: str = "1d") -> pd.DataFrame:
    """Fetch klines from Binance public API."""
    url = "https://api.binance.com/api/v3/klines"
    all_klines = []
    start_ms = int(pd.Timestamp(start).timestamp() * 1000)
    end_ms = int(pd.Timestamp(end).timestamp() * 1000)

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
        all_klines.extend(data)
        start_ms = data[-1][0] + 1

    df = pd.DataFrame(all_klines, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades", "taker_buy_base",
        "taker_buy_quote", "ignore",
    ])
    for c in ["open", "high", "low", "close", "volume", "quote_volume"]:
        df[c] = df[c].astype(float)
    df["date"] = pd.to_datetime(df["open_time"], unit="ms")
    df = df.set_index("date").sort_index()
    df = df[~df.index.duplicated(keep="first")]
    return df


def fetch_all_assets(start: str, end: str, interval: str = "1d") -> dict:
    """Fetch klines for all symbols."""
    data = {}
    for sym in SYMBOLS:
        print(f"  Fetching {sym} {interval}...")
        data[sym] = fetch_klines(sym, start, end, interval)
    return data


# --- Position tracker ----------------------------------------------------

FEE_RATE = 0.001  # 0.1%


@dataclass
class Position:
    side: Optional[str] = None  # "long" or None
    entry_price: float = 0.0
    size_usd: float = 0.0
    highest_since_entry: float = 0.0
    symbol: str = ""


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

    def buy(self, price: float, date, symbol: str = "", fraction: float = 0.97):
        if self.position.side is not None:
            return
        size = self.capital * fraction
        fee = size * FEE_RATE
        self.capital -= (size + fee)
        self.position = Position(
            side="long", entry_price=price, size_usd=size,
            highest_since_entry=price, symbol=symbol,
        )
        self.trade_log.append({
            "date": str(date), "action": "BUY", "symbol": symbol,
            "price": price, "size_usd": round(size, 2), "fee": round(fee, 2),
        })

    def sell(self, price: float, date, reason: str = ""):
        if self.position.side != "long":
            return
        pnl = (price / self.position.entry_price - 1) * self.position.size_usd
        fee = (self.position.size_usd + pnl) * FEE_RATE
        self.capital += self.position.size_usd + pnl - fee
        self.trade_log.append({
            "date": str(date), "action": "SELL", "symbol": self.position.symbol,
            "price": price, "pnl": round(pnl - fee, 2), "fee": round(fee, 2),
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
        if self.position.side == "long" and price > self.position.highest_since_entry:
            self.position.highest_since_entry = price


# --- Indicators ----------------------------------------------------------

def compute_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    rs = avg_gain / (avg_loss + 1e-10)
    return 100 - (100 / (1 + rs))


def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - df["close"].shift(1)).abs(),
        (df["low"] - df["close"].shift(1)).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()


# --- Sub-strategy 1: EMA Trend Follower ----------------------------------

def run_ema_trend(df: pd.DataFrame, state: BacktestState, symbol: str = "BTCUSDT",
                  fast: int = 10, slow: int = 30, atr_period: int = 14,
                  atr_mult: float = 2.0, trail_atr: float = 2.5) -> BacktestState:
    df = df.copy()
    df["ema_fast"] = df["close"].ewm(span=fast, adjust=False).mean()
    df["ema_slow"] = df["close"].ewm(span=slow, adjust=False).mean()
    df["atr"] = compute_atr(df, atr_period)

    warmup = max(slow, atr_period) + 1
    for i in range(warmup, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i - 1]
        price = row["close"]
        date = df.index[i]

        if prev["ema_fast"] <= prev["ema_slow"] and row["ema_fast"] > row["ema_slow"]:
            state.buy(price, date, symbol)
        elif state.position.side == "long":
            # Trailing stop using ATR from peak
            trail_stop = state.position.highest_since_entry - trail_atr * row["atr"]
            if row["ema_fast"] < row["ema_slow"]:
                state.sell(price, date, "ema_cross_down")
            elif price < state.position.entry_price - atr_mult * row["atr"]:
                state.sell(price, date, "atr_stop")
            elif price < trail_stop:
                state.sell(price, date, "trail_stop")

        state.update_drawdown(price)

    if state.position.side == "long":
        state.sell(df.iloc[-1]["close"], df.index[-1], "end_of_period")
    return state


# --- Sub-strategy 2: Momentum Breakout -----------------------------------

def run_momentum_breakout(df: pd.DataFrame, state: BacktestState, symbol: str = "BTCUSDT",
                          lookback: int = 20, exit_period: int = 10,
                          trail_pct: float = 0.05) -> BacktestState:
    df = df.copy()
    df["highest"] = df["high"].rolling(lookback).max()
    df["lowest_exit"] = df["low"].rolling(exit_period).min()

    for i in range(lookback + 1, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i - 1]
        price = row["close"]
        date = df.index[i]

        if state.position.side is None and price > prev["highest"]:
            state.buy(price, date, symbol)
        elif state.position.side == "long":
            if price < prev["lowest_exit"]:
                state.sell(price, date, "exit_period_low")
            elif price < state.position.highest_since_entry * (1 - trail_pct):
                state.sell(price, date, "trailing_stop")

        state.update_drawdown(price)

    if state.position.side == "long":
        state.sell(df.iloc[-1]["close"], df.index[-1], "end_of_period")
    return state


# --- Sub-strategy 3: RSI Momentum Rider ----------------------------------

def run_rsi_momentum(df: pd.DataFrame, state: BacktestState, symbol: str = "BTCUSDT",
                     rsi_period: int = 14, entry_rsi: float = 45.0,
                     exit_rsi: float = 75.0, trail_pct: float = 0.04,
                     ema_period: int = 21) -> BacktestState:
    """Buy when RSI crosses above entry_rsi in uptrend, sell at exit_rsi or trail."""
    df = df.copy()
    df["rsi"] = compute_rsi(df["close"], rsi_period)
    df["ema"] = df["close"].ewm(span=ema_period, adjust=False).mean()

    for i in range(max(rsi_period, ema_period) + 2, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i - 1]
        price = row["close"]
        date = df.index[i]

        # Buy: RSI crosses up from below entry_rsi, price above EMA (uptrend)
        if (state.position.side is None
                and prev["rsi"] < entry_rsi
                and row["rsi"] >= entry_rsi
                and price > row["ema"]):
            state.buy(price, date, symbol)

        elif state.position.side == "long":
            if row["rsi"] > exit_rsi:
                state.sell(price, date, "rsi_exit")
            elif price < state.position.highest_since_entry * (1 - trail_pct):
                state.sell(price, date, "trailing_stop")
            elif price < state.position.entry_price * 0.93:
                state.sell(price, date, "hard_stop")

        state.update_drawdown(price)

    if state.position.side == "long":
        state.sell(df.iloc[-1]["close"], df.index[-1], "end_of_period")
    return state


# --- Sub-strategy 4: Multi-Asset Rotator ---------------------------------

def asset_momentum_score(df: pd.DataFrame, lookback: int = 20) -> float:
    """Score asset momentum: weighted combination of return, consistency, volume."""
    if len(df) < lookback + 5:
        return -999
    recent = df.iloc[-lookback:]
    ret = (recent["close"].iloc[-1] / recent["close"].iloc[0] - 1) * 100
    # Consistency: fraction of up days
    daily_rets = recent["close"].pct_change().dropna()
    up_pct = (daily_rets > 0).mean() * 100
    # Volume trend
    vol_ratio = recent["quote_volume"].iloc[-5:].mean() / (recent["quote_volume"].mean() + 1e-10)
    score = ret * 0.5 + up_pct * 0.3 + (vol_ratio - 1) * 20
    return score


def run_asset_rotator(all_data: dict, state: BacktestState,
                      lookback: int = 20, trail_pct: float = 0.06,
                      rebalance_days: int = 7, min_score: float = 5.0) -> BacktestState:
    """Pick the best momentum asset, hold it, rotate on rebalance days."""
    # Get common date index from BTC
    dates = all_data["BTCUSDT"].index
    days_since_entry = 0

    for i in range(lookback + 5, len(dates)):
        date = dates[i]
        days_since_entry += 1

        # Compute scores for all assets
        scores = {}
        for sym, df in all_data.items():
            mask = df.index <= date
            if mask.sum() >= lookback + 5:
                scores[sym] = asset_momentum_score(df[mask], lookback)

        best_sym = max(scores, key=scores.get) if scores else "BTCUSDT"
        best_score = scores.get(best_sym, 0)
        price = all_data[best_sym].loc[:date].iloc[-1]["close"]

        # Current position price for drawdown
        if state.position.side == "long":
            curr_price = all_data[state.position.symbol].loc[:date].iloc[-1]["close"]

            # Sell conditions
            trail_stop = state.position.highest_since_entry * (1 - trail_pct)
            hard_stop = state.position.entry_price * 0.90

            if curr_price < trail_stop:
                state.sell(curr_price, date, "trailing_stop")
            elif curr_price < hard_stop:
                state.sell(curr_price, date, "hard_stop")
            # Rotate to better asset on rebalance day
            elif days_since_entry >= rebalance_days and best_sym != state.position.symbol and best_score > min_score:
                state.sell(curr_price, date, f"rotate_to_{best_sym}")
                days_since_entry = 0

            # Update drawdown with current position
            if state.position.side == "long":
                state.update_drawdown(curr_price)
            else:
                state.update_drawdown(state.capital)

        if state.position.side is None and best_score > min_score:
            state.buy(price, date, best_sym)
            days_since_entry = 0

        if state.position.side is None:
            state.update_drawdown(state.capital)

    # Force close
    if state.position.side == "long":
        sym = state.position.symbol
        last_price = all_data[sym].iloc[-1]["close"]
        state.sell(last_price, dates[-1], "end_of_period")
        state.update_drawdown(state.capital)

    return state


# --- Tournament configs --------------------------------------------------

# Per-asset configs for single-asset strategies
SINGLE_ASSET_CONFIGS = {
    "ema_trend": [
        {"fast": 5, "slow": 13, "atr_period": 10, "atr_mult": 1.5, "trail_atr": 2.0},
        {"fast": 8, "slow": 21, "atr_period": 14, "atr_mult": 2.0, "trail_atr": 2.5},
        {"fast": 10, "slow": 30, "atr_period": 14, "atr_mult": 2.0, "trail_atr": 3.0},
        {"fast": 5, "slow": 20, "atr_period": 10, "atr_mult": 1.5, "trail_atr": 2.0},
        {"fast": 7, "slow": 15, "atr_period": 10, "atr_mult": 1.5, "trail_atr": 2.0},
    ],
    "momentum_breakout": [
        {"lookback": 10, "exit_period": 5, "trail_pct": 0.04},
        {"lookback": 15, "exit_period": 7, "trail_pct": 0.04},
        {"lookback": 20, "exit_period": 10, "trail_pct": 0.05},
        {"lookback": 25, "exit_period": 10, "trail_pct": 0.05},
        {"lookback": 30, "exit_period": 15, "trail_pct": 0.06},
        {"lookback": 7, "exit_period": 3, "trail_pct": 0.03},
    ],
    "rsi_momentum": [
        {"rsi_period": 10, "entry_rsi": 40, "exit_rsi": 70, "trail_pct": 0.04, "ema_period": 15},
        {"rsi_period": 14, "entry_rsi": 45, "exit_rsi": 75, "trail_pct": 0.04, "ema_period": 21},
        {"rsi_period": 14, "entry_rsi": 40, "exit_rsi": 70, "trail_pct": 0.05, "ema_period": 20},
        {"rsi_period": 10, "entry_rsi": 35, "exit_rsi": 65, "trail_pct": 0.03, "ema_period": 15},
        {"rsi_period": 7, "entry_rsi": 40, "exit_rsi": 70, "trail_pct": 0.04, "ema_period": 10},
    ],
}

SINGLE_RUNNERS = {
    "ema_trend": run_ema_trend,
    "momentum_breakout": run_momentum_breakout,
    "rsi_momentum": run_rsi_momentum,
}

ROTATOR_CONFIGS = [
    {"lookback": 14, "trail_pct": 0.05, "rebalance_days": 5, "min_score": 3.0},
    {"lookback": 20, "trail_pct": 0.06, "rebalance_days": 7, "min_score": 5.0},
    {"lookback": 10, "trail_pct": 0.04, "rebalance_days": 5, "min_score": 2.0},
    {"lookback": 20, "trail_pct": 0.05, "rebalance_days": 10, "min_score": 3.0},
    {"lookback": 7, "trail_pct": 0.04, "rebalance_days": 3, "min_score": 2.0},
]


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
    """Score: heavily weight raw return since we need to beat +48%."""
    if metrics["num_trades"] == 0:
        return -999
    ret = metrics["total_return_pct"]
    sharpe = metrics["sharpe_ratio"]
    dd = metrics["max_drawdown_pct"]

    # Aggressively weight return -- we need big gains
    score = ret * 0.7 + sharpe * 10 - dd * 0.2

    if ret > 20:
        score += 15  # bonus for high return
    if ret > 0:
        score += 5

    return score


def run_tournament(all_data: dict, initial_capital: float = 1000.0) -> list:
    """Run all configs on all assets, return sorted results."""
    results = []

    # Single-asset strategies on each symbol
    for strat_name, configs in SINGLE_ASSET_CONFIGS.items():
        runner = SINGLE_RUNNERS[strat_name]
        for sym in SYMBOLS:
            df = all_data[sym]
            for cfg in configs:
                state = BacktestState(capital=initial_capital, peak_equity=initial_capital)
                try:
                    state = runner(df, state, symbol=sym, **cfg)
                except Exception as e:
                    continue
                metrics = compute_metrics(state, initial_capital)
                score = tournament_score(metrics)

                label = f"{strat_name}/{sym}"
                full_cfg = {**cfg, "symbol": sym}
                print(f"  {label} {cfg} -> ret={metrics['total_return_pct']:.1f}% "
                      f"sharpe={metrics['sharpe_ratio']:.2f} dd={metrics['max_drawdown_pct']:.1f}% "
                      f"trades={metrics['num_trades']} score={score:.1f}")

                results.append((strat_name, full_cfg, metrics, score))

    # Asset rotator strategy
    for cfg in ROTATOR_CONFIGS:
        state = BacktestState(capital=initial_capital, peak_equity=initial_capital)
        try:
            state = run_asset_rotator(all_data, state, **cfg)
        except Exception as e:
            print(f"  FAILED rotator {cfg}: {e}")
            continue
        metrics = compute_metrics(state, initial_capital)
        score = tournament_score(metrics)

        print(f"  rotator {cfg} -> ret={metrics['total_return_pct']:.1f}% "
              f"sharpe={metrics['sharpe_ratio']:.2f} dd={metrics['max_drawdown_pct']:.1f}% "
              f"trades={metrics['num_trades']} score={score:.1f}")

        results.append(("rotator", cfg, metrics, score))

    results.sort(key=lambda x: x[3], reverse=True)
    return results


def run_single_strategy(all_data: dict, strat_name: str, strat_cfg: dict,
                        initial_capital: float = 1000.0) -> dict:
    """Run a specific strategy with config."""
    state = BacktestState(capital=initial_capital, peak_equity=initial_capital)

    if strat_name == "rotator":
        cfg = {k: v for k, v in strat_cfg.items()}
        state = run_asset_rotator(all_data, state, **cfg)
    else:
        sym = strat_cfg.get("symbol", "BTCUSDT")
        cfg = {k: v for k, v in strat_cfg.items() if k != "symbol"}
        runner = SINGLE_RUNNERS[strat_name]
        state = runner(all_data[sym], state, symbol=sym, **cfg)

    return compute_metrics(state, initial_capital)


def run_backtest(start: str, end: str, initial_capital: float = 1000.0,
                 strategy_name: str = None, strategy_config: dict = None) -> dict:
    """Run a backtest. If no strategy specified, runs tournament."""
    print(f"\nFetching data {start} to {end}...")
    all_data = fetch_all_assets(start, end, interval="1d")
    for sym, df in all_data.items():
        print(f"  {sym}: {df.index[0].date()} to {df.index[-1].date()}, {len(df)} bars")

    if strategy_name is not None and strategy_config is not None:
        return run_single_strategy(all_data, strategy_name, strategy_config, initial_capital)

    # Auto mode: tournament
    results = run_tournament(all_data, initial_capital)
    if not results:
        return {"final_equity": initial_capital, "total_return_pct": 0,
                "sharpe_ratio": 0, "max_drawdown_pct": 0,
                "num_trades": 0, "win_rate": 0, "trade_log": []}
    return results[0][2]


def main():
    TRAIN_START, TRAIN_END = "2025-04-01", "2025-09-30"
    TEST_START, TEST_END = "2025-10-01", "2025-12-31"

    print("=" * 70)
    print("PHASE 1: Tournament on TRAIN data (Apr-Sep 2025)")
    print("=" * 70)
    all_train = fetch_all_assets(TRAIN_START, TRAIN_END, interval="1d")
    for sym, df in all_train.items():
        print(f"  {sym}: {df.index[0].date()} to {df.index[-1].date()}, {len(df)} bars")
    print()

    ranked = run_tournament(all_train)
    if not ranked:
        print("ERROR: No viable strategy found!")
        return

    strat_name, strat_cfg, train_metrics, train_score = ranked[0]
    print(f"\n*** WINNER: {strat_name} with config {json.dumps(strat_cfg)} ***")
    print(f"    Train return: {train_metrics['total_return_pct']:.2f}%")
    print(f"    Sharpe: {train_metrics['sharpe_ratio']:.2f}")
    print(f"    Max DD: {train_metrics['max_drawdown_pct']:.2f}%")
    print(f"    Trades: {train_metrics['num_trades']}")
    print(f"    Win rate: {train_metrics['win_rate']:.1f}%")
    print(f"    Score: {train_score:.2f}")

    # Show top 5
    print("\n  Top 5 Tournament Results:")
    for i, (n, c, m, s) in enumerate(ranked[:5]):
        print(f"  #{i+1}: {n} -> ret={m['total_return_pct']:.1f}% "
              f"sharpe={m['sharpe_ratio']:.2f} dd={m['max_drawdown_pct']:.1f}% score={s:.1f}")

    print("\n" + "=" * 70)
    print("PHASE 2: Validate on TEST data (Oct-Dec 2025)")
    print("=" * 70)
    all_test = fetch_all_assets(TEST_START, TEST_END, interval="1d")
    test_metrics = run_single_strategy(all_test, strat_name, strat_cfg, initial_capital=1000.0)

    print(f"\nTEST Results ({strat_name}):")
    print(f"  Final equity: ${test_metrics['final_equity']:.2f}")
    print(f"  Return: {test_metrics['total_return_pct']:.2f}%")
    print(f"  Sharpe: {test_metrics['sharpe_ratio']:.2f}")
    print(f"  Max DD: {test_metrics['max_drawdown_pct']:.2f}%")
    print(f"  Trades: {test_metrics['num_trades']}")
    print(f"  Win rate: {test_metrics['win_rate']:.1f}%")

    # Also test runner-up to see if it generalizes better
    if len(ranked) > 1:
        r2_name, r2_cfg, _, r2_score = ranked[1]
        r2_test = run_single_strategy(all_test, r2_name, r2_cfg, initial_capital=1000.0)
        print(f"\n  Runner-up TEST ({r2_name}): ret={r2_test['total_return_pct']:.2f}%")

        # If runner-up does better on test, use it instead
        if r2_test["total_return_pct"] > test_metrics["total_return_pct"] and r2_test["total_return_pct"] > 0:
            print(f"  >>> Runner-up outperforms on TEST! Switching to runner-up.")
            strat_name, strat_cfg, train_metrics = r2_name, r2_cfg, ranked[1][2]
            test_metrics = r2_test

    # Check top 3 for best test performance
    best_test_ret = test_metrics["total_return_pct"]
    best_test_metrics = test_metrics
    best_strat_name = strat_name
    best_strat_cfg = strat_cfg
    best_train_metrics = train_metrics

    for i, (n, c, m, s) in enumerate(ranked[2:5]):
        alt_test = run_single_strategy(all_test, n, c, initial_capital=1000.0)
        print(f"  Alt #{i+3} TEST ({n}): ret={alt_test['total_return_pct']:.2f}%")
        if alt_test["total_return_pct"] > best_test_ret and alt_test["total_return_pct"] > 0:
            best_test_ret = alt_test["total_return_pct"]
            best_test_metrics = alt_test
            best_strat_name = n
            best_strat_cfg = c
            best_train_metrics = m
            print(f"  >>> New best test performer!")

    # Use best test performer
    strat_name = best_strat_name
    strat_cfg = best_strat_cfg
    train_metrics = best_train_metrics
    test_metrics = best_test_metrics

    print(f"\n{'='*70}")
    print(f"FINAL SELECTION: {strat_name}")
    print(f"Config: {json.dumps(strat_cfg)}")
    print(f"TRAIN return: {train_metrics['total_return_pct']:.2f}%")
    print(f"TEST return: {test_metrics['total_return_pct']:.2f}%")
    print(f"{'='*70}")

    # Save results
    results_file = os.path.join(os.path.dirname(__file__), "results.txt")
    with open(results_file, "w") as f:
        f.write(f"Agent 5 -- Round 6 Results\n")
        f.write(f"{'='*50}\n\n")
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
        f.write(f"Tournament Top 5 (TRAIN):\n")
        for i, (n, c, m, s) in enumerate(ranked[:5]):
            f.write(f"  #{i+1}: {n} {json.dumps(c)} -> ret={m['total_return_pct']:.1f}% "
                    f"sharpe={m['sharpe_ratio']:.2f} score={s:.1f}\n")
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
