# A_fr — Feedback Reflection Agent

## Role

The feedback reflection agent "deconstructs feedback `F` and formulates precise optimization plans, which are then transmitted to `A_be` for programmatic refinement" `[source: arxiv-2510.04787-html]`. It "employs mathematical reasoning `γ` in a three-step optimization process: first organizing risk scenarios from feedback `F` and transforming them into linear programming problems; then solving for the feasible parameter solution space; and finally optimizing parameters within the constrained space to maximize performance" `[source: arxiv-2510.04787-html]`. It is also the agent that enforces the **hierarchical minimal-intervention principle** — escalate from parameter to function to strategy layer only when the lower layer cannot resolve the issue `[source: arxiv-2510.04787-html]`. In the TiMi paper it is a math-specialist LLM (DeepSeek-R1).

## Inputs

- `competition/agent-N-timi/round<N>/strategy.py` — current bot under evolution.
- `competition/agent-N-timi/round<N>/backtest_out/*.json` — the raw `PortfolioAnalyzer` dump from the most recent TRAIN+TEST run.
- Raw trade log (CSV/parquet) — fills, timestamps, entry/exit prices, per-trade PnL.
- Any live testnet telemetry if available (`docs/research/timi/telemetry/*.jsonl`, future).
- The parameter-range manifest `docs/research/timi/ranges.yml` (to be created) — declares the feasible domain for each `TimiConfig` field.

## Outputs

One markdown file per refinement iteration: `docs/research/timi/reflection/round<N>__<PAIR>__<iter>.md`. Each file must contain:
- **Risk scenario identification** — structured description of the observed failure mode (e.g., "position too large during 2024-08-05 volatility spike, MDD 14% in a 30-min window").
- **LP formulation** — the actual linear program: variables (which `TimiConfig` fields), objective (e.g., maximize `total_return` or minimize `max_drawdown`), constraints (e.g., `stop_loss_pct ≤ 2 * median_ATR_pct`).
- **LP solution** — proposed new parameter values. Produced by `scipy.optimize.linprog` inside the agent's sandboxed `Bash` call.
- **Escalation decision** — `layer: parameter` (default), `layer: function`, or `layer: strategy`. Must be justified: parameter escalation means "LP was feasible and produced a better objective". Function escalation means "LP was infeasible or the improvement was trivial; swap component X for Y". Strategy escalation means "function swap is insufficient; rethink signal generation".
- **Stop-or-continue recommendation** — "continue" (hand back to A_be), "converged" (promote as round winner candidate), or "diverged" (abort, flag for human review).

**It does NOT write code.** It writes directives that A_be translates into diffs.

## Claude Code agent type

`general-purpose` subagent, launched under the name `timi-reflection`, with a math/reasoning-focused system prompt.

**System prompt must emphasize:**
- "Your output is a linear programming formulation, not prose advice. Every numeric change you propose must come from an LP solution, not from intuition."
- "The minimal intervention principle is a hard rule: always try the parameter layer first. Escalate to function layer only with evidence. Escalate to strategy layer only if function-layer allowlist cannot address the risk."
- "You consume `PortfolioAnalyzer` output. Remember: monetary columns are strings like `'-9.48 USD'` — strip the currency with `.str.replace(r'\\s+\\w+$', '', regex=True).astype(float)` before any numeric operation."
- "You may run `scipy.optimize.linprog` inside a sandboxed Python one-liner. You may NOT call external APIs."
- "If the LP is infeasible, you must report that and escalate. Do not 'round' or 'approximate' your way out of infeasibility."
- "You never write Python source files. Your output is markdown with formal LP blocks."

## Required tools

- `Read` — `strategy.py`, backtest outputs, trade logs.
- `Bash` — specifically:
  - `cd nautilus && uv run python -c "from scipy.optimize import linprog; ..."` for solving LPs.
  - `cd nautilus && uv run python -c "import pandas as pd; ..."` for parsing trade logs.
  - Re-running backtests is NOT A_fr's job — it hands back to A_be which runs the validator + backtest.
- `Grep`, `Glob` — navigating reflection history.
- `Write`, `Edit` — ONLY inside `docs/research/timi/reflection/`.

**Denied:** any `Write` or `Edit` under `competition/`, `strategies/`, `nautilus/`. Any `Bash` command that includes `git`, `curl`, `ssh`, or anything not explicitly whitelisted above. No `WebFetch`.

## Invocation trigger

- **Dependency:** a completed backtest run exists for the current iteration of `competition/agent-N-timi/round<N>/`.
- **Orchestrator:** dispatched after A_be signals "backtest complete".
- **Termination:** the loop stops when A_fr emits `converged: true` OR a max-iteration count is reached OR `diverged: true`. The stopping criterion is an open question (see OPEN_QUESTIONS.md Q2).

## Failure modes

| Failure | Response |
|---------|----------|
| `PortfolioAnalyzer` output missing or malformed | Agent aborts, flags `needs_backtest: true`, orchestrator re-invokes A_be. |
| LP infeasible at the parameter layer | Automatic escalation to function layer. If no valid function swap exists, escalation to strategy layer. If strategy layer is already where we are, emit `diverged: true` and stop. |
| `scipy` call produces a solution outside `ranges.yml` bounds | Agent must re-solve with the missing bounds added as explicit constraints, not clip silently. |
| Agent proposes a non-numeric parameter change (e.g., "change the instrument") | Forbidden — not a parameter-layer change. Must be escalated and flagged as a strategy-layer modification. |
| Agent's LP objective contradicts the competition evaluator (e.g., optimizes Sharpe when evaluator uses raw return) | Bug in the agent's spec; hard rule: objective MUST match the current round's `evaluate_roundN.py` scoring metric. |
| Agent loops without improvement (`J(π_Θ)` flat for K iterations) | Orchestrator enforces a hard stop at K=5. |
| Live telemetry contradicts backtest result | Agent must weight live data higher; report a regime-shift hypothesis and escalate if warranted. |

## Testing approach

**Pure-LP tests** in `tests/test_timi_reflection.py`:
- Given a fixture backtest output where "stop was too loose, took 14% MDD in one trade", assert A_fr produces an LP with `max_drawdown` constraint and proposes a tighter `stop_loss_pct`.
- Given an infeasible scenario (no parameter combination satisfies all risk constraints), assert A_fr escalates to function layer.
- Given a scenario where the current parameters are already near the Pareto frontier, assert A_fr emits `converged: true`.
- Snapshot-test the LP formulation markdown structure.

**Integration test:** full loop with A_be + A_fr on a synthetic pair where the gold solution is known. Assert the loop converges in ≤ N iterations to within ε of the gold parameter values.

**Regression guard:** every LP formulation A_fr emits must be validated by a separate "solver sanity check" that re-runs the LP with a different solver (or `linprog(method='highs')` vs `linprog(method='simplex')`). Disagreement is a bug.

**Property test:** A_fr must never propose parameter values outside `ranges.yml`. Fuzz the input and assert invariance.
