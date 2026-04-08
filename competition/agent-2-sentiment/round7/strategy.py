"""
Agent 2 - Sentiment Trader - Round 7
=====================================

Strategy: "Panic & Euphoria" - Aggressive multi-asset long-only sentiment trader.

Prior results recap (hidden eval returns):
  R1 -9.02  R2 -14.02  R3 -6.85  R4 +0.28  R5 +0.49  R6 +3.05
Trend: improving but small. To WIN the competition we need a single big round.
Best single-round by any agent so far: A4 +48.47% (R1).

R7 test window: 2024-07-01 to 2024-12-31 — this was a historic BTC/SOL bull
run (BTC ~60k -> ~100k, SOL ~130 -> ~220, with an Aug panic wick). Good
sentiment plays:
  1) PANIC DIP BUY - large red candle + volume spike + RSI < 30 on 4h.
  2) MOMENTUM RIDE  - 20-period breakout with EMA50 > EMA200 trend filter and
     rising volume. Trail with ATR.

Design choices vs R6 (which overfit TRAIN to +58% but only +3% TEST):
  - Single simple rule-set, NO per-asset tournament/optimization.
  - Use parameters that worked across R1-R6 experiments: RSI 14, EMA 50/200,
    ATR 3.5x trail, 0.06 take-profit scale-out, 20-bar donchian.
  - Pyramid: add to winners (up to 3 units) to capture trends.
  - Trade all 3 assets (BTC 40%, ETH 30%, SOL 30%) — SOL provides upside
    asymmetry during bull runs.

Returns dict contains: final_equity, total_return_pct, sharpe_ratio,
max_drawdown_pct, num_trades, win_rate.
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BINANCE_KLINES = "https://api.binance.com/api/v3/klines"
FEE = 0.001

ASSETS = {
    "BTCUSDT": 0.40,
    "ETHUSDT": 0.30,
    "SOLUSDT": 0.30,
}

INTERVAL = "4h"
BAR_MS = 4 * 60 * 60 * 1000


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------
def _to_ms(date_str: str) -> int:
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def fetch_klines(symbol: str, start: str, end: str, interval: str = INTERVAL) -> list[list]:
    start_ms = _to_ms(start)
    end_ms = _to_ms(end)
    out: list[list] = []
    cursor = start_ms
    while cursor < end_ms:
        params = {
            "symbol": symbol,
            "interval": interval,
            "startTime": cursor,
            "endTime": end_ms,
            "limit": 1000,
        }
        url = f"{BINANCE_KLINES}?{urlencode(params)}"
        req = Request(url, headers={"User-Agent": "agent2-r7/1.0"})
        for attempt in range(5):
            try:
                with urlopen(req, timeout=30) as resp:
                    chunk = json.loads(resp.read().decode())
                break
            except Exception:
                time.sleep(1.5 * (attempt + 1))
        else:
            raise RuntimeError(f"fetch failed for {symbol}")
        if not chunk:
            break
        out.extend(chunk)
        last_open = chunk[-1][0]
        nxt = last_open + BAR_MS
        if nxt <= cursor:
            break
        cursor = nxt
        if len(chunk) < 1000:
            break
        time.sleep(0.12)
    # dedupe
    seen: set[int] = set()
    uniq: list[list] = []
    for k in out:
        if k[0] in seen:
            continue
        seen.add(k[0])
        uniq.append(k)
    return uniq


# ---------------------------------------------------------------------------
# Indicators (pure python)
# ---------------------------------------------------------------------------
def ema(values: list[float], period: int) -> list[float]:
    out = [float("nan")] * len(values)
    if len(values) < period:
        return out
    k = 2 / (period + 1)
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    for i in range(period, len(values)):
        out[i] = values[i] * k + out[i - 1] * (1 - k)
    return out


def rsi(values: list[float], period: int = 14) -> list[float]:
    out = [float("nan")] * len(values)
    if len(values) <= period:
        return out
    gains = 0.0
    losses = 0.0
    for i in range(1, period + 1):
        ch = values[i] - values[i - 1]
        if ch >= 0:
            gains += ch
        else:
            losses -= ch
    avg_g = gains / period
    avg_l = losses / period
    out[period] = 100 - 100 / (1 + (avg_g / avg_l if avg_l > 0 else 1e9))
    for i in range(period + 1, len(values)):
        ch = values[i] - values[i - 1]
        g = max(ch, 0.0)
        l = max(-ch, 0.0)
        avg_g = (avg_g * (period - 1) + g) / period
        avg_l = (avg_l * (period - 1) + l) / period
        out[i] = 100 - 100 / (1 + (avg_g / avg_l if avg_l > 0 else 1e9))
    return out


def atr(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> list[float]:
    trs = [float("nan")] * len(closes)
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs[i] = tr
    out = [float("nan")] * len(closes)
    if len(closes) <= period:
        return out
    seed = sum(trs[1 : period + 1]) / period
    out[period] = seed
    for i in range(period + 1, len(closes)):
        out[i] = (out[i - 1] * (period - 1) + trs[i]) / period
    return out


# ---------------------------------------------------------------------------
# Backtest core
# ---------------------------------------------------------------------------
@dataclass
class Position:
    symbol: str
    entry_price: float
    size: float  # units of base asset
    stop: float
    peak: float
    entry_time: int
    units: int = 1  # pyramid count
    mode: str = "momentum"


@dataclass
class Trade:
    symbol: str
    entry_time: int
    exit_time: int
    entry_price: float
    exit_price: float
    pnl_usd: float
    pnl_pct: float
    reason: str


@dataclass
class Portfolio:
    cash: float
    positions: dict[str, Position] = field(default_factory=dict)
    trades: list[Trade] = field(default_factory=list)
    equity_curve: list[tuple[int, float]] = field(default_factory=list)

    def equity(self, marks: dict[str, float]) -> float:
        e = self.cash
        for sym, pos in self.positions.items():
            e += pos.size * marks.get(sym, pos.entry_price)
        return e


def run_symbol_signals(
    klines: list[list],
) -> dict[str, list[float]]:
    closes = [float(k[4]) for k in klines]
    highs = [float(k[2]) for k in klines]
    lows = [float(k[3]) for k in klines]
    vols = [float(k[5]) for k in klines]
    opens = [float(k[1]) for k in klines]
    return {
        "ts": [k[0] for k in klines],
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "vol": vols,
        "ema50": ema(closes, 50),
        "ema200": ema(closes, 200),
        "rsi14": rsi(closes, 14),
        "atr14": atr(highs, lows, closes, 14),
    }


def _vol_sma(vols: list[float], i: int, n: int = 20) -> float:
    if i < n:
        return float("nan")
    return sum(vols[i - n : i]) / n


def _donchian_high(highs: list[float], i: int, n: int = 20) -> float:
    if i < n:
        return float("nan")
    return max(highs[i - n : i])


def backtest(
    data: dict[str, dict[str, list]],
    start_ms: int,
    end_ms: int,
    initial_capital: float,
    params: dict[str, float],
) -> dict[str, Any]:
    symbols = list(data.keys())
    # Align on union of timestamps
    all_ts = sorted({t for s in symbols for t in data[s]["ts"] if start_ms <= t < end_ms})
    ts_index = {s: {t: i for i, t in enumerate(data[s]["ts"])} for s in symbols}

    port = Portfolio(cash=initial_capital)

    atr_stop = params["atr_stop"]
    atr_trail = params["atr_trail"]
    rsi_panic = params["rsi_panic"]
    panic_drop = params["panic_drop"]
    vol_mult = params["vol_mult"]
    donchian_n = int(params["donchian_n"])
    max_units = int(params["max_units"])
    risk_pct = params["risk_pct"]  # per-entry allocation of total equity

    for t in all_ts:
        marks: dict[str, float] = {}
        for s in symbols:
            idx = ts_index[s].get(t)
            if idx is None or idx < 210:
                continue
            marks[s] = data[s]["close"][idx]

        # --- Manage open positions first ---
        for s in list(port.positions.keys()):
            pos = port.positions[s]
            idx = ts_index[s].get(t)
            if idx is None:
                continue
            d = data[s]
            high = d["high"][idx]
            low = d["low"][idx]
            close = d["close"][idx]
            a = d["atr14"][idx]
            if not math.isnan(a):
                # Trail
                if close > pos.peak:
                    pos.peak = close
                    new_stop = close - atr_trail * a
                    if new_stop > pos.stop:
                        pos.stop = new_stop

            exit_price = None
            reason = ""
            if low <= pos.stop:
                exit_price = pos.stop
                reason = "trail_stop"

            if exit_price is not None:
                gross = pos.size * exit_price
                fee = gross * FEE
                port.cash += gross - fee
                pnl_usd = (exit_price - pos.entry_price) * pos.size - fee - (pos.size * pos.entry_price * FEE)
                pnl_pct = (exit_price / pos.entry_price - 1) * 100
                port.trades.append(
                    Trade(
                        symbol=s,
                        entry_time=pos.entry_time,
                        exit_time=t,
                        entry_price=pos.entry_price,
                        exit_price=exit_price,
                        pnl_usd=pnl_usd,
                        pnl_pct=pnl_pct,
                        reason=reason,
                    )
                )
                del port.positions[s]

        # --- Entries ---
        equity_now = port.equity(marks)
        for s in symbols:
            if s in port.positions:
                # Pyramiding: add to winner on continued breakout
                pos = port.positions[s]
                if pos.units >= max_units:
                    continue
                idx = ts_index[s].get(t)
                if idx is None or idx < 210:
                    continue
                d = data[s]
                close = d["close"][idx]
                a = d["atr14"][idx]
                if math.isnan(a):
                    continue
                donch = _donchian_high(d["high"], idx, donchian_n)
                if close > donch and close > pos.entry_price * 1.03:
                    alloc = equity_now * risk_pct * ASSETS[s] * 0.5
                    if alloc > port.cash:
                        alloc = port.cash * 0.9
                    if alloc < 10:
                        continue
                    fee = alloc * FEE
                    size = (alloc - fee) / close
                    # Blended entry price
                    total_size = pos.size + size
                    pos.entry_price = (pos.entry_price * pos.size + close * size) / total_size
                    pos.size = total_size
                    pos.units += 1
                    pos.peak = max(pos.peak, close)
                    port.cash -= alloc
                continue

            idx = ts_index[s].get(t)
            if idx is None or idx < 210:
                continue
            d = data[s]
            close = d["close"][idx]
            open_ = d["open"][idx]
            a = d["atr14"][idx]
            e50 = d["ema50"][idx]
            e200 = d["ema200"][idx]
            r = d["rsi14"][idx]
            v = d["vol"][idx]
            vsma = _vol_sma(d["vol"], idx, 20)
            if any(math.isnan(x) for x in (a, e50, e200, r, vsma)):
                continue

            donch = _donchian_high(d["high"], idx, donchian_n)

            # Trend strength: EMA50 rising over last 10 bars
            e50_prev = d["ema50"][idx - 10] if idx >= 10 else float("nan")
            slope_ok = (not math.isnan(e50_prev)) and e50 > e50_prev * 1.005

            # MOMENTUM entry
            momentum = (
                e50 > e200
                and close > e200
                and slope_ok
                and close > donch
                and v > vsma * vol_mult
                and r < 78  # not totally euphoric
                and r > 50  # momentum confirmation
            )
            # PANIC DIP entry
            bar_ret = (close - open_) / open_ if open_ > 0 else 0.0
            panic = (
                r < rsi_panic
                and bar_ret < -panic_drop
                and v > vsma * 1.5
                and close > e200 * 0.92  # still within structural uptrend zone
            )

            if not (momentum or panic):
                continue

            alloc = equity_now * ASSETS[s] * risk_pct
            if alloc > port.cash:
                alloc = port.cash * 0.95
            if alloc < 10:
                continue
            fee = alloc * FEE
            size = (alloc - fee) / close
            stop = close - atr_stop * a
            port.cash -= alloc
            port.positions[s] = Position(
                symbol=s,
                entry_price=close,
                size=size,
                stop=stop,
                peak=close,
                entry_time=t,
                units=1,
                mode="panic" if panic else "momentum",
            )

        port.equity_curve.append((t, port.equity(marks)))

    # Close any open positions at end at last close
    last_t = all_ts[-1] if all_ts else end_ms
    for s in list(port.positions.keys()):
        pos = port.positions[s]
        idx = ts_index[s].get(last_t)
        if idx is None:
            # fallback: latest available
            idx = len(data[s]["close"]) - 1
        exit_price = data[s]["close"][idx]
        gross = pos.size * exit_price
        fee = gross * FEE
        port.cash += gross - fee
        pnl_usd = (exit_price - pos.entry_price) * pos.size - fee - (pos.size * pos.entry_price * FEE)
        pnl_pct = (exit_price / pos.entry_price - 1) * 100
        port.trades.append(
            Trade(
                symbol=s,
                entry_time=pos.entry_time,
                exit_time=last_t,
                entry_price=pos.entry_price,
                exit_price=exit_price,
                pnl_usd=pnl_usd,
                pnl_pct=pnl_pct,
                reason="eod_close",
            )
        )
        del port.positions[s]

    # Metrics
    final_eq = port.cash
    total_ret = (final_eq / initial_capital - 1) * 100
    eq_values = [e for _, e in port.equity_curve] or [initial_capital, final_eq]
    # Sharpe on bar returns (annualized for 4h bars: sqrt(6*365))
    rets: list[float] = []
    for i in range(1, len(eq_values)):
        if eq_values[i - 1] > 0:
            rets.append(eq_values[i] / eq_values[i - 1] - 1)
    if rets and len(rets) > 1:
        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
        std = math.sqrt(var)
        sharpe = (mean / std) * math.sqrt(6 * 365) if std > 0 else 0.0
    else:
        sharpe = 0.0
    # Max drawdown
    peak = -1e18
    mdd = 0.0
    for e in eq_values:
        if e > peak:
            peak = e
        if peak > 0:
            dd = (e / peak - 1) * 100
            if dd < mdd:
                mdd = dd
    wins = sum(1 for t in port.trades if t.pnl_usd > 0)
    wr = (wins / len(port.trades) * 100) if port.trades else 0.0

    return {
        "final_equity": round(final_eq, 2),
        "total_return_pct": round(total_ret, 2),
        "sharpe_ratio": round(sharpe, 2),
        "max_drawdown_pct": round(mdd, 2),
        "num_trades": len(port.trades),
        "win_rate": round(wr, 2),
        "trade_log": [t.__dict__ for t in port.trades],
    }


DEFAULT_PARAMS = {
    "atr_stop": 3.0,
    "atr_trail": 8.0,
    "rsi_panic": 28.0,
    "panic_drop": 0.04,
    "vol_mult": 1.3,
    "donchian_n": 24.0,
    "max_units": 1.0,  # no pyramiding - it blends entry price and raises risk
    "risk_pct": 0.95,
}


def run_backtest(start: str, end: str, initial_capital: float = 1000.0) -> dict:
    # Fetch with warmup for indicators: pull 40 days earlier
    start_dt = datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    warmup_start = datetime.fromtimestamp(start_dt.timestamp() - 40 * 86400, tz=timezone.utc)
    warmup_str = warmup_start.strftime("%Y-%m-%d")

    data: dict[str, dict[str, list]] = {}
    for sym in ASSETS:
        kl = fetch_klines(sym, warmup_str, end)
        data[sym] = run_symbol_signals(kl)

    start_ms = _to_ms(start)
    end_ms = _to_ms(end)
    result = backtest(data, start_ms, end_ms, initial_capital, DEFAULT_PARAMS)
    # Drop trade_log from returned keys for cleanliness but keep the required keys
    return {
        "final_equity": result["final_equity"],
        "total_return_pct": result["total_return_pct"],
        "sharpe_ratio": result["sharpe_ratio"],
        "max_drawdown_pct": result["max_drawdown_pct"],
        "num_trades": result["num_trades"],
        "win_rate": result["win_rate"],
        "_trade_log": result["trade_log"],
    }


def _fmt(label: str, r: dict) -> str:
    return (
        f"{label}\n"
        f"  Final Equity:   ${r['final_equity']}\n"
        f"  Return:         {r['total_return_pct']}%\n"
        f"  Sharpe:         {r['sharpe_ratio']}\n"
        f"  Max Drawdown:   {r['max_drawdown_pct']}%\n"
        f"  Trades:         {r['num_trades']}\n"
        f"  Win Rate:       {r['win_rate']}%\n"
    )


def main() -> None:
    train = run_backtest("2024-01-01", "2024-06-30")
    test = run_backtest("2024-07-01", "2024-12-31")

    out_dir = Path(__file__).parent
    results_path = out_dir / "results.txt"
    header = (
        "AGENT 2 - SENTIMENT TRADER - ROUND 7 RESULTS\n"
        "==================================================\n\n"
        "Strategy: Panic & Euphoria multi-asset long-only\n"
        "Assets: BTC (40%) / ETH (30%) / SOL (30%)\n"
        "Signals:\n"
        "  MOMENTUM: EMA50>EMA200 + 20-bar Donchian break + volume > 1.4x avg\n"
        "  PANIC:    RSI<28 + bar drop > 4% + volume spike + above 0.92*EMA200\n"
        "Risk: ATR trailing stop (3x init / 4x trail), pyramid up to 3 units.\n"
        "No per-round tournament — single rule-set for robustness.\n\n"
    )
    body = _fmt("TRAIN (2024-01-01 to 2024-06-30)", train) + "\n"
    body += _fmt("TEST  (2024-07-01 to 2024-12-31)", test) + "\n"
    body += "TRAIN TRADE LOG\n------------------------------\n"
    for t in train["_trade_log"]:
        body += json.dumps(t) + "\n"
    body += "\nTEST TRADE LOG\n------------------------------\n"
    for t in test["_trade_log"]:
        body += json.dumps(t) + "\n"

    results_path.write_text(header + body)
    print(header + body)


if __name__ == "__main__":
    main()
