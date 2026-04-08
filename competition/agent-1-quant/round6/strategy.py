"""
Agent 1 -- Round 6: Aggressive Multi-Asset Momentum + Mean Reversion Tournament

Goal: Beat +48.47% (Agent 4's R1 score). Need high-conviction, aggressive sizing.

TRAIN: 2025-04-01 to 2025-09-30
TEST:  2025-10-01 to 2025-12-31

Approach:
  - Trade BTC, ETH, SOL on 4h bars for more signal granularity
  - Tournament of 8+ strategy variants per asset (24+ total)
  - Select top-N strategies by risk-adjusted return, ensemble them
  - Aggressive 95% capital deployment, pyramiding on winners
  - Use both momentum (breakout, EMA cross) and mean reversion (RSI, BB)
  - Short-term (4h) timeframe for more trades and compounding
  - ATR-based stops to control downside
"""

from __future__ import annotations

import itertools
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import requests

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
INTERVAL = "4h"
FEE_RATE = 0.001  # 0.1%
RESULTS_DIR = Path(__file__).parent

TRAIN_START = "2025-04-01"
TRAIN_END = "2025-09-30"
TEST_START = "2025-10-01"
TEST_END = "2025-12-31"

# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------
def fetch_binance_klines(symbol: str, interval: str, start: str, end: str) -> pd.DataFrame:
    """Fetch OHLCV from Binance public API."""
    url = "https://api.binance.com/api/v3/klines"
    start_ms = int(pd.Timestamp(start).timestamp() * 1000)
    end_ms = int(pd.Timestamp(end).timestamp() * 1000)
    all_data = []

    while start_ms < end_ms:
        params = {
            "symbol": symbol,
            "interval": interval,
            "startTime": start_ms,
            "endTime": end_ms,
            "limit": 1000,
        }
        for attempt in range(5):
            try:
                resp = requests.get(url, params=params, timeout=30)
                resp.raise_for_status()
                data = resp.json()
                break
            except Exception:
                if attempt < 4:
                    time.sleep(2 ** attempt)
                else:
                    raise

        if not data:
            break
        all_data.extend(data)
        start_ms = data[-1][0] + 1
        if len(data) < 1000:
            break
        time.sleep(0.2)

    if not all_data:
        return pd.DataFrame()

    df = pd.DataFrame(all_data, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "num_trades", "taker_buy_vol",
        "taker_buy_quote_vol", "ignore",
    ])
    for col in ["open", "high", "low", "close", "volume", "quote_volume"]:
        df[col] = df[col].astype(float)
    df["timestamp"] = pd.to_datetime(df["open_time"], unit="ms")
    df = df.set_index("timestamp")
    df = df[~df.index.duplicated(keep="first")]
    return df


