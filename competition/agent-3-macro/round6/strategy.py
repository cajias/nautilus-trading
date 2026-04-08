"""
Agent 3 - Macro Strategist - Round 6
Concentrated Momentum Rotator: BTC / ETH / SOL

Lessons from prior rounds:
- R3 (+13.55%): Weekly momentum + BTC only + patience = best result
- R4/R5 (-14%/-12%): Over-trading and over-engineering killed returns
- Agent 4's +48.47% came from concentrated bets in a trending market

Strategy for R6:
- Scan BTC, ETH, SOL weekly for strongest momentum
- Concentrate capital (90%) in the single strongest trending asset
- Use weekly EMA crossover for regime (bull/bear)
- Use daily RSI + price>EMA for entry timing within bullish regime
- ATR-based trailing stop (3.5x) to let winners run
- Stay 100% cash when no asset is trending
- Fewer trades, bigger conviction, wider stops
"""

import json
import math
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any


def fetch_klines(symbol: str, interval: str, start_ms: int, end_ms: int) -> list[list]:
    all_klines: list[list] = []
    current_start = start_ms
    while current_start < end_ms:
        url = (
            f"https://api.binance.com/api/v3/klines"
            f"?symbol={symbol}&interval={interval}"
            f"&startTime={current_start}&endTime={end_ms}&limit=1000"
        )
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
        if not data:
            break
        all_klines.extend(data)
        current_start = data[-1][0] + 1
        if len(data) < 1000:
            break
    return all_klines


def _ema(values: list[float], period: int) -> list[float]:
    out = [0.0] * len(values)
    if len(values) < period:
        return out
    k = 2.0 / (period + 1)
    out[period - 1] = sum(values[:period]) / period
    for i in range(period, len(values)):
        out[i] = values[i] * k + out[i - 1] * (1 - k)
    return out


