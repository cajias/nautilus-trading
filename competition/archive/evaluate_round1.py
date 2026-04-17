"""
Round 1 Competition Evaluator
=============================
Runs all 5 agent strategies on the HIDDEN evaluation period:
  Oct 1 - Dec 31, 2024

Starting capital: $1,000 per agent
The agents' strategy logic and parameters are NOT modified.
Only date ranges are changed to target the eval period.
"""

import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

EVAL_START = "2024-10-01"
EVAL_END = "2024-12-31"
STARTING_CAPITAL = 1000.0

results_summary: dict[str, dict] = {}


# ============================================================================
# Shared: Binance data download
# ============================================================================

def download_klines(symbol: str, interval: str, start: str, end: str) -> pd.DataFrame:
    """Download klines from Binance public API."""
    import requests

    url = "https://api.binance.com/api/v3/klines"
    all_data = []
    start_ms = int(pd.Timestamp(start).timestamp() * 1000)
    end_ms = int(pd.Timestamp(end).timestamp() * 1000)
    current = start_ms

    while current < end_ms:
        params = {
            "symbol": symbol,
            "interval": interval,
            "startTime": current,
            "endTime": end_ms,
            "limit": 1000,
        }
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if not data:
            break
        all_data.extend(data)
        current = data[-1][6] + 1
        time.sleep(0.1)

    df = pd.DataFrame(all_data, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades", "taker_buy_base",
        "taker_buy_quote", "ignore",
    ])
    for col in ["open", "high", "low", "close", "volume", "quote_volume",
                "taker_buy_base", "taker_buy_quote"]:
        if col in df.columns:
            df[col] = df[col].astype(float)
    df["timestamp"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df = df.set_index("timestamp")
    df = df[~df.index.duplicated(keep="first")]
    return df.sort_index()


# ============================================================================
# Agent 1: Quantitative Trader
# Multi-Factor Momentum + Mean Reversion on BTC/ETH/SOL (1d bars)
# ============================================================================

def evaluate_agent_1() -> dict:
    print("\n" + "=" * 70)
    print("  AGENT 1: Quantitative Trader")
    print("  Multi-Factor Momentum + Mean Reversion | Daily | BTC/ETH/SOL")
    print("=" * 70)

    SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    MOMENTUM_WINDOW = 7
    MEAN_REV_WINDOW = 20
    VOL_WINDOW = 14
    SIGNAL_THRESHOLD = 0.2
    STOP_LOSS_PCT = 0.03
    TAKE_PROFIT_PCT = 0.06
    MAX_POSITION_WEIGHT = 0.40
    TRANSACTION_COST_BPS = 10
    TREND_FILTER_WINDOW = 30
    MAX_EXPOSURE = 0.70

    buffer_start = (
        pd.Timestamp(EVAL_START) - pd.Timedelta(days=MEAN_REV_WINDOW + TREND_FILTER_WINDOW + 70)
    ).strftime("%Y-%m-%d")

    data = {}
    for sym in SYMBOLS:
        print(f"  Downloading {sym} 1d...")
        df = download_klines(sym, "1d", buffer_start, EVAL_END)
        df2 = df[["open", "high", "low", "close", "volume"]].copy()
        df2.index = df2.index.tz_localize(None)
        data[sym] = df2
        print(f"    {len(df2)} bars")

    def compute_signals(prices):
        close = prices["close"]
        roc = close.pct_change(MOMENTUM_WINDOW)
        mom_signal = roc / roc.rolling(MEAN_REV_WINDOW).std()
        bb_mid = close.rolling(MEAN_REV_WINDOW).mean()
        bb_std = close.rolling(MEAN_REV_WINDOW).std()
        z_score = (close - bb_mid) / bb_std
        mr_signal = -z_score
        ret = close.pct_change()
        realized_vol = ret.rolling(VOL_WINDOW).std() * np.sqrt(365)
        vol_pctile = realized_vol.rolling(60, min_periods=20).rank(pct=True)
        sma_trend = close.rolling(TREND_FILTER_WINDOW).mean()
        trend_score = (close / sma_trend - 1).clip(-0.1, 0.1) / 0.1
        trend_filter = (trend_score + 1) / 2
        mom_weight = vol_pctile.clip(0.3, 0.7)
        mr_weight = 1 - mom_weight
        composite = mom_weight * mom_signal + mr_weight * mr_signal
        composite = composite * trend_filter
        return pd.DataFrame({
            "close": close, "return": ret, "composite": composite,
        })

    all_signals = {sym: compute_signals(data[sym]) for sym in SYMBOLS}

    start_dt = pd.Timestamp(EVAL_START)
    end_dt = pd.Timestamp(EVAL_END)

    period_signals = {}
    for sym, sig in all_signals.items():
        mask = (sig.index >= start_dt) & (sig.index <= end_dt)
        period_signals[sym] = sig[mask].copy()

    common_dates = period_signals[SYMBOLS[0]].index
    for sym in SYMBOLS[1:]:
        common_dates = common_dates.intersection(period_signals[sym].index)
    common_dates = common_dates.sort_values()

    cash = STARTING_CAPITAL
    positions = {sym: 0.0 for sym in SYMBOLS}
    entry_prices = {sym: 0.0 for sym in SYMBOLS}
    portfolio_values = []
    trade_returns = []
    total_trades = 0

    for date in common_dates:
        port_value = cash
        for sym in SYMBOLS:
            price = period_signals[sym].loc[date, "close"]
            port_value += positions[sym] * price

        for sym in SYMBOLS:
            if positions[sym] > 0:
                price = period_signals[sym].loc[date, "close"]
                pnl_pct = (price - entry_prices[sym]) / entry_prices[sym]
                if pnl_pct < -STOP_LOSS_PCT or pnl_pct > TAKE_PROFIT_PCT:
                    proceeds = positions[sym] * price
                    cost = proceeds * (TRANSACTION_COST_BPS / 10000)
                    cash += proceeds - cost
                    trade_returns.append(pnl_pct)
                    positions[sym] = 0.0
                    entry_prices[sym] = 0.0
                    total_trades += 1

        port_value = cash
        for sym in SYMBOLS:
            price = period_signals[sym].loc[date, "close"]
            port_value += positions[sym] * price

        signals_today = {}
        for sym in SYMBOLS:
            sig_val = period_signals[sym].loc[date, "composite"]
            signals_today[sym] = 0.0 if pd.isna(sig_val) else sig_val

        positive_signals = {s: max(0, v) for s, v in signals_today.items()}
        total_pos_signal = sum(positive_signals.values())

        target_weights = {}
        if total_pos_signal > 0:
            for sym in SYMBOLS:
                w = positive_signals[sym] / total_pos_signal
                if signals_today[sym] < SIGNAL_THRESHOLD:
                    w = 0.0
                w = min(w, MAX_POSITION_WEIGHT)
                target_weights[sym] = w
        else:
            target_weights = {sym: 0.0 for sym in SYMBOLS}

        total_w = sum(target_weights.values())
        if total_w > MAX_EXPOSURE:
            scale = MAX_EXPOSURE / total_w
            target_weights = {s: v * scale for s, v in target_weights.items()}

        for sym in SYMBOLS:
            price = period_signals[sym].loc[date, "close"]
            target_value = port_value * target_weights[sym]
            current_value = positions[sym] * price
            delta_value = target_value - current_value

            if abs(delta_value) > port_value * 0.01:
                if delta_value > 0:
                    cost = delta_value * (1 + TRANSACTION_COST_BPS / 10000)
                    if cost <= cash:
                        units = delta_value / price
                        if positions[sym] == 0:
                            entry_prices[sym] = price
                        else:
                            old_val = positions[sym] * entry_prices[sym]
                            entry_prices[sym] = (old_val + delta_value) / (positions[sym] + units)
                        positions[sym] += units
                        cash -= cost
                        total_trades += 1
                elif delta_value < 0:
                    units_to_sell = min(abs(delta_value) / price, positions[sym])
                    proceeds = units_to_sell * price
                    cost = proceeds * (TRANSACTION_COST_BPS / 10000)
                    if positions[sym] > 0:
                        ret_pct = (price - entry_prices[sym]) / entry_prices[sym]
                        trade_returns.append(ret_pct)
                    positions[sym] -= units_to_sell
                    cash += proceeds - cost
                    total_trades += 1
                    if positions[sym] < 1e-8:
                        positions[sym] = 0.0
                        entry_prices[sym] = 0.0

        final_value = cash
        for sym in SYMBOLS:
            price = period_signals[sym].loc[date, "close"]
            final_value += positions[sym] * price
        portfolio_values.append(final_value)

    pv = pd.Series(portfolio_values, index=common_dates)
    total_return = (pv.iloc[-1] / STARTING_CAPITAL - 1) * 100

    result = {
        "final_equity": pv.iloc[-1],
        "total_return_pct": total_return,
        "trades": total_trades,
    }
    print(f"  Final Equity: ${result['final_equity']:,.2f}")
    print(f"  Return: {result['total_return_pct']:+.2f}%")
    print(f"  Trades: {result['trades']}")
    return result


# ============================================================================
# Agent 2: Sentiment Trader
# Fear/Greed Regime Trading | 1h bars | BTC + ETH
# ============================================================================

def evaluate_agent_2() -> dict:
    print("\n" + "=" * 70)
    print("  AGENT 2: Sentiment Trader")
    print("  Fear/Greed Regime Trading | 1h | BTC + ETH")
    print("=" * 70)

    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "agent2_strategy",
        Path(__file__).parent / "agent-2-sentiment" / "round1" / "strategy.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    build_features = mod.build_features
    dl2 = mod.download_binance_klines
    optimize_params = mod.optimize_params
    rb2 = mod.run_backtest

    symbols = ["BTCUSDT", "ETHUSDT"]
    train_start, train_end = "2024-01-01", "2024-06-30"

    total_final = 0
    total_trades = 0
    per_symbol_capital = 500.0

    for symbol in symbols:
        print(f"\n  Processing {symbol}...")
        print(f"    Downloading TRAIN data...")
        train_df = dl2(symbol, "1h", train_start, train_end)
        print(f"    {len(train_df)} train candles")

        print(f"    Downloading EVAL data ({EVAL_START} to {EVAL_END})...")
        eval_df = dl2(symbol, "1h", EVAL_START, EVAL_END)
        print(f"    {len(eval_df)} eval candles")

        train_feat = build_features(train_df)
        eval_feat = build_features(eval_df)

        print(f"    Optimizing params on TRAIN...")
        best_params = optimize_params(train_feat)

        eval_result = rb2(eval_feat, best_params, capital=per_symbol_capital)
        symbol_final = eval_result["final_equity"]
        total_final += symbol_final
        total_trades += eval_result["n_trades"]
        print(f"    {symbol} EVAL Return: {eval_result['total_return_pct']:.2f}%, "
              f"Final: ${symbol_final:.2f}, Trades: {eval_result['n_trades']}")

    portfolio_return = (total_final - STARTING_CAPITAL) / STARTING_CAPITAL * 100

    result = {
        "final_equity": total_final,
        "total_return_pct": portfolio_return,
        "trades": total_trades,
    }
    print(f"\n  Combined EVAL Return: {result['total_return_pct']:+.2f}%")
    print(f"  Final Equity: ${result['final_equity']:,.2f}")
    return result


# ============================================================================
# Agent 3: Macro Strategist
# RSI Mean-Reversion + Trend | Daily | BTC only
# ============================================================================

def evaluate_agent_3() -> dict:
    print("\n" + "=" * 70)
    print("  AGENT 3: Macro Strategist")
    print("  RSI Mean-Reversion + Trend | Daily | BTC")
    print("=" * 70)

    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "agent3_strategy",
        Path(__file__).parent / "agent-3-macro" / "round1" / "strategy.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    rb3 = mod.run_backtest

    results = rb3(
        symbol="BTCUSDT",
        start_data="2023-08-01",
        end_data="2025-01-02",
        train_start="2024-01-01",
        train_end="2024-06-30",
        test_start=EVAL_START,
        test_end=EVAL_END,
        initial_capital=STARTING_CAPITAL,
    )

    if not results or not results.get("test"):
        return {"final_equity": STARTING_CAPITAL, "total_return_pct": 0.0, "trades": 0}

    test_m = results["test"]
    result = {
        "final_equity": test_m["final_equity"],
        "total_return_pct": test_m["total_return_pct"],
        "trades": test_m["num_entries"],
    }
    print(f"\n  EVAL Return: {result['total_return_pct']:+.2f}%")
    print(f"  Final Equity: ${result['final_equity']:,.2f}")
    return result


# ============================================================================
# Agent 4: ML Engineer
# Multi-Strategy Tournament | Daily | BTC
# ============================================================================

def evaluate_agent_4() -> dict:
    print("\n" + "=" * 70)
    print("  AGENT 4: ML Engineer")
    print("  Multi-Strategy Tournament | Daily | BTC")
    print("=" * 70)

    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "agent4_strategy",
        Path(__file__).parent / "agent-4-ml" / "round1" / "strategy.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    fetch4 = mod.fetch_binance_klines
    add_features = mod.add_features
    walk_forward_predict = mod.walk_forward_predict
    strat_bb_reversion = mod.strat_bb_reversion
    strat_ema_trend = mod.strat_ema_trend
    strat_ml_signal = mod.strat_ml_signal
    strat_dip_buyer = mod.strat_dip_buyer
    ema = mod.ema

    SYMBOL = "BTCUSDT"
    TRAIN_START = "2024-01-01"
    TRAIN_END = "2024-06-30"

    buffer_start = "2023-06-01"
    print(f"  Downloading {SYMBOL} daily data...")
    df = fetch4(SYMBOL, "1d", buffer_start, EVAL_END)
    print(f"  {len(df)} candles")

    print("  Engineering features...")
    df, feature_cols = add_features(df)

    print("  Walk-forward ML predictions...")
    train_preds = walk_forward_predict(df, feature_cols, "2024-02-29", TRAIN_END)
    eval_preds = walk_forward_predict(df, feature_cols, TRAIN_END, EVAL_END)
    print(f"  Train: {len(train_preds)}, Eval: {len(eval_preds)} predictions")

    best_ml_ret = -999.0
    best_tl, best_te = 0.55, 0.45
    for tl in np.arange(0.50, 0.62, 0.02):
        for te in np.arange(0.38, 0.52, 0.02):
            if te >= tl:
                continue
            r = strat_ml_signal(df, train_preds, tl, te)
            if r["total_return_pct"] > best_ml_ret and r["num_trades"] >= 2:
                best_ml_ret = r["total_return_pct"]
                best_tl, best_te = tl, te

    print("  Running strategy tournament on EVAL period...")
    strategies = {}

    for fast, slow in [(5, 20), (10, 30), (10, 50), (20, 50)]:
        name = f"EMA({fast},{slow})"
        df[f"ema_{fast}"] = ema(df["close"], fast)
        df[f"ema_{slow}"] = ema(df["close"], slow)
        strategies[name] = strat_ema_trend(df, EVAL_START, EVAL_END, fast, slow)

    for rsi_buy, rsi_sell in [(30, 70), (35, 65), (40, 60)]:
        name = f"BB_MR(rsi {rsi_buy}/{rsi_sell})"
        strategies[name] = strat_bb_reversion(df, EVAL_START, EVAL_END, rsi_buy, rsi_sell)

    strategies["DipBuyer"] = strat_dip_buyer(df, EVAL_START, EVAL_END)
    strategies[f"ML(tl={best_tl:.2f},te={best_te:.2f})"] = strat_ml_signal(
        df, eval_preds, best_tl, best_te
    )

    print(f"\n  {'Strategy':<30} {'Return':>12} {'Trades':>8}")
    print(f"  {'-' * 52}")
    for name, s in sorted(strategies.items(), key=lambda x: x[1]["total_return_pct"], reverse=True):
        print(f"  {name:<30} {s['total_return_pct']:>+10.2f}% {s['num_trades']:>7}")

    profitable = {n: s for n, s in strategies.items() if s["total_return_pct"] > 0}
    if profitable:
        best_name = max(profitable, key=lambda n: profitable[n]["sharpe_ratio"])
    else:
        best_name = max(strategies, key=lambda n: strategies[n]["total_return_pct"])

    best = strategies[best_name]
    print(f"\n  SELECTED: {best_name}")

    result = {
        "final_equity": best["final_equity"],
        "total_return_pct": best["total_return_pct"],
        "trades": best["num_trades"],
        "selected_strategy": best_name,
    }
    print(f"  Final Equity: ${result['final_equity']:,.2f}")
    print(f"  Return: {result['total_return_pct']:+.2f}%")
    return result


# ============================================================================
# Agent 5: Hybrid Strategist
# Multi-Signal Ensemble | 4h bars | BTC/ETH/SOL
# ============================================================================

def evaluate_agent_5() -> dict:
    print("\n" + "=" * 70)
    print("  AGENT 5: Hybrid Strategist")
    print("  Multi-Signal Ensemble | 4h | BTC/ETH/SOL")
    print("=" * 70)

    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "agent5_round1",
        Path(__file__).parent / "agent-5-hybrid" / "strategies" / "round1.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    dl5 = mod.download_binance_klines
    rb5 = mod.run_backtest

    ASSETS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    DATA_START = "2024-07-01"  # 3 months warmup before Oct 2024
    DATA_END = "2025-01-02"

    asset_data = {}
    for symbol in ASSETS:
        df = dl5(symbol=symbol, interval="4h", start=DATA_START, end=DATA_END)
        print(f"  {symbol}: {len(df)} bars")
        asset_data[symbol] = df

    eval_results = rb5(
        asset_data,
        start_date=EVAL_START,
        end_date=EVAL_END,
        initial_capital=STARTING_CAPITAL,
    )

    result = {
        "final_equity": eval_results["final_equity"],
        "total_return_pct": eval_results["total_return_pct"],
        "trades": eval_results["n_trades"],
    }
    print(f"\n  EVAL Return: {result['total_return_pct']:+.2f}%")
    print(f"  Final Equity: ${result['final_equity']:,.2f}")
    print(f"  Trades: {result['trades']}")
    return result


# ============================================================================
# Main
# ============================================================================

def main():
    print("=" * 70)
    print("  ROUND 1 COMPETITION EVALUATOR")
    print(f"  Hidden Evaluation Period: {EVAL_START} to {EVAL_END}")
    print(f"  Starting Capital: ${STARTING_CAPITAL:,.0f}")
    print("=" * 70)

    agents = {
        "Agent 1 (Quant)": evaluate_agent_1,
        "Agent 2 (Sentiment)": evaluate_agent_2,
        "Agent 3 (Macro)": evaluate_agent_3,
        "Agent 4 (ML)": evaluate_agent_4,
        "Agent 5 (Hybrid)": evaluate_agent_5,
    }

    results = {}
    for name, func in agents.items():
        try:
            results[name] = func()
        except Exception as e:
            print(f"\n  ERROR running {name}: {e}")
            traceback.print_exc()
            results[name] = {
                "final_equity": STARTING_CAPITAL,
                "total_return_pct": 0.0,
                "trades": 0,
                "error": str(e),
            }

    # Leaderboard
    print("\n\n" + "=" * 70)
    print("  ROUND 1 FINAL LEADERBOARD")
    print(f"  Evaluation Period: {EVAL_START} to {EVAL_END}")
    print(f"  Starting Capital: ${STARTING_CAPITAL:,.0f}")
    print("=" * 70)

    sorted_agents = sorted(results.items(), key=lambda x: x[1]["total_return_pct"], reverse=True)

    print(f"\n  {'Rank':<6} {'Agent':<25} {'Return':>12} {'Final Equity':>15} {'Trades':>8}")
    print(f"  {'-' * 68}")

    for rank, (name, r) in enumerate(sorted_agents, 1):
        err = " (ERROR)" if "error" in r else ""
        print(f"  {rank:<6} {name:<25} {r['total_return_pct']:>+10.2f}% "
              f"${r['final_equity']:>12,.2f} {r['trades']:>7}{err}")

    winner_name, winner_result = sorted_agents[0]
    if winner_result["total_return_pct"] > 0:
        print(f"\n  ROUND 1 WINNER: {winner_name}")
        print(f"  Return: {winner_result['total_return_pct']:+.2f}%")
        print(f"  Final Equity: ${winner_result['final_equity']:,.2f}")
    else:
        print("\n  NO WINNER: No agent achieved a positive return.")

    print("=" * 70)

    # Save results
    report_path = Path(__file__).parent / "round1_results.txt"
    with open(report_path, "w") as f:
        f.write("=" * 70 + "\n")
        f.write("  ROUND 1 COMPETITION RESULTS\n")
        f.write(f"  Hidden Evaluation Period: {EVAL_START} to {EVAL_END}\n")
        f.write(f"  Starting Capital: ${STARTING_CAPITAL:,.0f}\n")
        f.write(f"  Evaluated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("=" * 70 + "\n\n")

        f.write("CONTEXT: Oct-Dec 2024 was a very bullish crypto period.\n")
        f.write("BTC went from ~$60k to ~$100k+. Agents never trained on this period.\n\n")

        f.write("FINAL LEADERBOARD\n")
        f.write("-" * 70 + "\n")
        f.write(f"{'Rank':<6} {'Agent':<25} {'Return':>12} {'Final Equity':>15} {'Trades':>8}\n")
        f.write("-" * 70 + "\n")

        for rank, (name, r) in enumerate(sorted_agents, 1):
            err = " (ERROR)" if "error" in r else ""
            f.write(f"{rank:<6} {name:<25} {r['total_return_pct']:>+10.2f}% "
                    f"${r['final_equity']:>12,.2f} {r['trades']:>7}{err}\n")

        f.write("-" * 70 + "\n\n")

        if winner_result["total_return_pct"] > 0:
            f.write(f"ROUND 1 WINNER: {winner_name}\n")
            f.write(f"Return: {winner_result['total_return_pct']:+.2f}%\n")
            f.write(f"Final Equity: ${winner_result['final_equity']:,.2f}\n")
        else:
            f.write("NO WINNER: No agent achieved a positive return.\n")

        f.write("\n\nDETAILED RESULTS\n")
        f.write("=" * 70 + "\n\n")
        for name, r in sorted_agents:
            f.write(f"{name}\n")
            f.write(f"  Return: {r['total_return_pct']:+.2f}%\n")
            f.write(f"  Final Equity: ${r['final_equity']:,.2f}\n")
            f.write(f"  Trades: {r['trades']}\n")
            if "selected_strategy" in r:
                f.write(f"  Selected Sub-Strategy: {r['selected_strategy']}\n")
            if "error" in r:
                f.write(f"  ERROR: {r['error']}\n")
            f.write("\n")

    print(f"\n  Results saved to {report_path}")
    return results


if __name__ == "__main__":
    main()
