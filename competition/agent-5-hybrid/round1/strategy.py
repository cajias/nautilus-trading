"""
Agent 5 - Hybrid Strategist: Round 1 Runner
============================================
Downloads Binance data, runs the multi-signal ensemble strategy
on TRAIN (Jan-Jun 2024) for validation, then TEST (Jul-Sep 2024) for scoring.

The core strategy is in ../strategies/round1.py (used by the evaluator).
This script exercises it on the competition-specified periods.
"""

import sys
from pathlib import Path

# Add parent so we can import the strategy module
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from strategies.round1 import (
    download_binance_klines,
    run_backtest,
    print_results,
)

ASSETS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
INITIAL_CAPITAL = 1000.0

# Need warmup buffer before train period for indicators (~120 bars of 4H = 20 days)
DATA_START = "2023-10-01"  # 3 months warmup before Jan 2024
TRAIN_START = "2024-01-01"
TRAIN_END = "2024-06-30"
TEST_START = "2024-07-01"
TEST_END = "2024-09-30"
DATA_END = "2024-10-01"


def main():
    print("=" * 70)
    print("  Agent 5 - Hybrid Strategist: Round 1")
    print("  TRAIN: Jan-Jun 2024 | TEST: Jul-Sep 2024")
    print("=" * 70)

    # Download data covering warmup + train + test
    asset_data = {}
    for symbol in ASSETS:
        df = download_binance_klines(
            symbol=symbol,
            interval="4h",
            start=DATA_START,
            end=DATA_END,
        )
        print(f"  {symbol}: {len(df)} bars ({df.index[0]} to {df.index[-1]})")
        asset_data[symbol] = df

    # --- TRAIN period (validation, not used for fitting -- strategy is rule-based) ---
    print("\n" + "=" * 70)
    print("  TRAIN PERIOD: Jan 1 - Jun 30, 2024")
    print("=" * 70)
    train_results = run_backtest(
        asset_data,
        start_date=TRAIN_START,
        end_date=TRAIN_END,
        initial_capital=INITIAL_CAPITAL,
    )
    train_output = print_results(train_results)
    print(train_output)

    # --- TEST period (must be profitable) ---
    print("\n" + "=" * 70)
    print("  TEST PERIOD: Jul 1 - Sep 30, 2024")
    print("=" * 70)
    test_results = run_backtest(
        asset_data,
        start_date=TEST_START,
        end_date=TEST_END,
        initial_capital=INITIAL_CAPITAL,
    )
    test_output = print_results(test_results)
    print(test_output)

    # --- Summary ---
    print("\n" + "=" * 70)
    print("  SUMMARY")
    print("=" * 70)
    print(f"  TRAIN Return: {train_results['total_return_pct']:+.2f}%  "
          f"Sharpe: {train_results['sharpe_ratio']:.2f}  "
          f"MaxDD: {train_results['max_drawdown_pct']:.2f}%  "
          f"Trades: {train_results['n_trades']}")
    print(f"  TEST Return:  {test_results['total_return_pct']:+.2f}%  "
          f"Sharpe: {test_results['sharpe_ratio']:.2f}  "
          f"MaxDD: {test_results['max_drawdown_pct']:.2f}%  "
          f"Trades: {test_results['n_trades']}")

    profitable = test_results['total_return_pct'] > 0
    print(f"\n  TEST PROFITABLE: {'YES' if profitable else 'NO'}")
    print("=" * 70)

    # Save results
    out_dir = Path(__file__).resolve().parent
    results_text = (
        f"Agent 5 - Hybrid Strategist: Round 1 Results\n"
        f"{'=' * 50}\n\n"
        f"TRAIN (Jan-Jun 2024):\n{train_output}\n\n"
        f"TEST (Jul-Sep 2024):\n{test_output}\n\n"
        f"SUMMARY:\n"
        f"  TRAIN Return: {train_results['total_return_pct']:+.2f}%\n"
        f"  TEST Return:  {test_results['total_return_pct']:+.2f}%\n"
        f"  TEST Profitable: {'YES' if profitable else 'NO'}\n"
    )
    with open(out_dir / "results.txt", "w") as f:
        f.write(results_text)
    print(f"\nResults saved to {out_dir / 'results.txt'}")

    return test_results


if __name__ == "__main__":
    main()
