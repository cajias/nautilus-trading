"""
Round 5 Competition Evaluator
Hidden evaluation period: 2025-10-01 to 2025-12-31, $1,000 starting capital.
"""

import importlib.util
import sys
import traceback
import time
from pathlib import Path

EVAL_START = "2025-10-01"
EVAL_END = "2025-12-31"
INITIAL_CAPITAL = 1000.0

AGENTS = [
    {
        "name": "Agent 1 - Quant (RSI Dip-Buyer Tournament)",
        "path": "/Users/rc/Projects/workspace/nautilus-trading/competition/agent-1-quant/round5/strategy.py",
        "module": "agent1_r5",
    },
    {
        "name": "Agent 2 - Sentiment (Stability-Scored RSI Dip Buyer)",
        "path": "/Users/rc/Projects/workspace/nautilus-trading/competition/agent-2-sentiment/round5/strategy.py",
        "module": "agent2_r5",
    },
    {
        "name": "Agent 3 - Macro (Weekly EMA Momentum + Mean Reversion)",
        "path": "/Users/rc/Projects/workspace/nautilus-trading/competition/agent-3-macro/round5/strategy.py",
        "module": "agent3_r5",
    },
    {
        "name": "Agent 4 - ML (78-Variant Walk-Forward Tournament)",
        "path": "/Users/rc/Projects/workspace/nautilus-trading/competition/agent-4-ml/round5/strategy.py",
        "module": "agent4_r5",
    },
    {
        "name": "Agent 5 - Hybrid (BB Mean Reversion Tournament)",
        "path": "/Users/rc/Projects/workspace/nautilus-trading/competition/agent-5-hybrid/round5/strategy.py",
        "module": "agent5_r5",
    },
]


def load_module(name: str, path: str):
    """Load a module from file path with isolated import."""
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def run_agent(agent: dict) -> dict:
    """Run a single agent's backtest, return standardized results."""
    print(f"\n{'='*70}")
    print(f"Running: {agent['name']}")
    print(f"{'='*70}")

    start_time = time.time()
    mod = load_module(agent["module"], agent["path"])

    # Agent 5 needs special handling: run_tournament first on train data, then run_backtest with winner
    if agent["module"] == "agent5_r5":
        import pandas as pd
        # Train tournament on Jan-Jun 2025 data
        train_start, train_end = "2025-01-01", "2025-06-30"
        df_train = mod.fetch_klines(train_start, train_end, interval="1d")
        print(f"Training data: {df_train.index[0].date()} to {df_train.index[-1].date()}, {len(df_train)} bars")
        ranked = mod.run_tournament(df_train, INITIAL_CAPITAL)
        if not ranked:
            return {"error": "No viable strategy found in tournament",
                    "final_equity": INITIAL_CAPITAL, "total_return_pct": 0,
                    "sharpe_ratio": 0, "max_drawdown_pct": 0,
                    "num_trades": 0, "win_rate": 0}
        strat_name, strat_cfg, _, _ = ranked[0]
        print(f"Tournament winner: {strat_name} with config {strat_cfg}")
        result = mod.run_backtest(EVAL_START, EVAL_END, initial_capital=INITIAL_CAPITAL,
                                  strategy_name=strat_name, strategy_config=strat_cfg)

    # Agent 4 runs internal walk-forward tournament; pass eval dates
    elif agent["module"] == "agent4_r5":
        raw = mod.run_backtest(start=EVAL_START, end=EVAL_END, initial_capital=INITIAL_CAPITAL)
        # Agent 4 may return {"train": {...}, "test": {...}}
        if isinstance(raw, dict) and "test" in raw:
            result = raw["test"]
        else:
            result = raw

    # Agents 1, 2, 3: standard interface
    else:
        result = mod.run_backtest(start=EVAL_START, end=EVAL_END, initial_capital=INITIAL_CAPITAL)

    elapsed = time.time() - start_time
    result["elapsed_seconds"] = round(elapsed, 1)
    return result


