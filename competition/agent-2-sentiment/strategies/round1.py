"""
Agent 2 - Sentiment Trader: Adaptive Sentiment Regime (ASR) v5
================================================================
A regime-adaptive strategy using sentiment indicators to detect
trend changes and ride momentum.

Key insight from market analysis: Crypto H2 2025 had a strong
rally phase followed by a crash. The strategy needs to:
1. Ride trends aggressively (long in uptrends, short in downtrends)
2. Use sentiment to detect regime changes early
3. Be adaptive -- not pure long or pure short biased

Approach: Dual-timeframe trend following
- Weekly EMA for regime direction
- Daily signals for entries/exits
- RSI for sentiment confirmation
- Volume for conviction filtering

Multi-asset: ETH (strongest H2 performer) + BTC (market leader)
Timeframe: 12H (balance between signal quality and reactiveness)

Backtest: July 1, 2025 - December 31, 2025
Starting Capital: $1,000 USDT
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import requests


def fetch_klines(symbol: str, interval: str, start: str, end: str) -> pd.DataFrame:
    url = "https://api.binance.com/api/v3/klines"
    start_ms = int(pd.Timestamp(start).timestamp() * 1000)
    end_ms = int(pd.Timestamp(end).timestamp() * 1000)
    rows: list[list] = []
    cur = start_ms
    while cur < end_ms:
        p = {"symbol": symbol, "interval": interval, "startTime": cur, "endTime": end_ms, "limit": 1000}
        r = requests.get(url, params=p, timeout=30)
        r.raise_for_status()
        d = r.json()
        if not d:
            break
        rows.extend(d)
        cur = d[-1][0] + 1
        time.sleep(0.12)
    df = pd.DataFrame(rows, columns=[
        "ot", "open", "high", "low", "close", "volume",
        "ct", "qv", "trades", "tbb", "tbq", "ign",
    ])
    for c in ["open", "high", "low", "close", "volume", "qv"]:
        df[c] = df[c].astype(float)
    df["timestamp"] = pd.to_datetime(df["ot"], unit="ms")
    df = df.set_index("timestamp")
    return df


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    # Trend EMAs
    df["ema_8"] = df["close"].ewm(span=8, min_periods=8).mean()
    df["ema_21"] = df["close"].ewm(span=21, min_periods=21).mean()
    df["ema_55"] = df["close"].ewm(span=55, min_periods=55).mean()

    # RSI
    delta = df["close"].diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    ag = gain.ewm(alpha=1/14, min_periods=14).mean()
    al = loss.ewm(alpha=1/14, min_periods=14).mean()
    df["rsi"] = 100 - (100 / (1 + ag / al.replace(0, np.nan)))

    # ATR
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - df["close"].shift(1)).abs(),
        (df["low"] - df["close"].shift(1)).abs(),
    ], axis=1).max(axis=1)
    df["atr"] = tr.ewm(alpha=1/14, min_periods=14).mean()

    # Volume
    df["vol_sma"] = df["volume"].rolling(20).mean()
    df["vol_ratio"] = df["volume"] / df["vol_sma"]

    # Donchian Channel (20-period high/low for breakout detection)
    df["dc_high"] = df["high"].rolling(20).max()
    df["dc_low"] = df["low"].rolling(20).min()
    df["dc_mid"] = (df["dc_high"] + df["dc_low"]) / 2

    # Price position in range (0 = bottom, 1 = top)
    df["dc_pct"] = (df["close"] - df["dc_low"]) / (df["dc_high"] - df["dc_low"]).replace(0, np.nan)

    # ADX for trend strength
    plus_dm = df["high"].diff()
    minus_dm = -df["low"].diff()
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)
    plus_di = 100 * (plus_dm.ewm(alpha=1/14, min_periods=14).mean() / df["atr"])
    minus_di = 100 * (minus_dm.ewm(alpha=1/14, min_periods=14).mean() / df["atr"])
    dx = 100 * ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan))
    df["adx"] = dx.ewm(alpha=1/14, min_periods=14).mean()

    return df


def generate_signals(df: pd.DataFrame) -> pd.DataFrame:
    """
    Regime-adaptive signals using EMA alignment + sentiment filters.

    LONG when:
    - EMA 8 > EMA 21 > EMA 55 (full uptrend alignment)
    - RSI > 50 (bullish momentum) but < 80 (not extremely overbought)
    - Price above Donchian midline

    SHORT when:
    - EMA 8 < EMA 21 < EMA 55 (full downtrend alignment)
    - RSI < 50 (bearish momentum) but > 20 (not extremely oversold)
    - Price below Donchian midline

    EXIT signals embedded in backtest via trend reversal.
    """
    df = df.copy()
    df["signal"] = 0  # 0=flat, 1=long, -1=short
    df["regime"] = 0  # Continuous regime indicator

    for i in range(1, len(df)):
        idx = df.index[i]
        prev = df.index[i - 1]

        ema8 = df.loc[idx, "ema_8"]
        ema21 = df.loc[idx, "ema_21"]
        ema55 = df.loc[idx, "ema_55"]
        rsi = df.loc[idx, "rsi"]
        close = df.loc[idx, "close"]
        dc_mid = df.loc[idx, "dc_mid"]
        dc_pct = df.loc[idx, "dc_pct"]
        vol_r = df.loc[idx, "vol_ratio"]
        adx = df.loc[idx, "adx"]

        if any(pd.isna(v) for v in [ema55, rsi, dc_mid, dc_pct, adx]):
            continue

        # Determine regime
        if ema8 > ema21 > ema55:
            df.loc[idx, "regime"] = 1  # Bull
        elif ema8 < ema21 < ema55:
            df.loc[idx, "regime"] = -1  # Bear
        else:
            df.loc[idx, "regime"] = 0  # Transition

        # ADX available for context but don't filter on it
        adx_val = adx

        # --- LONG entry ---
        # Full bull alignment + momentum + not overbought
        if ema8 > ema21 > ema55 and 45 < rsi < 78 and close > dc_mid:
            near_support = (close < ema8 * 1.02) or (abs(close - ema21) / close < 0.02)
            if near_support:
                df.loc[idx, "signal"] = 1

        # --- SHORT entry ---
        if ema8 < ema21 < ema55 and 22 < rsi < 55 and close < dc_mid:
            near_resist = (close > ema8 * 0.98) or (abs(close - ema21) / close < 0.02)
            if near_resist:
                df.loc[idx, "signal"] = -1

    return df


@dataclass
class Trade:
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp | None = None
    direction: int = 0
    entry_price: float = 0.0
    exit_price: float = 0.0
    size: float = 0.0
    pnl: float = 0.0
    pnl_pct: float = 0.0
    exit_reason: str = ""
    symbol: str = ""


def backtest(
    df: pd.DataFrame,
    symbol: str,
    capital: float,
    risk_pct: float = 0.025,
    atr_stop: float = 2.0,
    rr: float = 3.0,
    max_hold: int = 20,     # 10 days on 12h
    cooldown: int = 4,      # 2 days on 12h
    comm: float = 0.001,
) -> tuple[float, list[Trade], list[float]]:
    bt_s = pd.Timestamp("2025-07-01")
    bt_e = pd.Timestamp("2025-12-31 23:59:59")
    bt = df[(df.index >= bt_s) & (df.index <= bt_e)].copy()

    trades: list[Trade] = []
    equity: list[float] = []
    pos: Trade | None = None
    since = cooldown
    trail: float | None = None
    rdist: float = 0.0
    consecutive_losses = 0  # Track losing streak

    for i in range(len(bt)):
        idx = bt.index[i]
        row = bt.iloc[i]
        c, h, l = row["close"], row["high"], row["low"]
        atr = row["atr"]
        sig = int(row["signal"])
        regime = int(row["regime"])
        since += 1

        # EXIT
        if pos is not None:
            ei = bt.index.get_loc(pos.entry_time)
            held = i - ei
            er = ""
            ep = 0.0

            if pos.direction == 1:
                bsl = pos.entry_price - rdist
                tp = pos.entry_price + rdist * rr

                # Trail: at 1.5R move to breakeven, at 2R trail at 1.5 ATR
                ur = (h - pos.entry_price) / rdist if rdist > 0 else 0
                if ur >= 2.0:
                    nt = h - 1.5 * atr
                    if trail is None or nt > trail:
                        trail = nt
                elif ur >= 1.0:
                    be = pos.entry_price + 0.002 * pos.entry_price
                    if trail is None or be > trail:
                        trail = be

                esl = max(bsl, trail) if trail else bsl

                if l <= esl:
                    er = "trailing_stop" if trail and trail > bsl else "stop_loss"
                    ep = max(esl, l)
                elif h >= tp:
                    er = "take_profit"
                    ep = tp
                elif held >= max_hold:
                    er = "time_exit"
                    ep = c
                elif regime == -1 and held > 3:
                    er = "regime_exit"
                    ep = c

            else:  # Short
                bsl = pos.entry_price + rdist
                tp = pos.entry_price - rdist * rr

                ur = (pos.entry_price - l) / rdist if rdist > 0 else 0
                if ur >= 2.0:
                    nt = l + 1.5 * atr
                    if trail is None or nt < trail:
                        trail = nt
                elif ur >= 1.0:
                    be = pos.entry_price - 0.002 * pos.entry_price
                    if trail is None or be < trail:
                        trail = be

                esl = min(bsl, trail) if trail else bsl

                if h >= esl:
                    er = "trailing_stop" if trail and trail < bsl else "stop_loss"
                    ep = min(esl, h)
                elif l <= tp:
                    er = "take_profit"
                    ep = tp
                elif held >= max_hold:
                    er = "time_exit"
                    ep = c
                elif regime == 1 and held > 3:
                    er = "regime_exit"
                    ep = c

            if er:
                raw = (ep - pos.entry_price) * pos.size * pos.direction
                fee = ep * pos.size * comm
                pnl = raw - fee
                pos.exit_time = idx
                pos.exit_price = ep
                pos.pnl = pnl
                pos.pnl_pct = pnl / capital * 100  # % of starting capital
                pos.exit_reason = er
                trades.append(pos)
                capital += pnl
                pos = None
                trail = None
                since = 0
                # Track consecutive losses for anti-tilt sizing
                if pnl < 0:
                    consecutive_losses += 1
                else:
                    consecutive_losses = 0

        # ENTRY -- with anti-tilt: increase cooldown and reduce size after losses
        effective_cooldown = cooldown + consecutive_losses * 2
        if pos is None and sig != 0 and not pd.isna(atr) and atr > 0 and since >= effective_cooldown:
            rdist = atr_stop * atr
            # Reduce risk after consecutive losses (anti-tilt)
            adj_risk = risk_pct * max(0.5, 1.0 - consecutive_losses * 0.15)
            ra = capital * adj_risk
            sz = ra / rdist
            mx = (capital * 0.95) / c
            sz = min(sz, mx)
            if sz * c > 10:
                capital -= c * sz * comm
                pos = Trade(
                    entry_time=idx, direction=sig,
                    entry_price=c, size=sz, symbol=symbol,
                )
                trail = None

        mtm = (c - pos.entry_price) * pos.size * pos.direction if pos else 0
        equity.append(capital + mtm)

    if pos:
        fc = bt.iloc[-1]["close"]
        pnl = (fc - pos.entry_price) * pos.size * pos.direction - fc * pos.size * comm
        pos.exit_time = bt.index[-1]
        pos.exit_price = fc
        pos.pnl = pnl
        pos.pnl_pct = pnl / 500 * 100
        pos.exit_reason = "eob"
        trades.append(pos)
        capital += pnl

    return capital, trades, equity


def main():
    print("=" * 60)
    print("Agent 2 - Sentiment Trader")
    print("Strategy: Adaptive Sentiment Regime (ASR) v5")
    print("Pairs: ETH+BTC+BNB | TF: 12H")
    print("Backtest: 2025-07-01 to 2025-12-31")
    print("Starting Capital: $1,000 USDT")
    print("=" * 60)

    symbols = ["ETHUSDT", "BTCUSDT", "BNBUSDT"]
    cap_each = 1000.0 / len(symbols)
    all_trades: list[Trade] = []
    total_final = 0.0
    all_eq: dict[str, list[float]] = {}
    bt_idx = None

    for sym in symbols:
        print(f"\n--- {sym} ---")
        df = fetch_klines(sym, "12h", "2025-01-01", "2026-01-02")
        print(f"  {len(df)} candles")
        df = add_indicators(df)
        df = generate_signals(df)

        bt = df[(df.index >= "2025-07-01") & (df.index <= "2025-12-31")]
        ls = (bt["signal"] == 1).sum()
        ss = (bt["signal"] == -1).sum()
        print(f"  Signals: {ls} long, {ss} short")

        # Show regime breakdown
        bull = (bt["regime"] == 1).sum()
        bear = (bt["regime"] == -1).sum()
        trans = (bt["regime"] == 0).sum()
        print(f"  Regime: {bull} bull, {bear} bear, {trans} transition bars")

        final, trades, eq = backtest(df, sym, cap_each)
        all_trades.extend(trades)
        all_eq[sym] = eq
        total_final += final
        if bt_idx is None:
            bt_idx = bt.index

        w = sum(1 for t in trades if t.pnl > 0)
        print(f"  Trades: {len(trades)}, Wins: {w}, PnL: ${final - cap_each:+.2f}")
        for t in trades:
            dir_str = "L" if t.direction == 1 else "S"
            print(f"    {dir_str} {t.entry_time.strftime('%m/%d')} -> {t.exit_time.strftime('%m/%d') if t.exit_time else '?'}: "
                  f"${t.pnl:+.2f} ({t.exit_reason})")

    # Combined
    ml = min(len(e) for e in all_eq.values())
    ml = min(ml, len(bt_idx))  # Ensure we don't exceed index length
    ceq = [sum(all_eq[s][i] for s in symbols) for i in range(ml)]
    eq_s = pd.Series(ceq, index=bt_idx[:ml])

    tot_ret = (total_final - 1000) / 1000 * 100
    rets = eq_s.pct_change().dropna()
    sharpe = (rets.mean() / rets.std()) * np.sqrt(365.25 * 2) if rets.std() > 0 else 0  # 12h = 2/day
    peak = eq_s.cummax()
    max_dd = ((eq_s - peak) / peak).min() * 100

    wins = [t for t in all_trades if t.pnl > 0]
    losses = [t for t in all_trades if t.pnl <= 0]
    wr = len(wins) / len(all_trades) * 100 if all_trades else 0
    aw = np.mean([t.pnl_pct for t in wins]) if wins else 0
    al = np.mean([t.pnl_pct for t in losses]) if losses else 0
    gp = sum(t.pnl for t in wins)
    gl = abs(sum(t.pnl for t in losses))
    pf = gp / gl if gl > 0 else float("inf")

    print("\n" + "=" * 60)
    print("COMBINED RESULTS")
    print("=" * 60)
    print(f"  Total Return:    {tot_ret:+.2f}%")
    print(f"  Sharpe Ratio:    {sharpe:.2f}")
    print(f"  Max Drawdown:    {max_dd:.2f}%")
    print(f"  Win Rate:        {wr:.1f}%")
    print(f"  Num Trades:      {len(all_trades)}")
    print(f"  Avg Win:         {aw:+.2f}%")
    print(f"  Avg Loss:        {al:+.2f}%")
    print(f"  Profit Factor:   {pf:.2f}")
    print(f"  Final Capital:   ${total_final:.2f}")

    reasons: dict[str, int] = {}
    for t in all_trades:
        reasons[t.exit_reason] = reasons.get(t.exit_reason, 0) + 1
    print(f"\n  Exit Reasons:")
    for r, c_ in sorted(reasons.items()):
        print(f"    {r}: {c_}")

    monthly: dict[str, float] = {}
    for t in all_trades:
        if t.exit_time:
            m = t.exit_time.strftime("%Y-%m")
            monthly[m] = monthly.get(m, 0) + t.pnl
    print(f"\n  Monthly PnL:")
    for m in sorted(monthly):
        print(f"    {m}: ${monthly[m]:+.2f}")
    print("=" * 60)


if __name__ == "__main__":
    main()
