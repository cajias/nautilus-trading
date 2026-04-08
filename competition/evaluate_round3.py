"""
Round 3 Competition Evaluator
Hidden evaluation period: 2025-04-01 to 2025-06-30
$1,000 starting capital per agent.
"""

import importlib.util
import json
import sys
import traceback
from datetime import datetime

AGENTS = [
    {
        "name": "Agent 1 — Quantitative Trader",
        "desc": "Regime-adaptive multi-strategy (4H BTC)",
        "path": "/Users/rc/Projects/workspace/nautilus-trading/competition/agent-1-quant/round3/strategy.py",
        "module": "agent1_r3_strategy",
    },
    {
        "name": "Agent 2 — Sentiment Trader",
        "desc": "Dual-mode breakout + panic-buy (BTC)",
        "path": "/Users/rc/Projects/workspace/nautilus-trading/competition/agent-2-sentiment/round3/strategy.py",
        "module": "agent2_r3_strategy",
    },
    {
        "name": "Agent 3 — Macro Strategist",
        "desc": "Weekly momentum + dip-buying (BTC)",
        "path": "/Users/rc/Projects/workspace/nautilus-trading/competition/agent-3-macro/round3/strategy.py",
        "module": "agent3_r3_strategy",
    },
    {
        "name": "Agent 4 — ML Engineer",
        "desc": "Multi-strategy tournament 97 variants (BTC/ETH/SOL)",
        "path": "/Users/rc/Projects/workspace/nautilus-trading/competition/agent-4-ml/round3/strategy.py",
        "module": "agent4_r3_strategy",
    },
    {
        "name": "Agent 5 — Hybrid Strategist",
        "desc": "Regime-adaptive daily BTC",
        "path": "/Users/rc/Projects/workspace/nautilus-trading/competition/agent-5-hybrid/round3/strategy.py",
        "module": "agent5_r3_strategy",
    },
]

START = "2025-04-01"
END = "2025-06-30"
CAPITAL = 1000.0


def load_and_run(agent: dict) -> dict:
    """Load a strategy module via importlib and run its backtest."""
    spec = importlib.util.spec_from_file_location(agent["module"], agent["path"])
    mod = importlib.util.module_from_spec(spec)
    # Do NOT insert into sys.modules to avoid cross-contamination
    spec.loader.exec_module(mod)
    result = mod.run_backtest(START, END, initial_capital=CAPITAL)
    return result