def main():
    from datetime import datetime

    print("=" * 70)
    print("ROUND 5 COMPETITION EVALUATION")
    print(f"Period: {EVAL_START} to {EVAL_END} | Capital: ${INITIAL_CAPITAL:,.0f}")
    print(f"Evaluated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    results = []
    for agent in AGENTS:
        try:
            result = run_agent(agent)
            results.append({"agent": agent["name"], **result})
        except Exception as e:
            print(f"\n  ERROR: {e}")
            traceback.print_exc()
            results.append({
                "agent": agent["name"],
                "final_equity": INITIAL_CAPITAL,
                "total_return_pct": 0.0,
                "sharpe_ratio": 0.0,
                "max_drawdown_pct": 0.0,
                "num_trades": 0,
                "win_rate": 0.0,
                "error": str(e),
            })

    # Sort by return
    results.sort(key=lambda r: r.get("total_return_pct", 0), reverse=True)

    # Print leaderboard
    print("\n")
    print("=" * 70)
    print("ROUND 5 LEADERBOARD")
    print("=" * 70)
    print(f"{'Rank':<6}{'Agent':<55}{'Return %':>10}{'Equity':>10}{'Sharpe':>8}{'MaxDD%':>8}{'Trades':>8}{'WinR%':>8}")
    print("-" * 113)

    for i, r in enumerate(results, 1):
        err = " [ERROR]" if "error" in r else ""
        strat = r.get("strategy_name", "")
        if strat:
            strat = f" ({strat})"
        print(f"{i:<6}{r['agent'][:54]:<55}"
              f"{r.get('total_return_pct', 0):>+10.2f}"
              f"{r.get('final_equity', INITIAL_CAPITAL):>10.2f}"
              f"{r.get('sharpe_ratio', 0):>8.2f}"
              f"{r.get('max_drawdown_pct', 0):>8.2f}"
              f"{r.get('num_trades', 0):>8}"
              f"{r.get('win_rate', 0):>8.1f}"
              f"{err}")
        if strat:
            print(f"{'':>6}  Strategy: {strat}")

    # Determine winner
    print()
    winner = results[0] if results else None
    if winner and winner.get("total_return_pct", 0) > 0 and "error" not in winner:
        print(f"WINNER: {winner['agent']} with {winner['total_return_pct']:+.2f}% return!")
    else:
        print("NO WINNER: No agent achieved a positive return.")

    # Save results
    output_path = Path("/Users/rc/Projects/workspace/nautilus-trading/competition/round5_results.txt")
    with open(output_path, "w") as f:
        eval_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        f.write("ROUND 5 COMPETITION RESULTS\n")
        f.write(f"Evaluation Period: {EVAL_START} to {EVAL_END}\n")
        f.write(f"Starting Capital: ${INITIAL_CAPITAL:,.0f}\n")
        f.write(f"Evaluated: {eval_time}\n")
        f.write("=" * 70 + "\n\n")

        f.write(f"{'Rank':<6}{'Agent':<55}{'Return %':>10}{'Equity':>10}{'Sharpe':>8}{'MaxDD%':>8}{'Trades':>8}{'WinR%':>8}\n")
        f.write("-" * 113 + "\n")

        for i, r in enumerate(results, 1):
            err = " [ERROR]" if "error" in r else ""
            f.write(f"{i:<6}{r['agent'][:54]:<55}"
                    f"{r.get('total_return_pct', 0):>+10.2f}"
                    f"{r.get('final_equity', INITIAL_CAPITAL):>10.2f}"
                    f"{r.get('sharpe_ratio', 0):>8.2f}"
                    f"{r.get('max_drawdown_pct', 0):>8.2f}"
                    f"{r.get('num_trades', 0):>8}"
                    f"{r.get('win_rate', 0):>8.1f}"
                    f"{err}\n")
            strat = r.get("strategy_name", "")
            if strat:
                f.write(f"{'':>6}  Strategy: {strat}\n")

        f.write("\n")
        if winner and winner.get("total_return_pct", 0) > 0 and "error" not in winner:
            f.write(f"WINNER: {winner['agent']} with {winner['total_return_pct']:+.2f}% return!\n")
        else:
            f.write("NO WINNER: No agent achieved a positive return.\n")

        f.write("\n\nDetailed Results:\n")
        f.write("=" * 70 + "\n")
        for r in results:
            f.write(f"\n{r['agent']}\n")
            f.write("-" * 40 + "\n")
            for k, v in r.items():
                if k in ("agent", "trade_log", "all_strategies", "strategy_params", "equity_curve"):
                    continue
                f.write(f"  {k}: {v}\n")

    print(f"\nResults saved to {output_path}")


if __name__ == "__main__":
    main()
