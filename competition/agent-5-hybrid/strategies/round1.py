"""
Agent 5 - The Hybrid Strategist: Multi-Signal Ensemble with Adaptive Weighting
===============================================================================
Trades BTC/USDT, ETH/USDT, SOL/USDT on 4-hour bars.

Ensemble of 4 Signal Types:
  1. Trend: Dual EMA crossover (12/50) with ADX confirmation
  2. Momentum: RSI deviation from neutral with directional bias
  3. Volatility Breakout: Keltner Channel breakout
  4. Mean Reversion: Bollinger Band z-score snap-back

Regime Detection:
  - TRENDING: ADX > 25 and clear EMA separation
  - RANGING: ADX <= 25 or EMAs converged
  - HIGH_VOL: ATR percentile > 80th (reduce size, favor mean reversion)

Dynamic Signal Weighting:
  - Track rolling hit rate of each signal over last 30 trades
  - Weight signals by recent accuracy; minimum weight floor of 0.1
  - Composite score = weighted sum of normalized signals [-1, +1]

Risk Management:
  - 1.5x ATR stop loss, trailing after 1x ATR profit
  - Position size: target 1% equity risk per trade
  - Max 40% equity per asset, max 80% total exposure
  - 0.1% commission per trade (Binance spot)
"""

import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import requests


# ---------------------------------------------------------------------------
# 1. DATA DOWNLOAD
# ---------------------------------------------------------------------------

