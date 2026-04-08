"""
Round 2 Evaluation — Hidden period: 2025-01-01 to 2025-03-31, $1000 starting capital.
Uses importlib for isolated imports to prevent sys.path/module cache pollution.
"""

import importlib.util
import sys
import traceback
from datetime import datetime

START = "2025-01-01"
END = "2025-03-31"
INITIAL_CAPITAL = 1000.0

AGENTS = [
    {
        "name": "Agent 1 — Quant (Donchian Breakout)",
        "module_name": "agent1_r2_strategy",
        "path": "/Users/rc/Projects/workspace/nautilus-trading/competition/agent-1-quant/round2/strategy.py",
    },
    {
        "name": "Agent 2 — Sentiment (Volume-Sentiment)",
        "module_name": "agent2_r2_strategy",
        "path": "/Users/rc/Projects/workspace/nautilus-trading/competition/agent-2-sentiment/round2/strategy.py",
    },
    {
        "name": "Agent 3 — Macro (Trend Following)",
        "module_name": "agent3_r2_strategy",
        "path": "/Users/rc/Projects/workspace/nautilus-trading/competition/agent-3-macro/round2/strategy.py",
    },
    {
        "name": "Agent 4 — ML (Multi-Strategy Tournament)",
        "module_name": "agent4_r2_strategy",
        "path": "/Users/rc/Projects/workspace/nautilus-trading/competition/agent-4-ml/round2/strategy.py",
    },
    {
        "name": "Agent 5 — Hybrid (Multi-Signal Ensemble)",
        "module_name": "agent5_r2_strategy",
        "path": "/Users/rc/Projects/workspace/nautilus-trading/competition/agent-5-hybrid/round2/strategy.py",
    },
]


def load_and_run(agent: dict) -> dict:
    """Load agent module in isolation and run backtest."""
    spec = importlib.util.spec_from_file_location(agent["module_name"], agent["path"])
    mod = importlib.util.module_from_spec(spec)
    # Do NOT insert into sys.modules to avoid cache pollution
    spec.loader.exec_module(mod)
    return mod.run_backtest(START, END, initial_capital=INITIAL_CAPITAL)


