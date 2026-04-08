"""
Agent 3 - Macro Strategist Round 2: Macro Regime Trend Follower

Key design:
- LONG ONLY in crypto (structural upward bias)
- Daily timeframe for macro perspective
- Extra lookback fetched before start date for indicator warmup
- Regime: price > 50 SMA and 20 EMA > 50 SMA = uptrend
- Entry: Pullback to 20 EMA, breakout new highs, trend start crossover
- Exit: ATR trailing stop, or trend reversal below 50 SMA
- Multi-asset: BTC, ETH, SOL with capital rotation
"""

import json
import math
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any


def fetch_klines(symbol: str, interval: str, start_ms: int, end_ms: int) -> list[list]:
    all_klines = []
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


def _sma(values: list[float], period: int) -> list[float]:
    out = [0.0] * len(values)
    for i in range(period - 1, len(values)):
        out[i] = sum(values[i - period + 1:i + 1]) / period
    return out


def _atr(highs: list[float], lows: list[float], closes: list[float], period: int) -> list[float]:
    out = [0.0] * len(closes)
    tr = [0.0] * len(closes)
    for i in range(1, len(closes)):
        tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
    if len(closes) < period + 1:
        return out
    out[period] = sum(tr[1:period + 1]) / period
    for i in range(period + 1, len(closes)):
        out[i] = (out[i - 1] * (period - 1) + tr[i]) / period
    return out


def _rsi(closes: list[float], period: int = 14) -> list[float]:
    out = [50.0] * len(closes)
    if len(closes) < period + 1:
        return out
    gains = [0.0] * len(closes)
    losses = [0.0] * len(closes)
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains[i] = d if d > 0 else 0.0
        losses[i] = -d if d < 0 else 0.0
    ag = sum(gains[1:period + 1]) / period
    al = sum(losses[1:period + 1]) / period
    for i in range(period, len(closes)):
        if i > period:
            ag = (ag * (period - 1) + gains[i]) / period
            al = (al * (period - 1) + losses[i]) / period
        if al == 0:
            out[i] = 100.0
        else:
            out[i] = 100 - 100 / (1 + ag / al)
    return out


def _highest(values: list[float], period: int) -> list[float]:
    out = [0.0] * len(values)
    for i in range(period - 1, len(values)):
        out[i] = max(values[i - period + 1:i + 1])
    return out


class Position:
    def __init__(self, symbol: str, entry_price: float, size_frac: float, stop: float):
        self.symbol = symbol
        self.entry_price = entry_price
        self.size_frac = size_frac
        self.stop = stop
        self.highest_since = entry_price