def download_binance_klines(
    symbol: str,
    interval: str = "4h",
    start: str = "2025-05-01",
    end: str = "2026-01-02",
) -> pd.DataFrame:
    """Download klines from Binance public API (no auth needed)."""
    url = "https://api.binance.com/api/v3/klines"
    start_ms = int(pd.Timestamp(start).timestamp() * 1000)
    end_ms = int(pd.Timestamp(end).timestamp() * 1000)
    limit = 1000

    all_data = []
    current = start_ms

    print(f"  Downloading {symbol} {interval} from {start} to {end}...")
    while current < end_ms:
        params = {
            "symbol": symbol,
            "interval": interval,
            "startTime": current,
            "endTime": end_ms,
            "limit": limit,
        }
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if not data:
            break
        all_data.extend(data)
        current = data[-1][6] + 1

    cols = [
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades", "taker_buy_base",
        "taker_buy_quote", "ignore",
    ]
    df = pd.DataFrame(all_data, columns=cols)
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = df[c].astype(float)
    df["timestamp"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df = df.set_index("timestamp")
    df = df[["open", "high", "low", "close", "volume"]]
    df = df[~df.index.duplicated(keep="first")]
    return df.sort_index()


# ---------------------------------------------------------------------------
# 2. INDICATORS
# ---------------------------------------------------------------------------

def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Compute all indicators for the 4-signal ensemble."""
    d = df.copy()

    # --- Trend: Dual EMA ---
    d["ema_12"] = d["close"].ewm(span=12, adjust=False).mean()
    d["ema_50"] = d["close"].ewm(span=50, adjust=False).mean()
    d["ema_spread"] = (d["ema_12"] - d["ema_50"]) / d["close"]

    # --- ADX (Average Directional Index) ---
    high_diff = d["high"].diff()
    low_diff = -d["low"].diff()
    plus_dm = np.where((high_diff > low_diff) & (high_diff > 0), high_diff, 0.0)
    minus_dm = np.where((low_diff > high_diff) & (low_diff > 0), low_diff, 0.0)

    tr = pd.concat([
        d["high"] - d["low"],
        (d["high"] - d["close"].shift(1)).abs(),
        (d["low"] - d["close"].shift(1)).abs(),
    ], axis=1).max(axis=1)
    d["atr_14"] = tr.ewm(span=14, adjust=False).mean()

    plus_di = 100 * pd.Series(plus_dm, index=d.index).ewm(span=14, adjust=False).mean() / d["atr_14"]
    minus_di = 100 * pd.Series(minus_dm, index=d.index).ewm(span=14, adjust=False).mean() / d["atr_14"]
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    d["adx"] = dx.ewm(span=14, adjust=False).mean()

    # --- ATR (20-period for stops/sizing) ---
    d["atr_20"] = tr.rolling(20).mean()

    # --- ATR percentile (volatility regime) ---
    d["atr_pct"] = d["atr_20"].rolling(120).rank(pct=True)

    # --- RSI (14) ---
    delta = d["close"].diff()
    gain = delta.where(delta > 0, 0.0).ewm(span=14, adjust=False).mean()
    loss = (-delta.where(delta < 0, 0.0)).ewm(span=14, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    d["rsi"] = 100 - (100 / (1 + rs))

    # --- Bollinger Bands (20, 2.0) ---
    d["bb_mid"] = d["close"].rolling(20).mean()
    d["bb_std"] = d["close"].rolling(20).std()
    d["bb_upper"] = d["bb_mid"] + 2.0 * d["bb_std"]
    d["bb_lower"] = d["bb_mid"] - 2.0 * d["bb_std"]
    d["bb_zscore"] = (d["close"] - d["bb_mid"]) / d["bb_std"].replace(0, np.nan)

    # --- Keltner Channel (20, 1.5x ATR) ---
    d["kc_mid"] = d["close"].ewm(span=20, adjust=False).mean()
    d["kc_upper"] = d["kc_mid"] + 1.5 * d["atr_20"]
    d["kc_lower"] = d["kc_mid"] - 1.5 * d["atr_20"]

    # --- Volume MA for confirmation ---
    d["vol_ma"] = d["volume"].rolling(20).mean()
    d["vol_ratio"] = d["volume"] / d["vol_ma"].replace(0, np.nan)

    # --- Log returns ---
    d["log_ret"] = np.log(d["close"] / d["close"].shift(1))

    return d


# ---------------------------------------------------------------------------
# 3. SIGNAL GENERATION (each returns -1 to +1)
# ---------------------------------------------------------------------------

def signal_trend(row: pd.Series) -> float:
    """EMA crossover trend signal. +1 = bullish, -1 = bearish."""
    spread = row["ema_spread"]
    adx = row["adx"]

    if pd.isna(adx) or pd.isna(spread):
        return 0.0

    # Scale by ADX strength (stronger trend = stronger signal)
    adx_factor = min(adx / 40.0, 1.0)  # Normalize ADX to 0-1

    # Require meaningful EMA separation to avoid whipsaw
    if spread > 0.005:
        return adx_factor  # Bullish, scaled by trend strength
    elif spread < -0.005:
        return -adx_factor  # Bearish
    return 0.0


def signal_momentum(row: pd.Series) -> float:
    """RSI-based momentum signal. Deviation from neutral."""
    rsi = row["rsi"]
    if pd.isna(rsi):
        return 0.0

    # Normalize RSI to [-1, +1] centered at 50
    return np.clip((rsi - 50) / 30, -1, 1)


def signal_volatility_breakout(row: pd.Series) -> float:
    """Keltner Channel breakout signal."""
    close = row["close"]
    kc_upper = row["kc_upper"]
    kc_lower = row["kc_lower"]
    vol_ratio = row["vol_ratio"]

    if pd.isna(kc_upper) or pd.isna(vol_ratio):
        return 0.0

    # Require strong volume confirmation for breakouts
    vol_confirm = 1.0 if vol_ratio > 1.5 else 0.3

    kc_range = kc_upper - kc_lower
    if kc_range == 0:
        return 0.0

    if close > kc_upper:
        strength = min((close - kc_upper) / (0.5 * kc_range), 1.0)
        return strength * vol_confirm
    elif close < kc_lower:
        strength = min((kc_lower - close) / (0.5 * kc_range), 1.0)
        return -strength * vol_confirm
    return 0.0


def signal_mean_reversion(row: pd.Series) -> float:
    """Bollinger Band z-score mean reversion signal (contrarian)."""
    zscore = row["bb_zscore"]
    if pd.isna(zscore):
        return 0.0

    # Mean reversion: extreme z-scores suggest snap-back
    # Positive z-score (overbought) -> negative signal (expect drop)
    # Negative z-score (oversold) -> positive signal (expect bounce)
    if abs(zscore) > 1.5:
        return np.clip(-zscore / 3.0, -1, 1)
    return 0.0


# ---------------------------------------------------------------------------
# 4. REGIME DETECTION
# ---------------------------------------------------------------------------

def detect_regime(row: pd.Series) -> str:
    """Classify market regime for signal weighting."""
    adx = row["adx"]
    atr_pct = row["atr_pct"]

    if pd.isna(adx) or pd.isna(atr_pct):
        return "UNKNOWN"

    if atr_pct > 0.80:
        return "HIGH_VOL"
    elif adx > 25:
        return "TRENDING"
    else:
        return "RANGING"


# ---------------------------------------------------------------------------
# 5. DYNAMIC SIGNAL WEIGHTING
# ---------------------------------------------------------------------------

# Base weights by regime (trend, momentum, vol_breakout, mean_reversion)
REGIME_WEIGHTS = {
    "TRENDING":   np.array([0.35, 0.30, 0.25, 0.10]),
    "RANGING":    np.array([0.10, 0.20, 0.15, 0.55]),
    "HIGH_VOL":   np.array([0.10, 0.15, 0.20, 0.55]),
    "UNKNOWN":    np.array([0.25, 0.25, 0.25, 0.25]),
}

MIN_WEIGHT = 0.05  # Floor to prevent any signal from being fully ignored


class AdaptiveWeights:
    """Track signal performance and adapt weights."""

    def __init__(self, lookback: int = 40):
        self.lookback = lookback
        # History: list of (signal_name_idx, direction, outcome)
        self.history: list[tuple[int, float, float]] = []
        self.signal_names = ["trend", "momentum", "vol_breakout", "mean_reversion"]

    def record(self, signal_idx: int, signal_val: float, pnl: float):
        """Record a trade outcome for a signal."""
        self.history.append((signal_idx, signal_val, pnl))
        if len(self.history) > self.lookback * 4:
            self.history = self.history[-self.lookback * 4:]

    def get_adjusted_weights(self, base_weights: np.ndarray) -> np.ndarray:
        """Adjust base regime weights by recent signal performance."""
        if len(self.history) < 10:
            return base_weights

        # Compute hit rate per signal
        hit_rates = np.ones(4) * 0.5  # Default 50%
        for sig_idx in range(4):
            outcomes = [(d, p) for (s, d, p) in self.history[-self.lookback * 4:]
                        if s == sig_idx]
            if len(outcomes) >= 5:
                wins = sum(1 for _, p in outcomes if p > 0)
                hit_rates[sig_idx] = wins / len(outcomes)

        # Adjust weights: boost signals with higher hit rates
        # Performance factor: 0.5 -> 0.5x, 0.6 -> 1.2x, 0.7 -> 1.4x
        perf_factor = hit_rates * 2.0  # 50% -> 1.0, 60% -> 1.2
        adjusted = base_weights * perf_factor
        adjusted = np.maximum(adjusted, MIN_WEIGHT)
        adjusted /= adjusted.sum()
        return adjusted


# ---------------------------------------------------------------------------
# 6. BACKTEST ENGINE (Multi-Asset)
# ---------------------------------------------------------------------------

COMMISSION = 0.001  # 0.1% per trade


class Position:
    def __init__(self, symbol: str, side: str, entry_price: float,
                 size: float, stop_loss: float, entry_time, signals: np.ndarray):
        self.symbol = symbol
        self.side = side
        self.entry_price = entry_price
        self.size = size
        self.stop_loss = stop_loss
        self.entry_time = entry_time
        self.signals = signals  # Which signals contributed
        self.highest = entry_price if side == "long" else entry_price
        self.lowest = entry_price if side == "short" else entry_price


def run_backtest(
    asset_data: dict[str, pd.DataFrame],
    start_date: str = "2025-07-01",
    end_date: str = "2025-12-31",
    initial_capital: float = 1000.0,
    risk_per_trade: float = 0.01,
    composite_threshold: float = 0.40,
    max_per_asset: float = 0.40,
    max_total_exposure: float = 0.80,
) -> dict:
    """Run multi-asset ensemble backtest."""

    # Compute indicators for all assets
    processed: dict[str, pd.DataFrame] = {}
    for sym, df in asset_data.items():
        d = compute_indicators(df)
        d = d.loc[start_date:end_date]
        if d.empty:
            print(f"  WARNING: No data for {sym} in range")
            continue
        processed[sym] = d

    if not processed:
        raise ValueError("No data for any asset in the backtest range")

    # Build a unified timeline
    all_times = sorted(set().union(*[set(d.index) for d in processed.values()]))

    equity = initial_capital
    peak_equity = initial_capital
    max_drawdown = 0.0
    positions: dict[str, Position] = {}  # symbol -> position
    trades: list[dict] = []
    equity_curve = []
    adaptive = AdaptiveWeights(lookback=40)
    last_trade_time: dict[str, pd.Timestamp] = {}  # cooldown tracker
    COOLDOWN_BARS = 6  # Minimum 6 bars (24h on 4H) between trades per asset
    DRAWDOWN_BREAKER = 0.10  # Stop new entries if drawdown exceeds 10%
    in_circuit_break = False
    recent_pnl: list[float] = []  # Track recent trade PnLs for streak detection

    for ts in all_times:
        # --- Process each asset ---
        for sym, df in processed.items():
            if ts not in df.index:
                continue
            row = df.loc[ts]

            if pd.isna(row.get("atr_20", np.nan)) or pd.isna(row.get("adx", np.nan)):
                continue

            atr = row["atr_20"]
            if atr == 0:
                continue

            # --- Check existing position ---
            if sym in positions:
                pos = positions[sym]
                exit_price = None
                reason = None

                # Update trailing high/low
                if pos.side == "long":
                    pos.highest = max(pos.highest, row["high"])
                else:
                    pos.lowest = min(pos.lowest, row["low"])

                # Stop loss check
                if pos.side == "long" and row["low"] <= pos.stop_loss:
                    exit_price = pos.stop_loss
                    reason = "stop"
                elif pos.side == "short" and row["high"] >= pos.stop_loss:
                    exit_price = pos.stop_loss
                    reason = "stop"

                # Trailing stop update (after 2x ATR profit, trail at 2x ATR)
                if exit_price is None:
                    if pos.side == "long":
                        profit = row["close"] - pos.entry_price
                        if profit > 2 * atr:
                            new_stop = pos.highest - 2.0 * atr
                            pos.stop_loss = max(pos.stop_loss, new_stop)
                    else:
                        profit = pos.entry_price - row["close"]
                        if profit > 2 * atr:
                            new_stop = pos.lowest + 2.0 * atr
                            pos.stop_loss = min(pos.stop_loss, new_stop)

                # Time-based exit: close after 42 bars (~7 days on 4H)
                bars_held = len(df.loc[pos.entry_time:ts]) - 1
                if exit_price is None and bars_held >= 42:
                    exit_price = row["close"]
                    reason = "time_exit"

                # Signal reversal exit
                if exit_price is None:
                    signals = np.array([
                        signal_trend(row),
                        signal_momentum(row),
                        signal_volatility_breakout(row),
                        signal_mean_reversion(row),
                    ])
                    regime = detect_regime(row)
                    base_w = REGIME_WEIGHTS.get(regime, REGIME_WEIGHTS["UNKNOWN"])
                    weights = adaptive.get_adjusted_weights(base_w)
                    composite = np.dot(weights, signals)

                    if pos.side == "long" and composite < -0.40:
                        exit_price = row["close"]
                        reason = "reversal"
                    elif pos.side == "short" and composite > 0.40:
                        exit_price = row["close"]
                        reason = "reversal"

                if exit_price is not None:
                    if pos.side == "long":
                        pnl = (exit_price - pos.entry_price) * pos.size
                    else:
                        pnl = (pos.entry_price - exit_price) * pos.size
                    pnl -= abs(exit_price * pos.size) * COMMISSION
                    equity += pnl

                    # Record outcome for adaptive weights and streak tracking
                    dominant_signal = int(np.argmax(np.abs(pos.signals)))
                    adaptive.record(dominant_signal, pos.signals[dominant_signal], pnl)
                    recent_pnl.append(pnl)
                    if len(recent_pnl) > 10:
                        recent_pnl = recent_pnl[-10:]

                    trades.append({
                        "symbol": sym,
                        "entry_time": pos.entry_time,
                        "exit_time": ts,
                        "side": pos.side,
                        "entry": pos.entry_price,
                        "exit": exit_price,
                        "pnl": pnl,
                        "reason": reason,
                    })
                    del positions[sym]
                    last_trade_time[sym] = ts

            # --- Entry logic (no existing position for this asset) ---
            if sym not in positions and not in_circuit_break:
                # Cooldown check
                if sym in last_trade_time:
                    bars_since = len(df.loc[last_trade_time[sym]:ts]) - 1
                    if bars_since < COOLDOWN_BARS:
                        continue
                # Check exposure limits
                total_exposure = sum(
                    p.size * p.entry_price for p in positions.values()
                ) / equity if equity > 0 else 0

                if total_exposure >= max_total_exposure:
                    continue

                # Compute signals
                signals = np.array([
                    signal_trend(row),
                    signal_momentum(row),
                    signal_volatility_breakout(row),
                    signal_mean_reversion(row),
                ])

                # Regime detection and dynamic weighting
                regime = detect_regime(row)
                base_w = REGIME_WEIGHTS.get(regime, REGIME_WEIGHTS["UNKNOWN"])
                weights = adaptive.get_adjusted_weights(base_w)
                composite = np.dot(weights, signals)

                # Require minimum agreement: at least 3 signals in same direction
                signal_agreement = sum(1 for s in signals if s * composite > 0 and abs(s) > 0.05)
                if signal_agreement < 3:
                    continue

                # Volatility scaling: reduce size in high vol regimes
                vol_scale = 0.6 if regime == "HIGH_VOL" else 1.0

                if abs(composite) >= composite_threshold:
                    side = "long" if composite > 0 else "short"
                    stop_distance = 2.5 * atr

                    # Position sizing: risk budget
                    size = (risk_per_trade * equity * vol_scale) / stop_distance
                    notional = size * row["close"]

                    # Asset exposure limit
                    if notional > max_per_asset * equity:
                        size = (max_per_asset * equity) / row["close"]
                        notional = size * row["close"]

                    # Total exposure limit
                    remaining = (max_total_exposure * equity) - (total_exposure * equity)
                    if notional > remaining:
                        size = remaining / row["close"]
                        if size <= 0:
                            continue

                    entry_cost = row["close"] * size * COMMISSION
                    equity -= entry_cost

                    if side == "long":
                        stop = row["close"] - stop_distance
                    else:
                        stop = row["close"] + stop_distance

                    positions[sym] = Position(
                        symbol=sym, side=side, entry_price=row["close"],
                        size=size, stop_loss=stop, entry_time=ts, signals=signals,
                    )

        # --- Drawdown circuit breaker ---
        mtm_check = equity
        for sym_c, pos_c in positions.items():
            if sym_c in processed and ts in processed[sym_c].index:
                price_c = processed[sym_c].loc[ts, "close"]
                if pos_c.side == "long":
                    mtm_check += (price_c - pos_c.entry_price) * pos_c.size
                else:
                    mtm_check += (pos_c.entry_price - price_c) * pos_c.size
        current_dd = (peak_equity - mtm_check) / peak_equity if peak_equity > 0 else 0

        if current_dd >= DRAWDOWN_BREAKER:
            in_circuit_break = True
        elif in_circuit_break and current_dd < DRAWDOWN_BREAKER * 0.3:
            in_circuit_break = False

        # Losing streak detection: if last 4 trades all losses, increase cooldown
        if len(recent_pnl) >= 4 and all(p < 0 for p in recent_pnl[-4:]):
            COOLDOWN_BARS = 12  # Double cooldown during losing streaks
        else:
            COOLDOWN_BARS = 6

        # Track equity
        # Mark-to-market open positions
        mtm_equity = equity
        for sym, pos in positions.items():
            if sym in processed and ts in processed[sym].index:
                price = processed[sym].loc[ts, "close"]
                if pos.side == "long":
                    mtm_equity += (price - pos.entry_price) * pos.size
                else:
                    mtm_equity += (pos.entry_price - price) * pos.size

        equity_curve.append(mtm_equity)
        if mtm_equity > peak_equity:
            peak_equity = mtm_equity
        dd = (peak_equity - mtm_equity) / peak_equity if peak_equity > 0 else 0
        max_drawdown = max(max_drawdown, dd)

    # Close all open positions at end
    for sym, pos in list(positions.items()):
        if sym in processed:
            last = processed[sym].iloc[-1]
            if pos.side == "long":
                pnl = (last["close"] - pos.entry_price) * pos.size
            else:
                pnl = (pos.entry_price - last["close"]) * pos.size
            pnl -= abs(last["close"] * pos.size) * COMMISSION
            equity += pnl
            trades.append({
                "symbol": sym,
                "entry_time": pos.entry_time,
                "exit_time": processed[sym].index[-1],
                "side": pos.side,
                "entry": pos.entry_price,
                "exit": last["close"],
                "pnl": pnl,
                "reason": "eod",
            })

    # --- Compute metrics ---
    trades_df = pd.DataFrame(trades) if trades else pd.DataFrame()
    n_trades = len(trades_df)
    total_return = ((equity - initial_capital) / initial_capital) * 100

    if n_trades > 0:
        win_rate = (trades_df["pnl"] > 0).sum() / n_trades * 100
        avg_win = trades_df.loc[trades_df["pnl"] > 0, "pnl"].mean() if (trades_df["pnl"] > 0).any() else 0
        avg_loss = trades_df.loc[trades_df["pnl"] < 0, "pnl"].mean() if (trades_df["pnl"] < 0).any() else 0
        profit_factor = (
            trades_df.loc[trades_df["pnl"] > 0, "pnl"].sum() /
            abs(trades_df.loc[trades_df["pnl"] < 0, "pnl"].sum())
            if (trades_df["pnl"] < 0).any() else float("inf")
        )
    else:
        win_rate = avg_win = avg_loss = profit_factor = 0

    # Sharpe ratio (annualized from 4H equity curve)
    eq_series = pd.Series(equity_curve)
    returns = eq_series.pct_change().dropna()
    if len(returns) > 1 and returns.std() > 0:
        bars_per_year = 6 * 365  # 4H bars
        sharpe = (returns.mean() / returns.std()) * np.sqrt(bars_per_year)
    else:
        sharpe = 0.0

    # Per-asset breakdown
    asset_pnl = {}
    if n_trades > 0:
        for sym in trades_df["symbol"].unique():
            sym_trades = trades_df[trades_df["symbol"] == sym]
            asset_pnl[sym] = round(sym_trades["pnl"].sum(), 2)

    return {
        "strategy_name": "Hybrid Ensemble: Multi-Signal Adaptive Weighting (MSAW)",
        "assets": list(asset_data.keys()),
        "timeframe": "4H",
        "period": f"{start_date} to {end_date}",
        "initial_capital": initial_capital,
        "final_equity": round(equity, 2),
        "total_return_pct": round(total_return, 2),
        "sharpe_ratio": round(sharpe, 2),
        "max_drawdown_pct": round(max_drawdown * 100, 2),
        "n_trades": n_trades,
        "win_rate_pct": round(win_rate, 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "profit_factor": round(profit_factor, 2),
        "asset_pnl": asset_pnl,
        "trades": trades_df,
        "equity_curve": equity_curve,
    }


# ---------------------------------------------------------------------------
# 7. OUTPUT
# ---------------------------------------------------------------------------

def print_results(r: dict) -> str:
    lines = [
        "=" * 70,
        f"  STRATEGY: {r['strategy_name']}",
        "=" * 70,
        f"  Assets:            {', '.join(r['assets'])}",
        f"  Timeframe:         {r['timeframe']}",
        f"  Period:            {r['period']}",
        f"  Initial Capital:   ${r['initial_capital']:,.2f}",
        f"  Final Equity:      ${r['final_equity']:,.2f}",
        "-" * 70,
        f"  Total Return:      {r['total_return_pct']:+.2f}%",
        f"  Sharpe Ratio:      {r['sharpe_ratio']:.2f}",
        f"  Max Drawdown:      {r['max_drawdown_pct']:.2f}%",
        f"  Profit Factor:     {r['profit_factor']:.2f}",
        f"  Number of Trades:  {r['n_trades']}",
        f"  Win Rate:          {r['win_rate_pct']:.1f}%",
        f"  Avg Win:           ${r['avg_win']:.2f}",
        f"  Avg Loss:          ${r['avg_loss']:.2f}",
        "-" * 70,
        "  Per-Asset P&L:",
    ]
    for sym, pnl in r.get("asset_pnl", {}).items():
        lines.append(f"    {sym}: ${pnl:+.2f}")

    lines.append("=" * 70)

    if not r["trades"].empty:
        lines.append("\n  Trade Summary by Asset:")
        for sym in r["trades"]["symbol"].unique():
            sym_trades = r["trades"][r["trades"]["symbol"] == sym]
            n = len(sym_trades)
            wins = (sym_trades["pnl"] > 0).sum()
            lines.append(f"    {sym}: {n} trades, {wins} wins ({wins/n*100:.0f}%)")

        lines.append("\n  Last 15 Trades:")
        cols = ["symbol", "entry_time", "side", "entry", "exit", "pnl", "reason"]
        lines.append(r["trades"][cols].tail(15).to_string())

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 8. MAIN
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    ASSETS = {
        "BTCUSDT": "BTCUSDT",
        "ETHUSDT": "ETHUSDT",
        "SOLUSDT": "SOLUSDT",
    }

    print("=" * 70)
    print("  Agent 5 - Hybrid Strategist: Downloading data...")
    print("=" * 70)

    asset_data = {}
    for label, symbol in ASSETS.items():
        df = download_binance_klines(
            symbol=symbol,
            interval="4h",
            start="2025-05-01",  # Buffer for indicator warmup
            end="2026-01-02",
        )
        print(f"  {label}: {len(df)} bars ({df.index[0]} to {df.index[-1]})")
        asset_data[label] = df

    print("\nRunning backtest...")
    results = run_backtest(asset_data)
    output = print_results(results)
    print(output)

    # Save results
    out_dir = Path(__file__).parent.parent / "results"
    out_dir.mkdir(exist_ok=True)
    with open(out_dir / "round1_results.txt", "w") as f:
        f.write(output)
    print(f"\nResults saved to {out_dir / 'round1_results.txt'}")