def main():
    print("=" * 70)
    print("ROUND 2 EVALUATION — HIDDEN PERIOD")
    print(f"Period: {START} to {END} | Starting Capital: ${INITIAL_CAPITAL:,.0f}")
    print("=" * 70)

    results = []

    for agent in AGENTS:
        print(f"\n{'─' * 70}")
        print(f"Running: {agent['name']}")
        print(f"{'─' * 70}")
        try:
            r = load_and_run(agent)
            results.append({
                "name": agent["name"],
                "final_equity": r.get("final_equity", INITIAL_CAPITAL),
                "total_return_pct": r.get("total_return_pct", 0.0),
                "sharpe_ratio": r.get("sharpe_ratio", 0.0),
                "max_drawdown_pct": r.get("max_drawdown_pct", 0.0),
                "num_trades": r.get("num_trades", 0),
                "win_rate": r.get("win_rate", 0.0),
                "trade_log": r.get("trade_log", []),
                "error": None,
            })
            print(f"  -> Return: {r.get('total_return_pct', 0.0):.2f}% | "
                  f"Equity: ${r.get('final_equity', 0):.2f} | "
                  f"Trades: {r.get('num_trades', 0)}")
        except Exception as e:
            print(f"  -> ERROR: {e}")
            traceback.print_exc()
            results.append({
                "name": agent["name"],
                "final_equity": INITIAL_CAPITAL,
                "total_return_pct": 0.0,
                "sharpe_ratio": 0.0,
                "max_drawdown_pct": 0.0,
                "num_trades": 0,
                "win_rate": 0.0,
                "trade_log": [],
                "error": str(e),
            })

    # Sort by return descending
    results.sort(key=lambda x: x["total_return_pct"], reverse=True)

    # Print leaderboard
    print("\n")
    print("=" * 70)
    print("ROUND 2 LEADERBOARD")
    print("=" * 70)
    print(f"{'Rank':<6}{'Agent':<45}{'Return':>10}{'Equity':>12}{'Sharpe':>8}{'MaxDD':>8}{'Trades':>8}{'WinR':>8}")
    print("-" * 105)

    for i, r in enumerate(results, 1):
        err_marker = " [ERR]" if r["error"] else ""
        print(
            f"{i:<6}"
            f"{r['name'][:44]:<45}"
            f"{r['total_return_pct']:>9.2f}%"
            f"  ${r['final_equity']:>9.2f}"
            f"{r['sharpe_ratio']:>8.2f}"
            f"{r['max_drawdown_pct']:>7.2f}%"
            f"{r['num_trades']:>8}"
            f"{r['win_rate']:>7.1f}%"
            f"{err_marker}"
        )

    # Determine winner
    print()
    winner = results[0] if results else None
    if winner and winner["total_return_pct"] > 0 and winner["error"] is None:
        print(f"WINNER: {winner['name']} with {winner['total_return_pct']:.2f}% return!")
    else:
        print("NO WINNER — no agent achieved a positive return this round.")

    # Save results to file
    output_path = "/Users/rc/Projects/workspace/nautilus-trading/competition/round2_results.txt"
    with open(output_path, "w") as f:
        f.write("ROUND 2 EVALUATION RESULTS\n")
        f.write(f"Hidden Period: {START} to {END}\n")
        f.write(f"Starting Capital: ${INITIAL_CAPITAL:,.0f}\n")
        f.write(f"Evaluated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("=" * 70 + "\n\n")

        f.write("LEADERBOARD\n")
        f.write("-" * 70 + "\n")
        f.write(f"{'Rank':<6}{'Agent':<40}{'Return':>10}{'Equity':>12}{'Sharpe':>8}{'MaxDD':>8}{'Trades':>8}{'WinR':>8}\n")
        f.write("-" * 100 + "\n")

        for i, r in enumerate(results, 1):
            err_marker = " [ERR]" if r["error"] else ""
            f.write(
                f"{i:<6}"
                f"{r['name'][:39]:<40}"
                f"{r['total_return_pct']:>9.2f}%"
                f"  ${r['final_equity']:>9.2f}"
                f"{r['sharpe_ratio']:>8.2f}"
                f"{r['max_drawdown_pct']:>7.2f}%"
                f"{r['num_trades']:>8}"
                f"{r['win_rate']:>7.1f}%"
                f"{err_marker}\n"
            )

        f.write("\n")
        if winner and winner["total_return_pct"] > 0 and winner["error"] is None:
            f.write(f"WINNER: {winner['name']} with {winner['total_return_pct']:.2f}% return!\n")
        else:
            f.write("NO WINNER — no agent achieved a positive return this round.\n")

        # Detailed per-agent results
        f.write("\n\n" + "=" * 70 + "\n")
        f.write("DETAILED RESULTS\n")
        f.write("=" * 70 + "\n")

        for r in results:
            f.write(f"\n--- {r['name']} ---\n")
            if r["error"]:
                f.write(f"  ERROR: {r['error']}\n")
                continue
            f.write(f"  Final Equity:  ${r['final_equity']:.2f}\n")
            f.write(f"  Total Return:  {r['total_return_pct']:.2f}%\n")
            f.write(f"  Sharpe Ratio:  {r['sharpe_ratio']:.2f}\n")
            f.write(f"  Max Drawdown:  {r['max_drawdown_pct']:.2f}%\n")
            f.write(f"  Num Trades:    {r['num_trades']}\n")
            f.write(f"  Win Rate:      {r['win_rate']:.1f}%\n")
            if r["trade_log"]:
                f.write(f"  Trade Log ({len(r['trade_log'])} entries):\n")
                for t in r["trade_log"][:30]:  # Cap at 30 trades for readability
                    f.write(f"    {t}\n")
                if len(r["trade_log"]) > 30:
                    f.write(f"    ... ({len(r['trade_log']) - 30} more trades)\n")

    print(f"\nResults saved to {output_path}")


if __name__ == "__main__":
    main()
