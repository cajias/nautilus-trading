"""
Round 9 Competition Evaluator
Hidden evaluation period: 2026-03-01 to 2026-04-30, $1,000 starting capital.
"""

import importlib.util
import sys
import traceback
import time
from datetime import datetime
from pathlib import Path

EVAL_START = "2026-03-01"
EVAL_END = "2026-04-30"
INITIAL_CAPITAL = 1000.0

AGENTS = [
    {"name": "Agent 1 - Quant", "path": "/Users/rc/Projects/workspace/nautilus-trading/competition/agent-1-quant/round9/strategy.py", "module": "agent1_r9"},
    {"name": "Agent 2 - Sentiment", "path": "/Users/rc/Projects/workspace/nautilus-trading/competition/agent-2-sentiment/round9/strategy.py", "module": "agent2_r9"},
    {"name": "Agent 3 - Macro", "path": "/Users/rc/Projects/workspace/nautilus-trading/competition/agent-3-macro/round9/strategy.py", "module": "agent3_r9"},
    {"name": "Agent 4 - ML", "path": "/Users/rc/Projects/workspace/nautilus-trading/competition/agent-4-ml/round9/strategy.py", "module": "agent4_r9"},
    {"name": "Agent 5 - Hybrid", "path": "/Users/rc/Projects/workspace/nautilus-trading/competition/agent-5-hybrid/round9/strategy.py", "module": "agent5_r9"},
]


def load_module(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def run_agent(agent: dict) -> dict:
    print(f"\n{'='*70}\nRunning: {agent['name']}\n{'='*70}")
    start_time = time.time()
    mod = load_module(agent["module"], agent["path"])
    raw = mod.run_backtest(start=EVAL_START, end=EVAL_END, initial_capital=INITIAL_CAPITAL)
    result = raw["test"] if isinstance(raw, dict) and "test" in raw else raw
    result["elapsed_seconds"] = round(time.time() - start_time, 1)
    return result


def main():
    print("=" * 70)
    print("ROUND 9 COMPETITION EVALUATION")
    print(f"Period: {EVAL_START} to {EVAL_END} | Capital: ${INITIAL_CAPITAL:,.0f}")
    print(f"Evaluated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    results = []
    for agent in AGENTS:
        try:
            results.append({"agent": agent["name"], **run_agent(agent)})
        except Exception as e:
            print(f"\n  ERROR: {e}")
            traceback.print_exc()
            results.append({
                "agent": agent["name"], "final_equity": INITIAL_CAPITAL,
                "total_return_pct": 0.0, "sharpe_ratio": 0.0,
                "max_drawdown_pct": 0.0, "num_trades": 0, "win_rate": 0.0,
                "error": str(e),
            })

    results.sort(key=lambda r: r.get("total_return_pct", 0), reverse=True)

    print("\n" + "=" * 70)
    print("ROUND 9 LEADERBOARD")
    print("=" * 70)
    header = f"{'Rank':<6}{'Agent':<30}{'Return %':>10}{'Equity':>10}{'Sharpe':>8}{'MaxDD%':>8}{'Trades':>8}{'WinR%':>8}"
    print(header)
    print("-" * 88)
    for i, r in enumerate(results, 1):
        err = " [ERROR]" if "error" in r else ""
        print(f"{i:<6}{r['agent'][:29]:<30}"
              f"{r.get('total_return_pct', 0):>+10.2f}"
              f"{r.get('final_equity', INITIAL_CAPITAL):>10.2f}"
              f"{r.get('sharpe_ratio', 0):>8.2f}"
              f"{r.get('max_drawdown_pct', 0):>8.2f}"
              f"{r.get('num_trades', 0):>8}"
              f"{r.get('win_rate', 0):>8.1f}{err}")
        strat = r.get("strategy_name", "")
        if strat:
            print(f"        Strategy: {strat}")

    winner = results[0] if results else None
    print()
    if winner and winner.get("total_return_pct", 0) > 0 and "error" not in winner:
        print(f"WINNER: {winner['agent']} with {winner['total_return_pct']:+.2f}% return!")
    else:
        print("NO WINNER: No agent achieved a positive return.")

    output_path = Path("/Users/rc/Projects/workspace/nautilus-trading/competition/round9_results.txt")
    with open(output_path, "w") as f:
        f.write("ROUND 9 COMPETITION RESULTS\n")
        f.write(f"Evaluation Period: {EVAL_START} to {EVAL_END}\n")
        f.write(f"Starting Capital: ${INITIAL_CAPITAL:,.0f}\n")
        f.write(f"Evaluated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("=" * 70 + "\n\n")
        f.write(header + "\n")
        f.write("-" * 88 + "\n")
        for i, r in enumerate(results, 1):
            err = " [ERROR]" if "error" in r else ""
            f.write(f"{i:<6}{r['agent'][:29]:<30}"
                    f"{r.get('total_return_pct', 0):>+10.2f}"
                    f"{r.get('final_equity', INITIAL_CAPITAL):>10.2f}"
                    f"{r.get('sharpe_ratio', 0):>8.2f}"
                    f"{r.get('max_drawdown_pct', 0):>8.2f}"
                    f"{r.get('num_trades', 0):>8}"
                    f"{r.get('win_rate', 0):>8.1f}{err}\n")
            strat = r.get("strategy_name", "")
            if strat:
                f.write(f"        Strategy: {strat}\n")
        f.write("\n")
        if winner and winner.get("total_return_pct", 0) > 0 and "error" not in winner:
            f.write(f"WINNER: {winner['agent']} with {winner['total_return_pct']:+.2f}% return!\n")
        else:
            f.write("NO WINNER: No agent achieved a positive return.\n")
        f.write("\n\nDetailed Results:\n" + "=" * 70 + "\n")
        for r in results:
            f.write(f"\n{r['agent']}\n" + "-" * 40 + "\n")
            for k, v in r.items():
                if k in ("agent", "trade_log", "all_strategies", "strategy_params", "equity_curve", "trades"):
                    continue
                f.write(f"  {k}: {v}\n")

    print(f"\nResults saved to {output_path}")


if __name__ == "__main__":
    main()