def fetch_all_symbols(start: str, end: str) -> dict[str, pd.DataFrame]:
    """Fetch data for all symbols with buffer."""
    buffer_start = (pd.Timestamp(start) - pd.Timedelta(days=60)).strftime("%Y-%m-%d")
    fetch_end = (pd.Timestamp(end) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    data = {}
    for sym in SYMBOLS:
        print(f"  Fetching {sym} {INTERVAL} from {buffer_start} to {fetch_end}...")
        df = fetch_binance_klines(sym, INTERVAL, buffer_start, fetch_end)
        if len(df) > 0:
            data[sym] = df
            print(f"    Got {len(df)} bars")
        else:
            print(f"    WARNING: No data for {sym}")
        time.sleep(0.3)
    return data


# ---------------------------------------------------------------------------
# Technical indicators
# ---------------------------------------------------------------------------
def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(period).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()


def bollinger_bands(series: pd.Series, period: int = 20, std_mult: float = 2.0):
    mid = sma(series, period)
    std = series.rolling(period).std()
    return mid, mid + std_mult * std, mid - std_mult * std


def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    ema_fast = ema(series, fast)
    ema_slow = ema(series, slow)
    macd_line = ema_fast - ema_slow
    signal_line = ema(macd_line, signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def donchian(df: pd.DataFrame, period: int = 20):
    upper = df["high"].rolling(period).max()
    lower = df["low"].rolling(period).min()
    return upper, lower


def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average Directional Index for trend strength."""
    high, low, close = df["high"], df["low"], df["close"]
    plus_dm = high.diff()
    minus_dm = -low.diff()
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)

    atr_vals = atr(df, period)
    plus_di = 100 * ema(plus_dm, period) / atr_vals.replace(0, np.nan)
    minus_di = 100 * ema(minus_dm, period) / atr_vals.replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return ema(dx, period)


# ---------------------------------------------------------------------------
# Strategy implementations
# ---------------------------------------------------------------------------
@dataclass
class TradeRecord:
    entry_time: str
    exit_time: str
    side: str
    entry_price: float
    exit_price: float
    pnl_pct: float
    pnl_usd: float


def _simulate(
    df: pd.DataFrame,
    signals: pd.Series,  # 1=buy, -1=sell, 0=hold
    start: str,
    end: str,
    initial_capital: float,
    stop_loss_atr_mult: float = 2.0,
    take_profit_atr_mult: float = 4.0,
    position_size_pct: float = 0.95,
    use_trailing_stop: bool = True,
) -> dict[str, Any]:
    """Generic simulator: given a signal series, simulate trades."""
    mask = (df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end))
    period_df = df.loc[mask]

    if len(period_df) == 0:
        return _empty_result(initial_capital)

    atr_vals = atr(df, 14)

    cash = initial_capital
    position = 0.0
    entry_price = 0.0
    stop_price = 0.0
    highest_since_entry = 0.0
    entry_time = ""
    trades: list[TradeRecord] = []
    equity_curve: list[float] = []

    for ts in period_df.index:
        price = df.loc[ts, "close"]
        cur_atr = atr_vals.loc[ts] if ts in atr_vals.index else 0.0
        sig = signals.loc[ts] if ts in signals.index else 0

        if np.isnan(cur_atr) or cur_atr == 0:
            equity_curve.append(cash + position * price)
            continue

        # Exit logic
        if position > 0:
            highest_since_entry = max(highest_since_entry, price)

            # Trailing stop
            if use_trailing_stop:
                trailing_stop = highest_since_entry - stop_loss_atr_mult * cur_atr
                effective_stop = max(stop_price, trailing_stop)
            else:
                effective_stop = stop_price

            # Take profit
            tp_price = entry_price + take_profit_atr_mult * cur_atr

            should_exit = False
            if price <= effective_stop:
                should_exit = True
            elif price >= tp_price:
                should_exit = True
            elif sig == -1:
                should_exit = True

            if should_exit:
                proceeds = position * price * (1 - FEE_RATE)
                cost = position * entry_price * (1 + FEE_RATE)
                pnl_usd = proceeds - cost
                pnl_pct = (proceeds / cost - 1) * 100
                cash += proceeds
                trades.append(TradeRecord(
                    entry_time=entry_time, exit_time=str(ts),
                    side="LONG", entry_price=entry_price,
                    exit_price=price, pnl_pct=pnl_pct, pnl_usd=pnl_usd,
                ))
                position = 0.0

        # Entry logic
        elif sig == 1:
            invest = cash * position_size_pct
            fee = invest * FEE_RATE
            position = (invest - fee) / price
            cash -= invest
            entry_price = price
            stop_price = price - stop_loss_atr_mult * cur_atr
            highest_since_entry = price
            entry_time = str(ts)

        equity_curve.append(cash + position * price)

    # Force close at end
    if position > 0:
        final_price = period_df.iloc[-1]["close"]
        proceeds = position * final_price * (1 - FEE_RATE)
        cost = position * entry_price * (1 + FEE_RATE)
        pnl_usd = proceeds - cost
        pnl_pct = (proceeds / cost - 1) * 100
        cash += proceeds
        trades.append(TradeRecord(
            entry_time=entry_time, exit_time=str(period_df.index[-1]),
            side="LONG", entry_price=entry_price,
            exit_price=final_price, pnl_pct=pnl_pct, pnl_usd=pnl_usd,
        ))
        position = 0.0

    return _compute_metrics(cash, initial_capital, trades, equity_curve)


def _empty_result(initial_capital: float) -> dict[str, Any]:
    return {
        "final_equity": initial_capital,
        "total_return_pct": 0.0,
        "sharpe_ratio": 0.0,
        "max_drawdown_pct": 0.0,
        "num_trades": 0,
        "win_rate": 0.0,
        "trades": [],
    }


def _compute_metrics(
    final_cash: float,
    initial_capital: float,
    trades: list[TradeRecord],
    equity_curve: list[float],
) -> dict[str, Any]:
    total_return = (final_cash - initial_capital) / initial_capital

    ec = pd.Series(equity_curve)
    if len(ec) < 2:
        return {
            "final_equity": round(final_cash, 2),
            "total_return_pct": round(total_return * 100, 2),
            "sharpe_ratio": 0.0,
            "max_drawdown_pct": 0.0,
            "num_trades": len(trades),
            "win_rate": 0.0,
            "trades": trades,
        }

    # Resample to daily for Sharpe
    idx = pd.date_range(start="2020-01-01", periods=len(ec), freq="4h")
    ec_ts = pd.Series(equity_curve, index=idx)
    daily_eq = ec_ts.resample("D").last().dropna()
    daily_returns = daily_eq.pct_change().dropna()

    sharpe = 0.0
    if len(daily_returns) > 1 and daily_returns.std() > 0:
        sharpe = (daily_returns.mean() / daily_returns.std()) * np.sqrt(365)

    peak = ec.cummax()
    drawdown = (ec - peak) / peak
    max_dd = drawdown.min() * 100

    wins = [t for t in trades if t.pnl_pct > 0]
    win_rate = len(wins) / len(trades) * 100 if trades else 0.0

    return {
        "final_equity": round(final_cash, 2),
        "total_return_pct": round(total_return * 100, 2),
        "sharpe_ratio": round(sharpe, 4),
        "max_drawdown_pct": round(max_dd, 2),
        "num_trades": len(trades),
        "win_rate": round(win_rate, 1),
        "trades": trades,
    }


# ---------------------------------------------------------------------------
# Signal generators
# ---------------------------------------------------------------------------
def signals_ema_cross(df: pd.DataFrame, fast: int = 8, slow: int = 21) -> pd.Series:
    """EMA crossover: buy when fast > slow, sell when fast < slow."""
    ema_f = ema(df["close"], fast)
    ema_s = ema(df["close"], slow)
    signals = pd.Series(0, index=df.index)
    # Buy on crossover
    cross_up = (ema_f > ema_s) & (ema_f.shift(1) <= ema_s.shift(1))
    cross_down = (ema_f < ema_s) & (ema_f.shift(1) >= ema_s.shift(1))
    signals[cross_up] = 1
    signals[cross_down] = -1
    return signals


def signals_rsi_dip(df: pd.DataFrame, period: int = 14, buy_thresh: float = 30,
                     sell_thresh: float = 70) -> pd.Series:
    """RSI dip buyer: buy when RSI drops below threshold, sell when above."""
    rsi_vals = rsi(df["close"], period)
    signals = pd.Series(0, index=df.index)
    # State machine approach
    was_below = False
    was_above = False
    for i in range(1, len(df)):
        r = rsi_vals.iloc[i]
        if np.isnan(r):
            continue
        if r < buy_thresh:
            was_below = True
        if r > sell_thresh:
            was_above = True
        if was_below and r > buy_thresh + 5:  # Buy on recovery from oversold
            signals.iloc[i] = 1
            was_below = False
        if was_above and r < sell_thresh - 5:  # Sell on drop from overbought
            signals.iloc[i] = -1
            was_above = False
    return signals


def signals_bb_reversion(df: pd.DataFrame, period: int = 20, std_mult: float = 2.0,
                          rsi_confirm: bool = True) -> pd.Series:
    """Bollinger Band mean reversion with RSI confirmation."""
    close = df["close"]
    mid, upper, lower = bollinger_bands(close, period, std_mult)
    rsi_vals = rsi(close, 14) if rsi_confirm else pd.Series(50, index=df.index)

    signals = pd.Series(0, index=df.index)
    for i in range(period, len(df)):
        if np.isnan(lower.iloc[i]) or np.isnan(upper.iloc[i]):
            continue
        r = rsi_vals.iloc[i] if not np.isnan(rsi_vals.iloc[i]) else 50

        # Buy when price touches lower band and RSI oversold
        if close.iloc[i] <= lower.iloc[i] and r < 40:
            signals.iloc[i] = 1
        # Sell when price touches upper band and RSI overbought
        elif close.iloc[i] >= upper.iloc[i] and r > 60:
            signals.iloc[i] = -1
    return signals


def signals_macd_cross(df: pd.DataFrame, fast: int = 12, slow: int = 26,
                        sig_period: int = 9) -> pd.Series:
    """MACD histogram crossover."""
    _, _, hist = macd(df["close"], fast, slow, sig_period)
    signals = pd.Series(0, index=df.index)
    cross_up = (hist > 0) & (hist.shift(1) <= 0)
    cross_down = (hist < 0) & (hist.shift(1) >= 0)
    signals[cross_up] = 1
    signals[cross_down] = -1
    return signals


def signals_donchian_breakout(df: pd.DataFrame, period: int = 20,
                               trend_filter: bool = True) -> pd.Series:
    """Donchian channel breakout with optional trend filter."""
    upper, lower = donchian(df, period)
    signals = pd.Series(0, index=df.index)

    if trend_filter:
        trend_ema = ema(df["close"], 50)
    else:
        trend_ema = pd.Series(0, index=df.index)

    for i in range(period + 1, len(df)):
        if np.isnan(upper.iloc[i]) or np.isnan(lower.iloc[i]):
            continue
        price = df["close"].iloc[i]
        prev_price = df["close"].iloc[i - 1]

        # Breakout above upper channel
        if price > upper.iloc[i - 1] and prev_price <= upper.iloc[i - 1]:
            if not trend_filter or price > trend_ema.iloc[i]:
                signals.iloc[i] = 1
        # Break below lower channel
        elif price < lower.iloc[i - 1] and prev_price >= lower.iloc[i - 1]:
            signals.iloc[i] = -1
    return signals


def signals_momentum_squeeze(df: pd.DataFrame, bb_period: int = 20, bb_std: float = 2.0,
                              kc_period: int = 20, kc_mult: float = 1.5,
                              mom_period: int = 12) -> pd.Series:
    """Volatility squeeze: BB inside KC = squeeze, momentum direction on release."""
    close = df["close"]
    bb_mid, bb_upper, bb_lower = bollinger_bands(close, bb_period, bb_std)
    atr_vals = atr(df, kc_period)
    kc_upper = ema(close, kc_period) + kc_mult * atr_vals
    kc_lower = ema(close, kc_period) - kc_mult * atr_vals

    # Momentum: close - midline of Donchian
    high_roll = df["high"].rolling(mom_period).max()
    low_roll = df["low"].rolling(mom_period).min()
    midline = (high_roll + low_roll) / 2
    momentum = close - midline

    squeeze = (bb_lower > kc_lower) & (bb_upper < kc_upper)

    signals = pd.Series(0, index=df.index)
    was_squeeze = False
    for i in range(max(bb_period, kc_period, mom_period) + 1, len(df)):
        if squeeze.iloc[i]:
            was_squeeze = True
        elif was_squeeze:
            # Squeeze released
            was_squeeze = False
            if momentum.iloc[i] > 0:
                signals.iloc[i] = 1
            elif momentum.iloc[i] < 0:
                signals.iloc[i] = -1
    return signals


def signals_triple_ema(df: pd.DataFrame, fast: int = 5, mid: int = 13,
                        slow: int = 34) -> pd.Series:
    """Triple EMA alignment: buy when fast > mid > slow, sell when reversed."""
    ef = ema(df["close"], fast)
    em = ema(df["close"], mid)
    es = ema(df["close"], slow)

    signals = pd.Series(0, index=df.index)
    bullish = (ef > em) & (em > es)
    bearish = (ef < em) & (em < es)

    # Signal on transition
    bull_entry = bullish & (~bullish.shift(1).fillna(False))
    bear_entry = bearish & (~bearish.shift(1).fillna(False))
    signals[bull_entry] = 1
    signals[bear_entry] = -1
    return signals


def signals_rsi_trend(df: pd.DataFrame, rsi_period: int = 14,
                       trend_period: int = 50) -> pd.Series:
    """RSI with trend filter: buy dips in uptrend, sell rips in downtrend."""
    rsi_vals = rsi(df["close"], rsi_period)
    trend = ema(df["close"], trend_period)
    close = df["close"]

    signals = pd.Series(0, index=df.index)
    for i in range(trend_period + 1, len(df)):
        r = rsi_vals.iloc[i]
        if np.isnan(r):
            continue
        in_uptrend = close.iloc[i] > trend.iloc[i]

        if in_uptrend and r < 35:  # Dip in uptrend
            signals.iloc[i] = 1
        elif not in_uptrend and r > 65:  # Rip in downtrend
            signals.iloc[i] = -1
        elif in_uptrend and r > 80:  # Overbought exit
            signals.iloc[i] = -1
    return signals


def signals_adx_trend(df: pd.DataFrame, adx_period: int = 14,
                       adx_threshold: float = 25) -> pd.Series:
    """ADX trend following: enter on strong trend, exit on weakening."""
    adx_vals = adx(df, adx_period)
    ema_fast = ema(df["close"], 10)
    ema_slow = ema(df["close"], 30)

    signals = pd.Series(0, index=df.index)
    for i in range(30, len(df)):
        a = adx_vals.iloc[i]
        if np.isnan(a):
            continue
        bullish = ema_fast.iloc[i] > ema_slow.iloc[i]

        if a > adx_threshold and bullish:
            if not (adx_vals.iloc[i - 1] > adx_threshold and
                    ema_fast.iloc[i - 1] > ema_slow.iloc[i - 1]):
                signals.iloc[i] = 1
        elif a < adx_threshold or not bullish:
            if adx_vals.iloc[i - 1] > adx_threshold and ema_fast.iloc[i - 1] > ema_slow.iloc[i - 1]:
                signals.iloc[i] = -1
    return signals


# ---------------------------------------------------------------------------
# Tournament runner
# ---------------------------------------------------------------------------
def build_strategy_variants(df: pd.DataFrame) -> dict[str, tuple]:
    """Build all strategy signal variants for a given dataframe."""
    variants = {}

    # EMA Cross variants
    for fast, slow in [(5, 13), (8, 21), (10, 30), (5, 20)]:
        name = f"EMA({fast},{slow})"
        variants[name] = (signals_ema_cross, {"fast": fast, "slow": slow})

    # RSI Dip variants
    for period, buy, sell in [(14, 25, 70), (14, 30, 65), (10, 30, 70), (14, 35, 65)]:
        name = f"RSI_Dip({period},{buy},{sell})"
        variants[name] = (signals_rsi_dip, {"period": period, "buy_thresh": buy, "sell_thresh": sell})

    # BB Reversion variants
    for period, std in [(20, 2.0), (20, 1.5), (15, 2.0)]:
        name = f"BB_Rev({period},{std})"
        variants[name] = (signals_bb_reversion, {"period": period, "std_mult": std})

    # MACD variants
    for fast, slow, sig in [(12, 26, 9), (8, 17, 9), (5, 13, 8)]:
        name = f"MACD({fast},{slow},{sig})"
        variants[name] = (signals_macd_cross, {"fast": fast, "slow": slow, "sig_period": sig})

    # Donchian variants
    for period in [15, 20, 30]:
        name = f"Donchian({period})"
        variants[name] = (signals_donchian_breakout, {"period": period})

    # Volatility squeeze
    variants["VolSqueeze"] = (signals_momentum_squeeze, {})

    # Triple EMA
    for fast, mid, slow in [(5, 13, 34), (3, 8, 21)]:
        name = f"TripleEMA({fast},{mid},{slow})"
        variants[name] = (signals_triple_ema, {"fast": fast, "mid": mid, "slow": slow})

    # RSI Trend
    for rsi_p, trend_p in [(14, 50), (10, 30)]:
        name = f"RSI_Trend({rsi_p},{trend_p})"
        variants[name] = (signals_rsi_trend, {"rsi_period": rsi_p, "trend_period": trend_p})

    # ADX Trend
    variants["ADX_Trend"] = (signals_adx_trend, {})

    return variants


def run_tournament_for_symbol(
    df: pd.DataFrame,
    symbol: str,
    train_start: str,
    train_end: str,
    test_start: str,
    test_end: str,
    initial_capital: float,
) -> dict[str, dict]:
    """Run all strategies on train, pick best, evaluate on test."""
    variants = build_strategy_variants(df)
    train_results = {}
    test_results = {}

    for name, (sig_fn, params) in variants.items():
        try:
            sigs = sig_fn(df, **params)

            # Train with multiple stop/TP configs
            for sl_mult, tp_mult, trailing in [(1.5, 3.0, True), (2.0, 4.0, True),
                                                 (2.5, 5.0, True), (1.5, 3.0, False)]:
                full_name = f"{symbol}:{name}:SL{sl_mult}_TP{tp_mult}_T{trailing}"

                train_r = _simulate(df, sigs, train_start, train_end, initial_capital,
                                    stop_loss_atr_mult=sl_mult, take_profit_atr_mult=tp_mult,
                                    use_trailing_stop=trailing)
                train_results[full_name] = train_r

                test_r = _simulate(df, sigs, test_start, test_end, initial_capital,
                                   stop_loss_atr_mult=sl_mult, take_profit_atr_mult=tp_mult,
                                   use_trailing_stop=trailing)
                test_results[full_name] = test_r
        except Exception as e:
            print(f"    Error in {symbol}:{name}: {e}")
            continue

    return train_results, test_results


# ---------------------------------------------------------------------------
# Ensemble: Sequential capital allocation
# ---------------------------------------------------------------------------
def run_ensemble(
    all_data: dict[str, pd.DataFrame],
    train_start: str,
    train_end: str,
    test_start: str,
    test_end: str,
    initial_capital: float,
    top_n: int = 3,
) -> dict[str, Any]:
    """
    Run tournament across all assets, select top-N by train Sharpe,
    then allocate capital to the single best performer.
    Also try a simple approach: just pick the single best strategy overall.
    """
    all_train = {}
    all_test = {}

    for symbol, df in all_data.items():
        print(f"\n  Running tournament for {symbol}...")
        train_r, test_r = run_tournament_for_symbol(
            df, symbol, train_start, train_end, test_start, test_end, initial_capital
        )
        all_train.update(train_r)
        all_test.update(test_r)
        print(f"    {len(train_r)} strategy variants evaluated")

    # Filter strategies with trades and positive Sharpe on train
    valid_train = {
        k: v for k, v in all_train.items()
        if v["num_trades"] >= 2 and v["sharpe_ratio"] > 0
    }

    if not valid_train:
        # Fallback: any strategy with trades
        valid_train = {k: v for k, v in all_train.items() if v["num_trades"] >= 1}

    if not valid_train:
        return {
            "final_equity": initial_capital,
            "total_return_pct": 0.0,
            "sharpe_ratio": 0.0,
            "max_drawdown_pct": 0.0,
            "num_trades": 0,
            "win_rate": 0.0,
            "strategy_name": "NONE",
            "trade_log": [],
        }

    # Rank by composite score: 60% Sharpe + 40% return (normalized)
    scores = {}
    sharpe_vals = [v["sharpe_ratio"] for v in valid_train.values()]
    return_vals = [v["total_return_pct"] for v in valid_train.values()]
    max_sharpe = max(sharpe_vals) if sharpe_vals else 1
    max_return = max(return_vals) if return_vals else 1
    min_sharpe = min(sharpe_vals) if sharpe_vals else 0
    min_return = min(return_vals) if return_vals else 0

    sharpe_range = max_sharpe - min_sharpe if max_sharpe != min_sharpe else 1
    return_range = max_return - min_return if max_return != min_return else 1

    for k, v in valid_train.items():
        norm_sharpe = (v["sharpe_ratio"] - min_sharpe) / sharpe_range
        norm_return = (v["total_return_pct"] - min_return) / return_range
        scores[k] = 0.6 * norm_sharpe + 0.4 * norm_return

    # Sort by score
    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)

    # Pick the best strategy on TRAIN, report its TEST performance
    best_name = ranked[0][0]
    best_test = all_test[best_name]
    best_train = all_train[best_name]

    # Also check: what if we pick top-3 and average?
    top_names = [r[0] for r in ranked[:top_n]]

    print(f"\n  Top {top_n} strategies by train score:")
    for name in top_names:
        tr = all_train[name]
        te = all_test[name]
        print(f"    {name}: TRAIN={tr['total_return_pct']:+.2f}% (Sharpe={tr['sharpe_ratio']:.2f}) "
              f"TEST={te['total_return_pct']:+.2f}%")

    # Use single best (simpler, less risk of dilution)
    result = {
        "final_equity": best_test["final_equity"],
        "total_return_pct": best_test["total_return_pct"],
        "sharpe_ratio": best_test["sharpe_ratio"],
        "max_drawdown_pct": best_test["max_drawdown_pct"],
        "num_trades": best_test["num_trades"],
        "win_rate": best_test["win_rate"],
        "strategy_name": best_name,
        "trade_log": [
            {
                "entry_time": t.entry_time, "exit_time": t.exit_time,
                "side": t.side, "entry_price": round(t.entry_price, 2),
                "exit_price": round(t.exit_price, 2),
                "pnl_pct": round(t.pnl_pct, 2), "pnl_usd": round(t.pnl_usd, 2),
            }
            for t in best_test["trades"]
        ],
        "train_result": {k: v for k, v in best_train.items() if k != "trades"},
        "top_strategies": {
            name: {
                "train": {k: v for k, v in all_train[name].items() if k != "trades"},
                "test": {k: v for k, v in all_test[name].items() if k != "trades"},
            }
            for name in top_names
        },
    }

    # Check if any test result is dramatically better (opportunistic override)
    best_test_name = max(all_test, key=lambda k: all_test[k]["total_return_pct"])
    best_test_return = all_test[best_test_name]["total_return_pct"]
    if best_test_return > best_test["total_return_pct"] * 2 and best_test_return > 20:
        print(f"\n  NOTE: Best TEST performer is {best_test_name} with {best_test_return:+.2f}%")
        print(f"        (not selected because we pick by train performance)")

    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def run_backtest(start: str, end: str, initial_capital: float = 1000.0) -> dict:
    """
    Run the full tournament backtest.
    Uses start/end as the TEST period, with a preceding 6-month TRAIN period.
    """
    test_start = start
    test_end = end

    # Derive train period: 6 months before test start
    test_start_ts = pd.Timestamp(test_start)
    train_start_ts = test_start_ts - pd.DateOffset(months=6)
    train_start = train_start_ts.strftime("%Y-%m-%d")
    train_end = (test_start_ts - pd.Timedelta(days=1)).strftime("%Y-%m-%d")

    print(f"  TRAIN: {train_start} to {train_end}")
    print(f"  TEST:  {test_start} to {test_end}")

    # Fetch data (with buffer for indicators)
    all_data = fetch_all_symbols(train_start, test_end)

    if not all_data:
        return _empty_result(initial_capital)

    result = run_ensemble(
        all_data, train_start, train_end, test_start, test_end, initial_capital
    )

    return {
        "final_equity": result["final_equity"],
        "total_return_pct": result["total_return_pct"],
        "sharpe_ratio": result["sharpe_ratio"],
        "max_drawdown_pct": result["max_drawdown_pct"],
        "num_trades": result["num_trades"],
        "win_rate": result["win_rate"],
        "strategy_name": result.get("strategy_name", "unknown"),
        "trade_log": result.get("trade_log", []),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print("=" * 70)
    print("ROUND 6 — Agent 1: Aggressive Multi-Asset Momentum Tournament (4h)")
    print("=" * 70)

    # TRAIN period evaluation
    print("\n--- TRAIN Period ---")
    train_result = run_backtest(TRAIN_START, TRAIN_END, 1000.0)
    print(f"\n  TRAIN Results:")
    for k, v in train_result.items():
        if k not in ("trade_log",):
            print(f"    {k}: {v}")

    # TEST period evaluation
    print("\n\n--- TEST Period ---")
    test_result = run_backtest(TEST_START, TEST_END, 1000.0)
    print(f"\n  TEST Results:")
    for k, v in test_result.items():
        if k not in ("trade_log",):
            print(f"    {k}: {v}")

    # Save results
    results = {
        "agent": "Agent 1 — Quantitative Trader",
        "round": 6,
        "strategy": "Aggressive Multi-Asset Momentum Tournament (4h)",
        "description": (
            "Tournament of 24+ strategy variants across BTC/ETH/SOL on 4h bars. "
            "Strategies: EMA cross, RSI dip, BB reversion, MACD, Donchian breakout, "
            "volatility squeeze, triple EMA, RSI trend, ADX trend. "
            "Multiple stop/TP configs. Select best by composite train score "
            "(60% Sharpe + 40% return). 95% position sizing, ATR stops."
        ),
        "symbols": SYMBOLS,
        "interval": INTERVAL,
        "train": {k: v for k, v in train_result.items() if k != "trade_log"},
        "test": {k: v for k, v in test_result.items() if k != "trade_log"},
        "test_trade_log": test_result.get("trade_log", []),
    }

    results_file = RESULTS_DIR / "results.txt"
    with open(results_file, "w") as f:
        f.write("Agent 1 — Quantitative Trader: Round 6 Results\n")
        f.write("=" * 60 + "\n\n")
        f.write(f"Strategy: {results['strategy']}\n")
        f.write(f"Description: {results['description']}\n")
        f.write(f"Symbols: {', '.join(SYMBOLS)}\n")
        f.write(f"Interval: {INTERVAL}\n\n")

        f.write("TRAIN Period Results:\n")
        for k, v in results["train"].items():
            f.write(f"  {k}: {v}\n")

        f.write("\nTEST Period Results:\n")
        for k, v in results["test"].items():
            f.write(f"  {k}: {v}\n")

        f.write("\nTrade Log (TEST):\n")
        for t in results.get("test_trade_log", []):
            f.write(f"  {t['entry_time']} -> {t['exit_time']}: "
                    f"{t['side']} entry=${t['entry_price']:,.2f} "
                    f"exit=${t['exit_price']:,.2f} pnl={t['pnl_pct']:+.2f}%\n")

    print(f"\nResults saved to {results_file}")

    # JSON
    json_file = RESULTS_DIR / "results.json"
    with open(json_file, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"Full results saved to {json_file}")


if __name__ == "__main__":
    main()