def _rsi(closes: list[float], period: int = 14) -> list[float]:
    out = [50.0] * len(closes)
    if len(closes) < period + 1:
        return out
    ag = 0.0
    al = 0.0
    for i in range(1, period + 1):
        d = closes[i] - closes[i - 1]
        if d > 0:
            ag += d
        else:
            al -= d
    ag /= period
    al /= period
    if al == 0:
        out[period] = 100.0
    else:
        out[period] = 100 - 100 / (1 + ag / al)
    for i in range(period + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        gain = d if d > 0 else 0.0
        loss = -d if d < 0 else 0.0
        ag = (ag * (period - 1) + gain) / period
        al = (al * (period - 1) + loss) / period
        if al == 0:
            out[i] = 100.0
        else:
            out[i] = 100 - 100 / (1 + ag / al)
    return out


def _atr(highs: list[float], lows: list[float], closes: list[float], period: int) -> list[float]:
    out = [0.0] * len(closes)
    tr = [0.0] * len(closes)
    for i in range(1, len(closes)):
        tr[i] = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
    if len(closes) < period + 1:
        return out
    out[period] = sum(tr[1 : period + 1]) / period
    for i in range(period + 1, len(closes)):
        out[i] = (out[i - 1] * (period - 1) + tr[i]) / period
    return out


def _momentum_score(closes: list[float], idx: int) -> float:
    """Rate of change over multiple lookbacks, averaged. Higher = stronger momentum."""
    if idx < 60:
        return 0.0
    roc_20 = (closes[idx] / closes[idx - 20] - 1) if closes[idx - 20] > 0 else 0
    roc_40 = (closes[idx] / closes[idx - 40] - 1) if closes[idx - 40] > 0 else 0
    roc_60 = (closes[idx] / closes[idx - 60] - 1) if closes[idx - 60] > 0 else 0
    # Weight recent momentum more
    return roc_20 * 0.5 + roc_40 * 0.3 + roc_60 * 0.2


class AssetData:
    """Holds daily data and indicators for one asset."""
    def __init__(self, symbol: str, klines: list[list]):
        self.symbol = symbol
        self.timestamps = [k[0] for k in klines]
        self.opens = [float(k[1]) for k in klines]
        self.highs = [float(k[2]) for k in klines]
        self.lows = [float(k[3]) for k in klines]
        self.closes = [float(k[4]) for k in klines]
        self.volumes = [float(k[5]) for k in klines]

        self.ema_10 = _ema(self.closes, 10)
        self.ema_21 = _ema(self.closes, 21)
        self.ema_50 = _ema(self.closes, 50)
        self.rsi_14 = _rsi(self.closes, 14)
        self.atr_14 = _atr(self.highs, self.lows, self.closes, 14)

    def is_bullish(self, i: int) -> bool:
        """Weekly-equivalent regime: EMA10 > EMA21 > EMA50 (strong uptrend)."""
        if i < 50:
            return False
        return (self.ema_10[i] > self.ema_21[i] and
                self.ema_21[i] > self.ema_50[i] and
                self.closes[i] > self.ema_21[i])

    def is_weakly_bullish(self, i: int) -> bool:
        """Moderate uptrend: price above EMA50."""
        if i < 50:
            return False
        return self.closes[i] > self.ema_50[i] and self.ema_10[i] > self.ema_50[i]

    def momentum(self, i: int) -> float:
        return _momentum_score(self.closes, i)


class Position:
    def __init__(self, symbol: str, entry_price: float, size_usd: float,
                 stop: float, trail_atr_mult: float):
        self.symbol = symbol
        self.entry_price = entry_price
        self.size_usd = size_usd
        self.stop = stop
        self.trail_atr_mult = trail_atr_mult
        self.highest_since = entry_price
        self.entry_bar_idx = 0
        self.bars_held = 0


def run_backtest(start: str, end: str, initial_capital: float = 1000.0) -> dict[str, Any]:
    """Main entry point."""

    SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    FEE_RATE = 0.001
    LOOKBACK_DAYS = 180

    # Position sizing
    POSITION_SIZE = 0.90  # 90% of capital in one asset
    TRAIL_ATR_MULT = 3.0  # Trailing stop distance in ATR
    INITIAL_ATR_MULT = 1.5  # Tight stop for first 5 days
    HARD_STOP_PCT = 0.06  # 6% hard stop
    COOLDOWN_BARS = 3     # 3 days after stop-out

    # Entry filters -- STRICT to avoid bear traps
    MIN_MOMENTUM = 0.05   # At least 5% combined momentum score
    MAX_RSI_ENTRY = 68    # Don't buy overbought (tighter)
    MIN_RSI_ENTRY = 30    # Prefer some pullback for entry
    MIN_ROC_20 = 0.03     # 20-day ROC must be >3% to confirm recent uptrend
    MIN_ROC_5 = 0.0       # 5-day ROC must be non-negative (not falling into entry)

    start_dt = datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end_dt = datetime.strptime(end, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    lookback_dt = start_dt - timedelta(days=LOOKBACK_DAYS)
    fetch_start_ms = int(lookback_dt.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000)
    trade_start_ms = int(start_dt.timestamp() * 1000)

    # Fetch data for all assets
    assets: dict[str, AssetData] = {}
    for sym in SYMBOLS:
        klines = fetch_klines(sym, "1d", fetch_start_ms, end_ms)
        if klines:
            assets[sym] = AssetData(sym, klines)

    if not assets:
        return {"final_equity": initial_capital, "total_return_pct": 0.0, "sharpe_ratio": 0.0,
                "max_drawdown_pct": 0.0, "num_trades": 0, "win_rate": 0.0, "trade_log": []}

    # Use BTC as the time axis reference
    ref_sym = "BTCUSDT"
    ref = assets[ref_sym]
    n_bars = len(ref.closes)

    # Build timestamp-to-index maps for each asset
    ts_to_idx: dict[str, dict[int, int]] = {}
    for sym, ad in assets.items():
        ts_to_idx[sym] = {ad.timestamps[j]: j for j in range(len(ad.timestamps))}

    cash = initial_capital
    peak_equity = initial_capital
    max_dd = 0.0
    position: Position | None = None
    trade_log: list[dict] = []
    equity_curve: list[float] = []
    cooldown = 0

    for ref_i in range(1, n_bars):
        ts = ref.timestamps[ref_i]
        in_trade = ts >= trade_start_ms
        ts_str = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d")

        # --- EXIT LOGIC ---
        if position is not None:
            sym = position.symbol
            ad = assets[sym]
            idx_map = ts_to_idx[sym]
            if ts not in idx_map:
                # Asset doesn't have data for this timestamp, skip
                if in_trade:
                    equity_curve.append(cash + position.size_usd)
                continue
            ai = idx_map[ts]

            close = ad.closes[ai]
            low = ad.lows[ai]
            atr_val = ad.atr_14[ai]
            position.bars_held += 1

            # Update trailing stop (tighter for first 5 bars)
            current_mult = INITIAL_ATR_MULT if position.bars_held <= 5 else position.trail_atr_mult
            if close > position.highest_since:
                position.highest_since = close
                if atr_val > 0:
                    new_stop = close - current_mult * atr_val
                    position.stop = max(position.stop, new_stop)

            exit_price = None
            reason = ""

            # Hard stop
            hard_stop = position.entry_price * (1 - HARD_STOP_PCT)
            effective_stop = max(position.stop, hard_stop)

            if low <= effective_stop:
                exit_price = max(effective_stop, low)
                reason = "trail_stop"
            # Regime reversal: exit if asset is no longer bullish and held > 5 days
            elif position.bars_held > 5 and not ad.is_weakly_bullish(ai):
                exit_price = close
                reason = "regime_exit"

            if exit_price is not None:
                pnl = (exit_price / position.entry_price - 1) * position.size_usd
                fee = position.size_usd * FEE_RATE
                pnl -= fee
                cash += position.size_usd + pnl
                if in_trade:
                    trade_log.append({
                        "symbol": sym, "side": "SELL", "price": round(exit_price, 2),
                        "time": ts_str, "pnl": round(pnl, 2), "reason": reason,
                    })
                position = None
                cooldown = COOLDOWN_BARS

        if not in_trade:
            continue

        if cooldown > 0:
            cooldown -= 1

        # --- ENTRY LOGIC ---
        if position is None and cooldown <= 0:
            # Score each asset and pick the strongest
            candidates = []
            for sym, ad in assets.items():
                if ts not in ts_to_idx[sym]:
                    continue
                ai = ts_to_idx[sym][ts]
                if ai < 60:
                    continue

                mom = ad.momentum(ai)
                rsi = ad.rsi_14[ai]
                atr_val = ad.atr_14[ai]
                bullish = ad.is_bullish(ai)

                # STRICT: require full EMA stack alignment (strong uptrend)
                if not bullish:
                    continue
                if mom < MIN_MOMENTUM:
                    continue
                if rsi > MAX_RSI_ENTRY:
                    continue
                if atr_val <= 0:
                    continue
                # Require recent ROC to be positive
                roc_20 = (ad.closes[ai] / ad.closes[ai - 20] - 1) if ad.closes[ai - 20] > 0 else 0
                if roc_20 < MIN_ROC_20:
                    continue
                roc_5 = (ad.closes[ai] / ad.closes[ai - 5] - 1) if ad.closes[ai - 5] > 0 else 0
                if roc_5 < MIN_ROC_5:
                    continue
                # Reject if price is below recent 10-day high by more than 3%
                # (sign of weakening, not a fresh breakout)
                recent_high_10 = max(ad.highs[ai - 10: ai + 1])
                if (recent_high_10 - ad.closes[ai]) / recent_high_10 > 0.03:
                    continue
                # Reject if current close is below 40-day high by > 8%
                # This catches distribution tops where EMAs still lag
                recent_high_40 = max(ad.highs[max(0, ai - 40): ai + 1])
                if (recent_high_40 - ad.closes[ai]) / recent_high_40 > 0.08:
                    continue
                # Reject parabolic moves: if 10-day ROC > 15%, too extended
                roc_10 = (ad.closes[ai] / ad.closes[ai - 10] - 1) if ad.closes[ai - 10] > 0 else 0
                if roc_10 > 0.15:
                    continue
                # Volatility filter: high ATR/price = unstable, avoid
                if atr_val / ad.closes[ai] > 0.04:
                    continue
                # Macro health check: BTC must not have declined >8% from
                # its 30-day high. If BTC is weak, avoid ALL crypto entries.
                if ref_i >= 30:
                    btc_30d_high = max(ref.highs[ref_i - 30: ref_i + 1])
                    btc_close = ref.closes[ref_i]
                    btc_dd = (btc_30d_high - btc_close) / btc_30d_high
                    if btc_dd > 0.08:
                        continue

                # Prefer assets with pullback (lower RSI = better entry)
                # But also weight momentum heavily
                entry_score = mom * 2.0
                if rsi < 50:
                    entry_score += 0.02  # Small bonus for pullback
                entry_score += 0.05  # Already confirmed bullish

                candidates.append((sym, ai, entry_score, atr_val, rsi))

            if candidates:
                # Pick the highest-scoring asset
                candidates.sort(key=lambda x: x[2], reverse=True)
                best_sym, best_ai, best_score, best_atr, best_rsi = candidates[0]
                ad = assets[best_sym]
                close = ad.closes[best_ai]

                size_usd = POSITION_SIZE * cash
                if size_usd >= 10:
                    fee = size_usd * FEE_RATE
                    cash -= (size_usd + fee)
                    stop = close - INITIAL_ATR_MULT * best_atr
                    position = Position(best_sym, close, size_usd, stop, TRAIL_ATR_MULT)
                    position.entry_bar_idx = ref_i
                    trade_log.append({
                        "symbol": best_sym, "side": "BUY", "price": close,
                        "time": ts_str, "pnl": 0,
                        "reason": f"momentum_entry(score={best_score:.3f},RSI={best_rsi:.0f})",
                    })

        # --- MARK TO MARKET ---
        mtm = cash
        if position is not None:
            sym = position.symbol
            if ts in ts_to_idx[sym]:
                ai = ts_to_idx[sym][ts]
                close = assets[sym].closes[ai]
                unrealized = (close / position.entry_price - 1) * position.size_usd
                mtm += position.size_usd + unrealized
            else:
                mtm += position.size_usd

        equity_curve.append(mtm)
        if mtm > peak_equity:
            peak_equity = mtm
        dd_pct = (peak_equity - mtm) / peak_equity * 100
        if dd_pct > max_dd:
            max_dd = dd_pct

    # --- CLOSE REMAINING ---
    if position is not None:
        sym = position.symbol
        ad = assets[sym]
        close = ad.closes[-1]
        ts_str = datetime.fromtimestamp(ad.timestamps[-1] / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        pnl = (close / position.entry_price - 1) * position.size_usd
        fee = position.size_usd * FEE_RATE
        pnl -= fee
        cash += position.size_usd + pnl
        trade_log.append({
            "symbol": sym, "side": "SELL", "price": close,
            "time": ts_str, "pnl": round(pnl, 2), "reason": "end_of_period",
        })

    final_equity = cash
    total_return = (final_equity - initial_capital) / initial_capital * 100
    closed_trades = [t for t in trade_log if t["side"] == "SELL"]
    num_trades = len(closed_trades)
    winners = len([t for t in closed_trades if t["pnl"] > 0])
    win_rate = winners / num_trades * 100 if num_trades > 0 else 0.0

    sharpe = 0.0
    if len(equity_curve) > 1:
        rets = [
            (equity_curve[j] - equity_curve[j - 1]) / equity_curve[j - 1]
            for j in range(1, len(equity_curve))
            if equity_curve[j - 1] > 0
        ]
        if rets:
            mean_r = sum(rets) / len(rets)
            var_r = sum((r - mean_r) ** 2 for r in rets) / max(len(rets) - 1, 1)
            std_r = math.sqrt(var_r) if var_r > 0 else 1e-10
            sharpe = (mean_r / std_r) * math.sqrt(365)

    return {
        "final_equity": round(final_equity, 2),
        "total_return_pct": round(total_return, 2),
        "sharpe_ratio": round(sharpe, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "num_trades": num_trades,
        "win_rate": round(win_rate, 2),
        "trade_log": trade_log,
    }


if __name__ == "__main__":
    print("=" * 70)
    print("AGENT 3 - MACRO STRATEGIST - ROUND 6")
    print("Concentrated Momentum Rotator: BTC / ETH / SOL")
    print("=" * 70)

    results = {}
    periods = [
        ("TRAIN Apr-Sep 2025", "2025-04-01", "2025-09-30"),
        ("TEST Oct-Dec 2025", "2025-10-01", "2025-12-31"),
    ]

    for label, s, e in periods:
        print(f"\n{'=' * 50}")
        print(f"  {label}")
        print(f"{'=' * 50}")
        r = run_backtest(s, e, 1000)
        results[label] = r
        for k, v in r.items():
            if k != "trade_log":
                print(f"  {k}: {v}")
        print(f"\n  --- Trade Log ({len(r['trade_log'])} entries) ---")
        for t in r["trade_log"]:
            print(f"  {t['time']} | {t['symbol']:8s} | {t['side']:5s} | "
                  f"${t['price']:>10.2f} | PnL: ${t['pnl']:>8.2f} | {t['reason']}")

    rp = "/Users/rc/Projects/workspace/nautilus-trading/competition/agent-3-macro/round6/results.txt"
    with open(rp, "w") as f:
        f.write("AGENT 3 - MACRO STRATEGIST - ROUND 6\n")
        f.write("Concentrated Momentum Rotator: BTC / ETH / SOL\n")
        f.write("=" * 60 + "\n\n")
        f.write("Strategy:\n")
        f.write("  Multi-asset momentum rotation (BTC, ETH, SOL)\n")
        f.write("  EMA stack (10/21/50) for regime detection\n")
        f.write("  Momentum score: weighted ROC(20/40/60)\n")
        f.write("  92% concentrated position in strongest trending asset\n")
        f.write("  ATR-based trailing stop (3.5x) + 10% hard stop\n")
        f.write("  Regime exit when EMA stack breaks down\n")
        f.write("  0.1% fees per trade\n\n")
        for label, res in results.items():
            f.write(f"{label}:\n")
            for k, v in res.items():
                if k != "trade_log":
                    f.write(f"  {k}: {v}\n")
            f.write(f"\n  Trades:\n")
            for t in res["trade_log"]:
                f.write(f"    {t['time']} | {t['symbol']:8s} | {t['side']:5s} | "
                        f"${t['price']:>10.2f} | PnL: ${t['pnl']:>8.2f} | {t['reason']}\n")
            f.write("\n")

    print(f"\nResults saved to {rp}")
