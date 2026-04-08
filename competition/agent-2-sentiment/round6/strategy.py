"""
Agent 2 — Round 6: Aggressive Multi-Asset Momentum with Panic/Euphoria Detection

STRATEGY SHIFT: Previous rounds were too conservative (+0.49% best).
Agent 4 won with +48.47% by being aggressive on momentum.
To compete, I need high upside — willing to accept drawdowns for big returns.

Core approach:
- Trade BTC, ETH, and SOL simultaneously for more opportunities
- Momentum breakout entries (price above recent range + volume surge)
- Panic-buy on extreme fear (massive drops = mean reversion opportunity)
- Full capital deployment (not 50% position sizing)
- Tight trailing stops to protect gains but wide enough for trends
- Short-term timeframe (4h) for faster reaction

Behavioral/Sentiment signals:
1. Volume spike = crowd conviction (breakout confirmation)
2. Taker buy ratio surge = aggressive buying (bullish sentiment)
3. RSI extremes + volume = panic selling (buy opportunity)
4. Price-volume divergence = weakening trend (exit signal)
5. Multi-day consolidation breakout = stored energy release
"""

import json
import time
import os
from dataclasses import dataclass, field

import requests
import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------

def fetch_klines(symbol: str, interval: str, start: str, end: str) -> pd.DataFrame:
    """Fetch klines from Binance public API."""
    url = "https://api.binance.com/api/v3/klines"
    start_ms = int(pd.Timestamp(start, tz="UTC").timestamp() * 1000)
    end_ms = int(pd.Timestamp(end, tz="UTC").timestamp() * 1000)
    rows: list = []
    while start_ms < end_ms:
        resp = requests.get(url, params={
            "symbol": symbol, "interval": interval,
            "startTime": start_ms, "endTime": end_ms, "limit": 1000,
        }, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if not data:
            break
        rows.extend(data)
        start_ms = data[-1][0] + 1
        time.sleep(0.12)
    df = pd.DataFrame(rows, columns=[
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

def rsi(s: pd.Series, p: int = 14) -> pd.Series:
    d = s.diff()
    g = d.clip(lower=0)
    l = -d.clip(upper=0)
    ag = g.ewm(alpha=1/p, min_periods=p, adjust=False).mean()
    al = l.ewm(alpha=1/p, min_periods=p, adjust=False).mean()
    return 100 - (100 / (1 + ag / al))


def atr(h: pd.Series, l: pd.Series, c: pd.Series, n: int = 14) -> pd.Series:
    tr = pd.concat([h - l, (h - c.shift(1)).abs(), (l - c.shift(1)).abs()], axis=1).max(axis=1)
    return tr.ewm(span=n, adjust=False).mean()


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n).mean()


def obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    direction = np.sign(close.diff())
    return (volume * direction).cumsum()


def taker_buy_ratio(taker_buy_quote: pd.Series, quote_volume: pd.Series) -> pd.Series:
    """Ratio of taker buy volume to total volume — proxy for buying pressure."""
    return taker_buy_quote / quote_volume.replace(0, np.nan)


FEE_RATE = 0.001


# ---------------------------------------------------------------------------
# Multi-asset aggressive momentum strategy
# ---------------------------------------------------------------------------

@dataclass
class Position:
    symbol: str
    entry_time: str
    entry_price: float
    size_usd: float
    side: str = "long"
    trail_stop: float = 0.0
    entry_idx: int = 0


@dataclass
class TradeResult:
    symbol: str
    entry_time: str
    entry_price: float
    exit_time: str
    exit_price: float
    side: str
    pnl_pct: float
    pnl_usd: float
    exit_reason: str


def run_single_asset(
    df: pd.DataFrame,
    symbol: str,
    capital: float,
    params: dict,
) -> tuple[float, list[TradeResult], list[float]]:
    """Run strategy on a single asset, return (final_capital, trades, equity_curve)."""

    p = params
    closes = df["close"].values
    highs = df["high"].values
    lows = df["low"].values
    volumes = df["volume"].values

    # Compute indicators
    rsi_v = rsi(df["close"], p["rsi_period"]).values
    atr_v = atr(df["high"], df["low"], df["close"], p["atr_period"]).values
    ema_fast = ema(df["close"], p["ema_fast"]).values
    ema_slow = ema(df["close"], p["ema_slow"]).values

    # Volume indicators
    vol_sma = sma(df["volume"], p["vol_window"]).values
    tbr = taker_buy_ratio(df["taker_buy_quote"], df["quote_volume"]).values
    tbr_sma = pd.Series(tbr).rolling(p["vol_window"]).mean().values

    # OBV trend
    obv_v = obv(df["close"], df["volume"]).values
    obv_ema = ema(pd.Series(obv_v), p["obv_ema"]).values

    # Bollinger bands for squeeze detection
    bb_mid = sma(df["close"], p["bb_period"]).values
    bb_std = df["close"].rolling(p["bb_period"]).std().values

    warmup = max(p["ema_slow"], p["bb_period"], p["vol_window"], p["obv_ema"]) + 10

    eq = capital
    pos = None
    trades = []
    equity_curve = []
    cooldown = 0

    for i in range(warmup, len(df)):
        price = closes[i]
        ts = str(df.index[i])

        if pos is None:
            if cooldown > 0:
                cooldown -= 1
                equity_curve.append(eq)
                continue

            # Skip if indicators are NaN
            if np.isnan(rsi_v[i]) or np.isnan(atr_v[i]) or np.isnan(ema_fast[i]):
                equity_curve.append(eq)
                continue

            # === ENTRY SIGNALS ===
            signal = False
            signal_type = ""

            # SIGNAL 1: Momentum breakout
            # EMA fast > slow (trend up) + volume surge + taker buy pressure
            trend_up = ema_fast[i] > ema_slow[i]
            vol_surge = volumes[i] > vol_sma[i] * p["vol_mult"]
            obv_rising = obv_v[i] > obv_ema[i]
            tbr_bullish = tbr[i] > tbr_sma[i] * p.get("tbr_mult", 1.02)

            # Price breaking above recent high (momentum)
            lookback = p.get("breakout_lb", 24)
            recent_high = np.max(highs[max(0, i-lookback):i])
            breakout = price > recent_high

            if trend_up and vol_surge and obv_rising and breakout:
                signal = True
                signal_type = "momentum_breakout"

            # SIGNAL 2: Panic buy (extreme fear → mean reversion)
            # RSI very low + volume spike + large recent drop
            if not signal:
                rsi_panic = rsi_v[i] < p["rsi_panic"]
                vol_spike = volumes[i] > vol_sma[i] * p["panic_vol_mult"]
                recent_drop = (price - np.max(closes[max(0,i-48):i])) / np.max(closes[max(0,i-48):i]) < -p["panic_drop"]

                if rsi_panic and vol_spike and recent_drop:
                    signal = True
                    signal_type = "panic_buy"

            # SIGNAL 3: Taker sentiment surge
            # Very high taker buy ratio + trend filter
            if not signal and trend_up:
                tbr_extreme = tbr[i] > p.get("tbr_extreme", 0.62)
                if tbr_extreme and vol_surge:
                    signal = True
                    signal_type = "taker_surge"

            if signal:
                # Aggressive position sizing — use most of available capital
                size = eq * p["position_pct"]
                fee = size * FEE_RATE
                eq -= size  # remove allocated capital from available equity

                # Set initial stop based on signal type
                if signal_type == "panic_buy":
                    stop = price * (1 - p["panic_stop_pct"])
                else:
                    stop = price - atr_v[i] * p["atr_stop_mult"]
                    stop = max(stop, price * (1 - p["max_stop_pct"]))

                pos = Position(
                    symbol=symbol, entry_time=ts, entry_price=price,
                    size_usd=size, side="long", trail_stop=stop, entry_idx=i,
                )

        else:
            # === POSITION MANAGEMENT ===
            hold_bars = i - pos.entry_idx

            # Update trailing stop
            new_trail = highs[i] - atr_v[i] * p["atr_trail_mult"]
            pos.trail_stop = max(pos.trail_stop, new_trail)

            # Exit conditions
            hit_stop = lows[i] <= pos.trail_stop
            hit_tp = price >= pos.entry_price * (1 + p["take_profit"])
            hit_max_hold = hold_bars >= p["max_hold"]

            # Sentiment-based exit: volume drying up in uptrend
            vol_dying = volumes[i] < vol_sma[i] * 0.5 and hold_bars > 12
            obv_diverge = obv_v[i] < obv_ema[i] and price > pos.entry_price * 1.02

            exit_signal = hit_stop or hit_tp or hit_max_hold
            soft_exit = (vol_dying and obv_diverge) and hold_bars > p.get("min_hold", 6)

            if exit_signal or soft_exit:
                if hit_stop:
                    exit_price = max(pos.trail_stop, lows[i])
                    reason = "trail_stop"
                elif hit_tp:
                    exit_price = price
                    reason = "take_profit"
                elif hit_max_hold:
                    exit_price = price
                    reason = "max_hold"
                else:
                    exit_price = price
                    reason = "sentiment_exit"

                gross_pnl = (exit_price - pos.entry_price) / pos.entry_price
                # Position value after price move, minus both entry and exit fees
                position_value = pos.size_usd * (1 + gross_pnl)
                entry_fee = pos.size_usd * FEE_RATE
                exit_fee = position_value * FEE_RATE
                net_proceeds = position_value - entry_fee - exit_fee
                net_pnl_usd = net_proceeds - pos.size_usd
                eq += net_proceeds  # return proceeds to available capital

                trades.append(TradeResult(
                    symbol=symbol, entry_time=pos.entry_time,
                    entry_price=round(pos.entry_price, 2),
                    exit_time=ts, exit_price=round(exit_price, 2),
                    side="long", pnl_pct=round(gross_pnl * 100, 2),
                    pnl_usd=round(net_pnl_usd, 2),
                    exit_reason=reason,
                ))

                cooldown = p.get("cooldown", 6)
                pos = None

        # Track equity including open position value
        if pos is not None:
            open_value = pos.size_usd * (closes[i] / pos.entry_price)
            equity_curve.append(eq + open_value)
        else:
            equity_curve.append(eq)

    # Close open position at end
    if pos is not None:
        price = closes[-1]
        gross_pnl = (price - pos.entry_price) / pos.entry_price
        position_value = pos.size_usd * (1 + gross_pnl)
        entry_fee = pos.size_usd * FEE_RATE
        exit_fee = position_value * FEE_RATE
        net_proceeds = position_value - entry_fee - exit_fee
        net_pnl_usd = net_proceeds - pos.size_usd
        eq += net_proceeds
        trades.append(TradeResult(
            symbol=symbol, entry_time=pos.entry_time,
            entry_price=round(pos.entry_price, 2),
            exit_time=str(df.index[-1]), exit_price=round(price, 2),
            side="long", pnl_pct=round(gross_pnl * 100, 2),
            pnl_usd=round(net_pnl_usd, 2),
            exit_reason="end_of_period",
        ))

    return eq, trades, equity_curve


# ---------------------------------------------------------------------------
# Parameter configurations to test
# ---------------------------------------------------------------------------

def generate_configs() -> list[dict]:
    """Generate parameter configurations focused on aggressive momentum."""
    configs = []

    # Grid over key parameters
    for ema_fast in [8, 12, 20]:
        for ema_slow in [40, 50, 72]:
            for rsi_period in [10, 14]:
                for atr_stop in [2.0, 2.5, 3.0]:
                    for atr_trail in [2.5, 3.0, 4.0]:
                        for vol_mult in [1.5, 2.0, 2.5]:
                            for tp in [0.08, 0.12, 0.20]:
                                for breakout_lb in [24, 48]:
                                    configs.append({
                                        "ema_fast": ema_fast,
                                        "ema_slow": ema_slow,
                                        "rsi_period": rsi_period,
                                        "rsi_panic": 22,
                                        "atr_period": 14,
                                        "atr_stop_mult": atr_stop,
                                        "atr_trail_mult": atr_trail,
                                        "vol_window": 48,
                                        "vol_mult": vol_mult,
                                        "panic_vol_mult": 2.5,
                                        "panic_drop": 0.06,
                                        "panic_stop_pct": 0.04,
                                        "tbr_mult": 1.02,
                                        "tbr_extreme": 0.62,
                                        "obv_ema": 24,
                                        "bb_period": 20,
                                        "breakout_lb": breakout_lb,
                                        "position_pct": 0.95,
                                        "max_stop_pct": 0.05,
                                        "take_profit": tp,
                                        "max_hold": 120,
                                        "min_hold": 6,
                                        "cooldown": 4,
                                    })

    return configs


# ---------------------------------------------------------------------------
# Portfolio runner: split capital across assets
# ---------------------------------------------------------------------------

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
WEIGHTS = {"BTCUSDT": 0.40, "ETHUSDT": 0.30, "SOLUSDT": 0.30}  # SOL has more upside potential


def compute_metrics(final_eq: float, initial: float, trades: list, equity_curve: list) -> dict:
    """Compute strategy metrics."""
    total_return = (final_eq - initial) / initial * 100
    n = len(trades)
    wins = [t for t in trades if t.pnl_pct > 0]
    wr = len(wins) / n * 100 if n else 0

    if equity_curve and len(equity_curve) > 1:
        eq = np.array(equity_curve)
        pk = np.maximum.accumulate(eq)
        dd = (eq - pk) / pk * 100
        mdd = abs(dd.min())

        r = pd.Series(equity_curve).pct_change().dropna()
        sharpe = (r.mean() / r.std() * np.sqrt(365 * 6)) if r.std() > 0 else 0
    else:
        mdd = 0.0
        sharpe = 0.0

    return {
        "final_equity": round(final_eq, 2),
        "total_return_pct": round(total_return, 2),
        "sharpe_ratio": round(sharpe, 2),
        "max_drawdown_pct": round(mdd, 2),
        "num_trades": n,
        "win_rate": round(wr, 1),
    }


def run_tournament_single_asset(
    symbol: str, df: pd.DataFrame, capital: float, configs: list[dict],
) -> tuple[dict, float, list, list]:
    """Run all configs on one asset, return best config + results."""
    best_ret = -999
    best_config = configs[0]
    best_trades = []
    best_curve = []
    best_eq = capital

    for cfg in configs:
        try:
            eq, trades, curve = run_single_asset(df, symbol, capital, cfg)
            ret = (eq - capital) / capital * 100
            if ret > best_ret and len(trades) >= 1:
                best_ret = ret
                best_config = cfg
                best_trades = trades
                best_curve = curve
                best_eq = eq
        except Exception:
            continue

    return best_config, best_eq, best_trades, best_curve


def run_backtest(start: str, end: str, initial_capital: float = 1000.0) -> dict:
    """Run multi-asset momentum backtest."""
    # Add buffer for indicator warmup
    buffer_days = 30
    buffer_start = (pd.Timestamp(start) - pd.Timedelta(days=buffer_days)).strftime("%Y-%m-%d")

    configs = generate_configs()
    print(f"Generated {len(configs)} parameter configurations")

    all_trades = []
    total_equity = 0.0
    all_curves = []
    best_configs = {}

    for symbol in SYMBOLS:
        weight = WEIGHTS[symbol]
        alloc = initial_capital * weight
        print(f"\n{'='*50}")
        print(f"{symbol} (allocation: ${alloc:.0f}, weight: {weight:.0%})")
        print(f"{'='*50}")

        print(f"Fetching {symbol} 4h klines {buffer_start} -> {end} ...")
        df = fetch_klines(symbol, "4h", buffer_start, end)
        print(f"  {len(df)} candles fetched")

        # Trim to actual period for evaluation (but keep buffer for indicators)
        start_ts = pd.Timestamp(start, tz="UTC")
        # We pass the full df (with buffer) to the strategy; it handles warmup internally

        print(f"Running tournament with {len(configs)} configs ...")
        best_cfg, eq, trades, curve = run_tournament_single_asset(
            symbol, df, alloc, configs,
        )

        best_configs[symbol] = best_cfg
        total_equity += eq
        all_trades.extend(trades)
        if curve:
            all_curves.extend(curve)

        ret = (eq - alloc) / alloc * 100
        print(f"  Best return: {ret:.2f}% ({len(trades)} trades)")
        print(f"  Best config: ema_fast={best_cfg['ema_fast']}, ema_slow={best_cfg['ema_slow']}, "
              f"vol_mult={best_cfg['vol_mult']}, tp={best_cfg['take_profit']}, "
              f"atr_stop={best_cfg['atr_stop_mult']}, breakout_lb={best_cfg['breakout_lb']}")

    # Compute portfolio metrics
    m = compute_metrics(total_equity, initial_capital, all_trades, all_curves)

    trade_log = [{
        "symbol": t.symbol, "entry_time": t.entry_time,
        "entry_price": t.entry_price, "exit_time": t.exit_time,
        "exit_price": t.exit_price, "side": t.side,
        "pnl_pct": t.pnl_pct, "pnl_usd": t.pnl_usd,
        "exit_reason": t.exit_reason,
    } for t in all_trades]

    return {
        **m,
        "trade_log": trade_log,
        "configs": {s: c for s, c in best_configs.items()},
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    out_dir = os.path.dirname(os.path.abspath(__file__))

    print("=" * 70)
    print("TRAIN: Apr - Sep 2025")
    print("=" * 70)
    train = run_backtest("2025-04-01", "2025-09-30", 1000)
    print("\n--- TRAIN RESULTS ---")
    for k, v in train.items():
        if k not in ("trade_log", "configs"):
            print(f"  {k}: {v}")

    print("\n" + "=" * 70)
    print("TEST: Oct - Dec 2025")
    print("=" * 70)

    # For TEST, use the best configs found on TRAIN
    train_configs = train.get("configs", {})

    # Re-run with fixed configs on TEST period
    buffer_start_test = "2025-09-01"  # 30 day buffer
    test_trades = []
    test_equity = 0.0
    test_curves = []

    for symbol in SYMBOLS:
        weight = WEIGHTS[symbol]
        alloc = 1000 * weight
        print(f"\n{symbol} (${alloc:.0f}):")

        df = fetch_klines(symbol, "4h", buffer_start_test, "2025-12-31")
        print(f"  {len(df)} candles")

        cfg = train_configs.get(symbol, generate_configs()[0])
        eq, trades, curve = run_single_asset(df, symbol, alloc, cfg)
        test_equity += eq
        test_trades.extend(trades)
        if curve:
            test_curves.extend(curve)

        ret = (eq - alloc) / alloc * 100
        print(f"  Return: {ret:.2f}% ({len(trades)} trades)")

    test_m = compute_metrics(test_equity, 1000, test_trades, test_curves)

    test_log = [{
        "symbol": t.symbol, "entry_time": t.entry_time,
        "entry_price": t.entry_price, "exit_time": t.exit_time,
        "exit_price": t.exit_price, "side": t.side,
        "pnl_pct": t.pnl_pct, "pnl_usd": t.pnl_usd,
        "exit_reason": t.exit_reason,
    } for t in test_trades]

    test_result = {**test_m, "trade_log": test_log,
                   "configs": {s: c for s, c in train_configs.items()}}

    print("\n--- TEST RESULTS ---")
    for k, v in test_result.items():
        if k not in ("trade_log", "configs"):
            print(f"  {k}: {v}")

    # Save results
    output = {
        "agent": "Agent 2 - Sentiment Trader",
        "round": 6,
        "approach": (
            "Aggressive multi-asset momentum with panic/euphoria detection. "
            "Trades BTC (40%), ETH (30%), SOL (30%). Three entry signals: "
            "momentum breakout (EMA trend + volume surge + price breakout), "
            "panic buy (extreme RSI + volume spike + large drop), "
            "taker sentiment surge (high buy ratio + volume). "
            "ATR trailing stops. Tournament selects best config per asset on TRAIN."
        ),
        "train": {k: v for k, v in train.items() if k != "configs"},
        "test": {k: v for k, v in test_result.items() if k != "configs"},
    }

    results_path = os.path.join(out_dir, "results.txt")
    with open(results_path, "w") as f:
        f.write(json.dumps(output, indent=2))
    print(f"\nResults saved to {results_path}")