class MacroTrendFollower:
    """
    Long-only macro trend follower.
    Fetches 90 days of lookback before start for indicator warmup.
    """

    def __init__(
        self,
        symbols: list[str] | None = None,
        ema_fast: int = 20,
        sma_slow: int = 50,
        atr_period: int = 14,
        rsi_period: int = 14,
        trail_atr_mult: float = 3.5,
        breakout_period: int = 20,
        max_positions: int = 3,
        position_size: float = 0.30,
        fee_rate: float = 0.001,
        lookback_days: int = 90,
    ):
        self.symbols = symbols or ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
        self.ema_fast = ema_fast
        self.sma_slow = sma_slow
        self.atr_period = atr_period
        self.rsi_period = rsi_period
        self.trail_atr_mult = trail_atr_mult
        self.breakout_period = breakout_period
        self.max_positions = max_positions
        self.position_size = position_size
        self.fee_rate = fee_rate
        self.lookback_days = lookback_days

    def run(self, start: str, end: str, initial_capital: float = 1000.0) -> dict[str, Any]:
        start_dt = datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        end_dt = datetime.strptime(end, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        # Fetch extra lookback for indicator warmup
        lookback_dt = start_dt - timedelta(days=self.lookback_days)
        fetch_start_ms = int(lookback_dt.timestamp() * 1000)
        end_ms = int(end_dt.timestamp() * 1000)
        trade_start_ms = int(start_dt.timestamp() * 1000)

        data: dict[str, dict] = {}
        for symbol in self.symbols:
            klines = fetch_klines(symbol, "1d", fetch_start_ms, end_ms)
            if not klines:
                continue
            closes = [float(k[4]) for k in klines]
            highs = [float(k[2]) for k in klines]
            lows = [float(k[3]) for k in klines]
            timestamps = [k[0] for k in klines]

            data[symbol] = {
                "closes": closes, "highs": highs, "lows": lows,
                "timestamps": timestamps,
                "ema20": _ema(closes, self.ema_fast),
                "sma50": _sma(closes, self.sma_slow),
                "atr14": _atr(highs, lows, closes, self.atr_period),
                "rsi14": _rsi(closes, self.rsi_period),
                "hi20": _highest(highs, self.breakout_period),
            }

        if not data:
            return self._empty_result(initial_capital)

        # Build index maps
        all_ts = sorted(set(ts for sd in data.values() for ts in sd["timestamps"]))
        ts_idx = {sym: {ts: i for i, ts in enumerate(sd["timestamps"])} for sym, sd in data.items()}

        equity = initial_capital
        peak_equity = initial_capital
        max_dd = 0.0
        positions: dict[str, Position] = {}
        trade_log: list[dict] = []
        equity_curve: list[float] = []
        warmup = self.sma_slow + 2

        for ts in all_ts:
            # Only trade after the actual start date (lookback is just for indicators)
            in_trade_period = ts >= trade_start_ms

            # Check exits first
            for sym in list(positions.keys()):
                if sym not in data or ts not in ts_idx[sym]:
                    continue
                idx = ts_idx[sym][ts]
                pos = positions[sym]
                sd = data[sym]
                close = sd["closes"][idx]
                atr_val = sd["atr14"][idx]
                sma50 = sd["sma50"][idx]
                ema20 = sd["ema20"][idx]
                ts_str = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d")

                # Update trailing stop
                if close > pos.highest_since:
                    pos.highest_since = close
                    if atr_val > 0:
                        new_stop = close - self.trail_atr_mult * atr_val
                        pos.stop = max(pos.stop, new_stop)

                # EXIT: trailing stop
                if sd["lows"][idx] <= pos.stop:
                    exit_price = pos.stop  # assume stop was hit at stop price
                    ret = (exit_price - pos.entry_price) / pos.entry_price
                    pnl = ret * pos.size_frac * equity
                    fee = pos.size_frac * equity * self.fee_rate
                    pnl -= fee
                    equity += pnl
                    trade_log.append({
                        "symbol": sym, "side": "SELL", "price": round(exit_price, 2),
                        "time": ts_str, "pnl": round(pnl, 2),
                        "reason": "trailing_stop",
                    })
                    del positions[sym]
                    continue

                # EXIT: trend reversal -- close below 50 SMA and EMA below SMA
                if idx >= warmup and close < sma50 and ema20 < sma50:
                    ret = (close - pos.entry_price) / pos.entry_price
                    pnl = ret * pos.size_frac * equity
                    fee = pos.size_frac * equity * self.fee_rate
                    pnl -= fee
                    equity += pnl
                    trade_log.append({
                        "symbol": sym, "side": "SELL", "price": close,
                        "time": ts_str, "pnl": round(pnl, 2),
                        "reason": "trend_reversal",
                    })
                    del positions[sym]
                    continue

            if not in_trade_period:
                continue

            # Entry logic -- rank symbols by momentum (return over last 20 days)
            candidates = []
            for sym, sd in data.items():
                if ts not in ts_idx[sym]:
                    continue
                idx = ts_idx[sym][ts]
                if idx < warmup:
                    continue
                if sym in positions:
                    continue

                close = sd["closes"][idx]
                ema20 = sd["ema20"][idx]
                sma50 = sd["sma50"][idx]
                atr_val = sd["atr14"][idx]
                rsi_val = sd["rsi14"][idx]
                hi20 = sd["hi20"][idx]
                prev_ema20 = sd["ema20"][idx - 1]
                prev_sma50 = sd["sma50"][idx - 1]

                if atr_val <= 0 or sma50 <= 0:
                    continue

                in_uptrend = close > sma50 and ema20 > sma50
                # Momentum score for ranking
                mom_20 = (close - sd["closes"][idx - 20]) / sd["closes"][idx - 20] if idx >= 20 else 0

                signal = None

                # Signal 1: Trend start -- EMA crosses above SMA
                if prev_ema20 <= prev_sma50 and ema20 > sma50:
                    signal = "trend_start"

                # Signal 2: Pullback to 20 EMA in uptrend (wider zone)
                elif in_uptrend and close <= ema20 * 1.01 and close >= ema20 * 0.97 and rsi_val < 55:
                    signal = f"pullback (RSI={rsi_val:.0f})"

                # Signal 3: Breakout new high
                elif in_uptrend and close >= hi20 and rsi_val > 50:
                    signal = f"breakout (RSI={rsi_val:.0f})"

                # Signal 4: Strong momentum continuation
                elif in_uptrend and rsi_val > 55 and mom_20 > 0.05:
                    signal = f"momentum (RSI={rsi_val:.0f}, mom={mom_20:.1%})"

                if signal:
                    candidates.append((sym, signal, mom_20, close, atr_val))

            # Sort by momentum (strongest first) and take up to max_positions
            candidates.sort(key=lambda x: x[2], reverse=True)
            for sym, signal, _, close, atr_val in candidates:
                if len(positions) >= self.max_positions:
                    break
                ts_str = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
                stop = close - self.trail_atr_mult * atr_val
                size = self.position_size
                fee = size * equity * self.fee_rate
                equity -= fee
                positions[sym] = Position(sym, close, size, stop)
                trade_log.append({
                    "symbol": sym, "side": "BUY", "price": close,
                    "time": ts_str, "pnl": 0,
                    "reason": signal,
                })

            # Mark-to-market equity
            mtm = equity
            for sym, pos in positions.items():
                if sym in data and ts in ts_idx[sym]:
                    idx = ts_idx[sym][ts]
                    close = data[sym]["closes"][idx]
                    ret = (close - pos.entry_price) / pos.entry_price
                    mtm += ret * pos.size_frac * equity

            equity_curve.append(mtm)
            if mtm > peak_equity:
                peak_equity = mtm
            dd = (peak_equity - mtm) / peak_equity * 100
            if dd > max_dd:
                max_dd = dd

        # Close remaining positions
        for sym, pos in list(positions.items()):
            sd = data[sym]
            close = sd["closes"][-1]
            ts_str = datetime.fromtimestamp(sd["timestamps"][-1] / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
            ret = (close - pos.entry_price) / pos.entry_price
            pnl = ret * pos.size_frac * equity
            fee = pos.size_frac * equity * self.fee_rate
            pnl -= fee
            equity += pnl
            trade_log.append({
                "symbol": sym, "side": "SELL", "price": close,
                "time": ts_str, "pnl": round(pnl, 2),
                "reason": "end_of_period",
            })

        # Metrics
        total_return = (equity - initial_capital) / initial_capital * 100
        closed_trades = [t for t in trade_log if t["pnl"] != 0]
        num_trades = len(closed_trades)
        winners = len([t for t in closed_trades if t["pnl"] > 0])
        win_rate = winners / num_trades * 100 if num_trades > 0 else 0.0

        if len(equity_curve) > 1:
            rets = [(equity_curve[i] - equity_curve[i - 1]) / equity_curve[i - 1]
                    for i in range(1, len(equity_curve)) if equity_curve[i - 1] > 0]
            if rets:
                mean_r = sum(rets) / len(rets)
                var_r = sum((r - mean_r) ** 2 for r in rets) / max(len(rets) - 1, 1)
                std_r = math.sqrt(var_r) if var_r > 0 else 1e-10
                sharpe = (mean_r / std_r) * math.sqrt(365)
            else:
                sharpe = 0.0
        else:
            sharpe = 0.0

        return {
            "final_equity": round(equity, 2),
            "total_return_pct": round(total_return, 2),
            "sharpe_ratio": round(sharpe, 2),
            "max_drawdown_pct": round(max_dd, 2),
            "num_trades": num_trades,
            "win_rate": round(win_rate, 2),
            "trade_log": trade_log,
        }

    @staticmethod
    def _empty_result(cap: float) -> dict:
        return {"final_equity": cap, "total_return_pct": 0.0, "sharpe_ratio": 0.0,
                "max_drawdown_pct": 0.0, "num_trades": 0, "win_rate": 0.0, "trade_log": []}


def run_backtest(start: str, end: str, initial_capital: float = 1000.0) -> dict[str, Any]:
    """Main entry point for the competition harness."""
    strategy = MacroTrendFollower(
        symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT"],
        ema_fast=20,
        sma_slow=50,
        atr_period=14,
        rsi_period=14,
        trail_atr_mult=3.5,
        breakout_period=20,
        max_positions=3,
        position_size=0.30,
        fee_rate=0.001,
        lookback_days=90,
    )
    return strategy.run(start, end, initial_capital)


if __name__ == "__main__":
    print("=" * 60)
    print("TRAIN PERIOD: 2024-04-01 to 2024-09-30")
    print("=" * 60)
    train = run_backtest("2024-04-01", "2024-09-30")
    for k, v in train.items():
        if k != "trade_log":
            print(f"  {k}: {v}")
    print(f"\n  --- Train Trade Log ---")
    for t in train["trade_log"]:
        print(f"  {t['time']} | {t['symbol']:8s} | {t['side']:5s} | ${t['price']:>10.2f} | PnL: ${t['pnl']:>8.2f} | {t['reason']}")

    print()
    print("=" * 60)
    print("TEST PERIOD: 2024-10-01 to 2024-12-31")
    print("=" * 60)
    test = run_backtest("2024-10-01", "2024-12-31")
    for k, v in test.items():
        if k != "trade_log":
            print(f"  {k}: {v}")
    print(f"\n  --- Test Trade Log ---")
    for t in test["trade_log"]:
        print(f"  {t['time']} | {t['symbol']:8s} | {t['side']:5s} | ${t['price']:>10.2f} | PnL: ${t['pnl']:>8.2f} | {t['reason']}")