def main():
    print("=" * 70)
    print("ROUND 3 — HIDDEN EVALUATION PERIOD")
    print(f"Period: {START} to {END} | Capital: ${CAPITAL:,.0f}")
    print(f"Evaluation time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    results = []

    for agent in AGENTS:
        print(f"\n{'─' * 70}")
        print(f"Running: {agent['name']}")
        print(f"  Strategy: {agent['desc']}")
        print(f"{'─' * 70}")

        try:
            result = load_and_run(agent)
            result["agent_name"] = agent["name"]
            result["agent_desc"] = agent["desc"]
            result["status"] = "OK"
            results.append(result)

            print(f"  Final equity:  ${result['final_equity']:,.2f}")
            print(f"  Return:        {result['total_return_pct']:+.2f}%")
            print(f"  Sharpe:        {result.get('sharpe_ratio', 'N/A')}")
            print(f"  Max drawdown:  {result.get('max_drawdown_pct', 'N/A')}%")
            print(f"  Trades:        {result.get('num_trades', 'N/A')}")
            print(f"  Win rate:      {result.get('win_rate', 'N/A')}%")

        except Exception as e:
            print(f"  ERROR: {e}")
            traceback.print_exc()
            results.append({
                "agent_name": agent["name"],
                "agent_desc": agent["desc"],
                "status": "ERROR",
                "error": str(e),
                "final_equity": CAPITAL,
                "total_return_pct": 0.0,
            })

    # Sort by return
    results.sort(key=lambda r: r.get("total_return_pct", -999), reverse=True)

    # Print leaderboard
    print("\n")
    print("=" * 70)
    print("ROUND 3 LEADERBOARD")
    print("=" * 70)
    print(f"{'Rank':<6} {'Agent':<35} {'Return':>10} {'Equity':>12} {'Sharpe':>8} {'MaxDD':>8} {'Trades':>7} {'WinR':>7} {'Status'}")
    print("─" * 100)

    for i, r in enumerate(results, 1):
        ret = r.get("total_return_pct", 0.0)
        eq = r.get("final_equity", CAPITAL)
        sharpe = r.get("sharpe_ratio", "N/A")
        mdd = r.get("max_drawdown_pct", "N/A")
        trades = r.get("num_trades", "N/A")
        wr = r.get("win_rate", "N/A")
        status = r.get("status", "?")

        sharpe_str = f"{sharpe:.2f}" if isinstance(sharpe, (int, float)) else str(sharpe)
        mdd_str = f"{mdd:.1f}%" if isinstance(mdd, (int, float)) else str(mdd)
        wr_str = f"{wr:.0f}%" if isinstance(wr, (int, float)) else str(wr)
        trades_str = str(trades)

        print(f"  {i:<4} {r['agent_name']:<35} {ret:>+9.2f}% ${eq:>10,.2f} {sharpe_str:>8} {mdd_str:>8} {trades_str:>7} {wr_str:>7}  {status}")

    # Winner
    print()
    winner = results[0] if results else None
    if winner and winner.get("total_return_pct", 0) > 0 and winner.get("status") == "OK":
        print(f"WINNER: {winner['agent_name']} with {winner['total_return_pct']:+.2f}% return!")
    else:
        has_positive = any(
            r.get("total_return_pct", 0) > 0 and r.get("status") == "OK"
            for r in results
        )
        if has_positive:
            for r in results:
                if r.get("total_return_pct", 0) > 0 and r.get("status") == "OK":
                    print(f"WINNER: {r['agent_name']} with {r['total_return_pct']:+.2f}% return!")
                    break
        else:
            print("NO WINNER — No agent achieved a positive return.")

    # Save results
    output_path = "/Users/rc/Projects/workspace/nautilus-trading/competition/round3_results.txt"
    with open(output_path, "w") as f:
        f.write("ROUND 3 — HIDDEN EVALUATION RESULTS\n")
        f.write(f"Period: {START} to {END} | Capital: ${CAPITAL:,.0f}\n")
        f.write(f"Evaluated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("=" * 70 + "\n\n")

        f.write("LEADERBOARD\n")
        f.write("─" * 70 + "\n")
        for i, r in enumerate(results, 1):
            ret = r.get("total_return_pct", 0.0)
            eq = r.get("final_equity", CAPITAL)
            sharpe = r.get("sharpe_ratio", "N/A")
            mdd = r.get("max_drawdown_pct", "N/A")
            trades = r.get("num_trades", "N/A")
            wr = r.get("win_rate", "N/A")
            status = r.get("status", "?")

            f.write(f"\n#{i} {r['agent_name']}\n")
            f.write(f"   Strategy:     {r.get('agent_desc', 'N/A')}\n")
            f.write(f"   Status:       {status}\n")
            if status == "ERROR":
                f.write(f"   Error:        {r.get('error', 'unknown')}\n")
            f.write(f"   Final equity: ${eq:,.2f}\n")
            f.write(f"   Return:       {ret:+.2f}%\n")
            f.write(f"   Sharpe:       {sharpe}\n")
            f.write(f"   Max Drawdown: {mdd}%\n" if isinstance(mdd, (int, float)) else f"   Max Drawdown: {mdd}\n")
            f.write(f"   Trades:       {trades}\n")
            f.write(f"   Win Rate:     {wr}%\n" if isinstance(wr, (int, float)) else f"   Win Rate:     {wr}\n")

        f.write("\n" + "=" * 70 + "\n")
        if winner and winner.get("total_return_pct", 0) > 0 and winner.get("status") == "OK":
            f.write(f"WINNER: {winner['agent_name']} with {winner['total_return_pct']:+.2f}% return!\n")
        else:
            has_positive = any(
                r.get("total_return_pct", 0) > 0 and r.get("status") == "OK"
                for r in results
            )
            if has_positive:
                for r in results:
                    if r.get("total_return_pct", 0) > 0 and r.get("status") == "OK":
                        f.write(f"WINNER: {r['agent_name']} with {r['total_return_pct']:+.2f}% return!\n")
                        break
            else:
                f.write("NO WINNER — No agent achieved a positive return.\n")

        # Detailed trade logs
        f.write("\n\nDETAILED TRADE LOGS\n")
        f.write("=" * 70 + "\n")
        for r in results:
            f.write(f"\n{r['agent_name']}\n")
            f.write("─" * 40 + "\n")
            if r.get("status") == "ERROR":
                f.write(f"Error: {r.get('error', 'unknown')}\n")
                continue
            trade_log = r.get("trade_log", [])
            if not trade_log:
                f.write("No trades.\n")
            else:
                for t in trade_log:
                    f.write(f"  {json.dumps(t)}\n")

    print(f"\nResults saved to {output_path}")


if __name__ == "__main__":
    main()
