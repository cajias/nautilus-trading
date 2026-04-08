"""
Agent 2 — Round 4: Tournament Sentiment Strategy
Runs multiple BTC sub-strategies on TRAIN, picks the best for TEST.
Sub-strategies use volume/momentum signals as sentiment proxies.
"""

import datetime as dt
import json
import time
from dataclasses import dataclass, field
from typing import Any

import requests
import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------

def fetch_klines(symbol: str, interval: str, start: str, end: str) -> pd.DataFrame:
    """Fetch Binance klines (public API). No auth needed."""
    url = "https://api.binance.com/api/v3/klines"
    start_ms = int(pd.Timestamp(start, tz="UTC").timestamp() * 1000)
    end_ms = int(pd.Timestamp(end, tz="UTC").timestamp() * 1000)
    all_rows = []
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
        time.sleep(0.15)
    df = pd.DataFrame(all_rows, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades", "taker_buy_base",
        "taker_buy_quote", "ignore",
    ])
    for c in ["open", "high", "low", "close", "volume", "quote_volume",
              "taker_buy_base", "taker_buy_quote"]:
        df[c] = df[c].astype(float)
    df["trades"] = df["trades"].astype(int)
    df["timestamp"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df.set_index("timestamp", inplace=True)
    df.sort_index(inplace=True)
    return df


# ---------------------------------------------------------------------------
# Indicators
# ---------------------------------------------------------------------------

def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def sma(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    sign = np.sign(close.diff()).fillna(0)
    return (sign * volume).cumsum()


def taker_buy_ratio(taker_buy_base: pd.Series, volume: pd.Series) -> pd.Series:
    return taker_buy_base / volume.replace(0, np.nan)


# ---------------------------------------------------------------------------
# Sub-strategies
# ---------------------------------------------------------------------------

FEE_RATE = 0.001  # 0.1%


@dataclass
class Trade:
    entry_time: str
    entry_price: float
    exit_time: str = ""
    exit_price: float = 0.0
    side: str = "long"
    pnl_pct: float = 0.0


@dataclass
class SubStrategy:
    name: str
    params: dict
    trades: list = field(default_factory=list)
    equity_curve: list = field(default_factory=list)
    final_equity: float = 0.0

    def run(self, df: pd.DataFrame, capital: float) -> "SubStrategy":
        raise NotImplementedError


class TBRMomentum(SubStrategy):
    """Taker-buy-ratio momentum: go long when TBR is high + trend confirmation."""

    def run(self, df: pd.DataFrame, capital: float) -> "SubStrategy":
        p = self.params
        tbr = taker_buy_ratio(df["taker_buy_base"], df["volume"])
        tbr_ma = sma(tbr, p["tbr_window"])
        trend_ma = ema(df["close"], p["trend_ema"])
        rsi_vals = rsi(df["close"], p.get("rsi_period", 14))

        position = None
        equity = capital
        self.trades = []
        self.equity_curve = []

        for i in range(max(p["tbr_window"], p["trend_ema"], 20), len(df)):
            price = df["close"].iloc[i]
            ts = str(df.index[i])

            if position is None:
                # Entry: TBR above its MA + price above trend EMA + RSI not overbought
                if (tbr.iloc[i] > tbr_ma.iloc[i] * p["tbr_threshold"]
                        and price > trend_ma.iloc[i]
                        and rsi_vals.iloc[i] < p.get("rsi_upper", 75)):
                    position = Trade(entry_time=ts, entry_price=price, side="long")
            else:
                # Exit conditions
                hold_bars = i - df.index.get_loc(
                    pd.Timestamp(position.entry_time))
                stop_loss = position.entry_price * (1 - p["stop_loss"])
                take_profit = position.entry_price * (1 + p["take_profit"])

                if (price <= stop_loss
                        or price >= take_profit
                        or tbr.iloc[i] < tbr_ma.iloc[i] * (2 - p["tbr_threshold"])
                        or hold_bars >= p.get("max_hold", 100)):
                    gross = (price - position.entry_price) / position.entry_price
                    net = gross - 2 * FEE_RATE
                    position.exit_time = ts
                    position.exit_price = price
                    position.pnl_pct = net * 100
                    equity *= (1 + net)
                    self.trades.append(position)
                    position = None

            self.equity_curve.append(equity)

        # Force close open position at end
        if position is not None:
            price = df["close"].iloc[-1]
            gross = (price - position.entry_price) / position.entry_price
            net = gross - 2 * FEE_RATE
            position.exit_time = str(df.index[-1])
            position.exit_price = price
            position.pnl_pct = net * 100
            equity *= (1 + net)
            self.trades.append(position)

        self.final_equity = equity
        return self


class OBVTrend(SubStrategy):
    """OBV divergence / trend following."""

    def run(self, df: pd.DataFrame, capital: float) -> "SubStrategy":
        p = self.params
        obv_vals = obv(df["close"], df["volume"])
        obv_ma = ema(obv_vals, p["obv_ema"])
        price_ma = ema(df["close"], p["price_ema"])
        rsi_vals = rsi(df["close"], p.get("rsi_period", 14))

        position = None
        equity = capital
        self.trades = []
        self.equity_curve = []

        warmup = max(p["obv_ema"], p["price_ema"], 20)
        for i in range(warmup, len(df)):
            price = df["close"].iloc[i]
            ts = str(df.index[i])

            if position is None:
                if (obv_vals.iloc[i] > obv_ma.iloc[i]
                        and price > price_ma.iloc[i]
                        and rsi_vals.iloc[i] > p.get("rsi_lower", 35)
                        and rsi_vals.iloc[i] < p.get("rsi_upper", 70)):
                    position = Trade(entry_time=ts, entry_price=price, side="long")
            else:
                hold_bars = i - df.index.get_loc(pd.Timestamp(position.entry_time))
                stop_loss = position.entry_price * (1 - p["stop_loss"])
                take_profit = position.entry_price * (1 + p["take_profit"])

                if (price <= stop_loss
                        or price >= take_profit
                        or obv_vals.iloc[i] < obv_ma.iloc[i]
                        or hold_bars >= p.get("max_hold", 100)):
                    gross = (price - position.entry_price) / position.entry_price
                    net = gross - 2 * FEE_RATE
                    position.exit_time = ts
                    position.exit_price = price
                    position.pnl_pct = net * 100
                    equity *= (1 + net)
                    self.trades.append(position)
                    position = None

            self.equity_curve.append(equity)

        if position is not None:
            price = df["close"].iloc[-1]
            gross = (price - position.entry_price) / position.entry_price
            net = gross - 2 * FEE_RATE
            position.exit_time = str(df.index[-1])
            position.exit_price = price
            position.pnl_pct = net * 100
            equity *= (1 + net)
            self.trades.append(position)

        self.final_equity = equity
        return self


class RSIReversion(SubStrategy):
    """RSI mean reversion with volume confirmation."""

    def run(self, df: pd.DataFrame, capital: float) -> "SubStrategy":
        p = self.params
        rsi_vals = rsi(df["close"], p["rsi_period"])
        vol_ma = sma(df["volume"], p["vol_window"])
        price_ma = ema(df["close"], p.get("trend_ema", 50))

        position = None
        equity = capital
        self.trades = []
        self.equity_curve = []

        warmup = max(p["rsi_period"], p["vol_window"], p.get("trend_ema", 50)) + 5
        for i in range(warmup, len(df)):
            price = df["close"].iloc[i]
            ts = str(df.index[i])

            if position is None:
                # Buy oversold + volume spike + still in uptrend
                if (rsi_vals.iloc[i] < p["rsi_entry"]
                        and df["volume"].iloc[i] > vol_ma.iloc[i] * p["vol_mult"]
                        and price > price_ma.iloc[i] * p.get("trend_filter", 0.97)):
                    position = Trade(entry_time=ts, entry_price=price, side="long")
            else:
                hold_bars = i - df.index.get_loc(pd.Timestamp(position.entry_time))
                stop_loss = position.entry_price * (1 - p["stop_loss"])
                take_profit = position.entry_price * (1 + p["take_profit"])

                if (price <= stop_loss
                        or price >= take_profit
                        or rsi_vals.iloc[i] > p["rsi_exit"]
                        or hold_bars >= p.get("max_hold", 80)):
                    gross = (price - position.entry_price) / position.entry_price
                    net = gross - 2 * FEE_RATE
                    position.exit_time = ts
                    position.exit_price = price
                    position.pnl_pct = net * 100
                    equity *= (1 + net)
                    self.trades.append(position)
                    position = None

            self.equity_curve.append(equity)

        if position is not None:
            price = df["close"].iloc[-1]
            gross = (price - position.entry_price) / position.entry_price
            net = gross - 2 * FEE_RATE
            position.exit_time = str(df.index[-1])
            position.exit_price = price
            position.pnl_pct = net * 100
            equity *= (1 + net)
            self.trades.append(position)

        self.final_equity = equity
        return self


# ---------------------------------------------------------------------------
# Tournament
# ---------------------------------------------------------------------------

def generate_candidates() -> list[SubStrategy]:
    """Generate diverse parameter combinations for tournament."""
    candidates = []

    # TBR Momentum variants — wider TP for big BTC moves
    for tbr_w in [10, 20, 30]:
        for trend_ema in [20, 50, 100]:
            for tp in [0.06, 0.10, 0.15]:
                for sl in [0.03, 0.04]:
                    for tbr_thresh in [1.02, 1.05]:
                        candidates.append(TBRMomentum(
                            name=f"TBR_tw{tbr_w}_te{trend_ema}_tp{tp}_sl{sl}_th{tbr_thresh}",
                            params={
                                "tbr_window": tbr_w, "trend_ema": trend_ema,
                                "tbr_threshold": tbr_thresh, "stop_loss": sl,
                                "take_profit": tp, "rsi_upper": 75,
                                "rsi_period": 14, "max_hold": 120,
                            }
                        ))

    # OBV Trend variants — longer holds, wider targets
    for obv_ema in [14, 21, 40]:
        for price_ema in [30, 50, 100]:
            for tp in [0.08, 0.12, 0.15]:
                for sl in [0.03, 0.05]:
                    candidates.append(OBVTrend(
                        name=f"OBV_oe{obv_ema}_pe{price_ema}_tp{tp}_sl{sl}",
                        params={
                            "obv_ema": obv_ema, "price_ema": price_ema,
                            "stop_loss": sl, "take_profit": tp,
                            "rsi_lower": 35, "rsi_upper": 72,
                            "rsi_period": 14, "max_hold": 168,
                        }
                    ))

    # RSI Reversion variants — tighter entry, wider TP
    for rsi_p in [10, 14, 20]:
        for rsi_entry in [25, 30, 35]:
            for tp in [0.06, 0.10, 0.15]:
                for sl in [0.025, 0.04]:
                    for vol_mult in [1.2, 1.5]:
                        candidates.append(RSIReversion(
                            name=f"RSI_rp{rsi_p}_re{rsi_entry}_tp{tp}_sl{sl}_vm{vol_mult}",
                            params={
                                "rsi_period": rsi_p, "rsi_entry": rsi_entry,
                                "rsi_exit": 65, "vol_window": 20,
                                "vol_mult": vol_mult, "trend_ema": 50,
                                "trend_filter": 0.97, "stop_loss": sl,
                                "take_profit": tp, "max_hold": 96,
                            }
                        ))

    return candidates


def compute_metrics(strat: SubStrategy, capital: float) -> dict:
    trades = strat.trades
    n = len(trades)
    ret_pct = (strat.final_equity - capital) / capital * 100
    wins = [t for t in trades if t.pnl_pct > 0]
    win_rate = len(wins) / n * 100 if n > 0 else 0

    # Max drawdown from equity curve
    if strat.equity_curve:
        eq = np.array(strat.equity_curve)
        peak = np.maximum.accumulate(eq)
        dd = (eq - peak) / peak * 100
        max_dd = abs(dd.min())
    else:
        max_dd = 0.0

    # Approximate Sharpe (daily returns from equity curve)
    if len(strat.equity_curve) > 1:
        eq = pd.Series(strat.equity_curve)
        rets = eq.pct_change().dropna()
        sharpe = (rets.mean() / rets.std() * np.sqrt(365 * 24)) if rets.std() > 0 else 0
    else:
        sharpe = 0.0

    return {
        "final_equity": round(strat.final_equity, 2),
        "total_return_pct": round(ret_pct, 2),
        "sharpe_ratio": round(sharpe, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "num_trades": n,
        "win_rate": round(win_rate, 1),
    }


def select_best(results: list[tuple[SubStrategy, dict]], min_trades: int = 3) -> tuple[SubStrategy, dict]:
    """Pick best strategy: prefer profitable with good win rate, moderate trade count."""
    # Tier 1: profitable, 3-15 trades, win rate >= 50%
    tier1 = [(s, m) for s, m in results
             if m["total_return_pct"] > 0
             and min_trades <= m["num_trades"] <= 15
             and m["win_rate"] >= 50]
    # Tier 2: profitable, 3-25 trades
    tier2 = [(s, m) for s, m in results
             if m["total_return_pct"] > 0
             and min_trades <= m["num_trades"] <= 25]
    # Tier 3: any profitable
    tier3 = [(s, m) for s, m in results if m["total_return_pct"] > 0]

    for tier in [tier1, tier2, tier3]:
        if tier:
            viable = tier
            break
    else:
        viable = results

    # Score: return - drawdown penalty + win rate bonus
    def score(item):
        m = item[1]
        return m["total_return_pct"] - 0.5 * m["max_drawdown_pct"] + 0.1 * m["win_rate"]

    viable.sort(key=score, reverse=True)
    return viable[0]


# ---------------------------------------------------------------------------
# Main backtest runner
# ---------------------------------------------------------------------------

def run_backtest(start: str, end: str, initial_capital: float = 1000) -> dict:
    """
    Fetch BTC 1h data, run tournament on data, return best strategy results.
    """
    print(f"Fetching BTCUSDT 1h klines {start} -> {end} ...")
    df = fetch_klines("BTCUSDT", "1h", start, end)
    print(f"  Got {len(df)} candles")

    candidates = generate_candidates()
    print(f"Running tournament with {len(candidates)} candidates ...")

    results = []
    for c in candidates:
        try:
            c.run(df, initial_capital)
            m = compute_metrics(c, initial_capital)
            results.append((c, m))
        except Exception as e:
            pass  # skip broken combos

    best_strat, best_metrics = select_best(results)
    print(f"Best: {best_strat.name} => {best_metrics['total_return_pct']}%")

    trade_log = [
        {
            "entry_time": t.entry_time,
            "entry_price": round(t.entry_price, 2),
            "exit_time": t.exit_time,
            "exit_price": round(t.exit_price, 2),
            "side": t.side,
            "pnl_pct": round(t.pnl_pct, 2),
        }
        for t in best_strat.trades
    ]

    return {
        "strategy_name": best_strat.name,
        "strategy_params": best_strat.params,
        **best_metrics,
        "trade_log": trade_log,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import os

    out_dir = os.path.dirname(os.path.abspath(__file__))

    # TRAIN
    print("=" * 60)
    print("TRAIN: Oct 2024 - Mar 2025")
    print("=" * 60)
    train = run_backtest("2024-10-01", "2025-03-31", 1000)
    print(json.dumps({k: v for k, v in train.items() if k != "trade_log"}, indent=2))

    # Now run the WINNING strategy's params on TEST period
    # We re-fetch data and re-run only that one strategy
    print()
    print("=" * 60)
    print("TEST: Apr - Jun 2025")
    print("=" * 60)

    test_df = fetch_klines("BTCUSDT", "1h", "2025-04-01", "2025-06-30")
    print(f"  Got {len(test_df)} candles")

    # Reconstruct the winning strategy type
    winner_name = train["strategy_name"]
    winner_params = train["strategy_params"]

    if winner_name.startswith("TBR"):
        test_strat = TBRMomentum(name=winner_name, params=winner_params)
    elif winner_name.startswith("OBV"):
        test_strat = OBVTrend(name=winner_name, params=winner_params)
    elif winner_name.startswith("RSI"):
        test_strat = RSIReversion(name=winner_name, params=winner_params)
    else:
        raise ValueError(f"Unknown strategy type: {winner_name}")

    test_strat.run(test_df, 1000)
    test_metrics = compute_metrics(test_strat, 1000)

    test_trade_log = [
        {
            "entry_time": t.entry_time,
            "entry_price": round(t.entry_price, 2),
            "exit_time": t.exit_time,
            "exit_price": round(t.exit_price, 2),
            "side": t.side,
            "pnl_pct": round(t.pnl_pct, 2),
        }
        for t in test_strat.trades
    ]

    test_result = {
        "strategy_name": winner_name,
        "strategy_params": winner_params,
        **test_metrics,
        "trade_log": test_trade_log,
    }

    print(json.dumps({k: v for k, v in test_result.items() if k != "trade_log"}, indent=2))

    # Save results
    output = {
        "agent": "Agent 2 - Sentiment Trader",
        "round": 4,
        "approach": "Tournament: run 200+ sub-strategies (TBR momentum, OBV trend, RSI reversion) on TRAIN, pick best for TEST",
        "train": train,
        "test": test_result,
    }

    results_path = os.path.join(out_dir, "results.txt")
    with open(results_path, "w") as f:
        f.write(json.dumps(output, indent=2))

    print(f"\nResults saved to {results_path}")
