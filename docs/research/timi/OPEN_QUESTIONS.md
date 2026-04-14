# TiMi Port — Open Questions for the User

Every numbered item below is a decision we need from you before any code lands. Items are grouped; the most urgent three are flagged with `[URGENT]`.

## LLM and agent-runtime choices

1. **[ANSWERED 2026-04-09] LLM backend: Claude Code in programmatic mode.** All four agents run as Claude Code headless / Agent-SDK invocations. Implementation path: orchestrator spawns `claude -p` subprocess per agent (or uses `claude_agent_sdk` Python client), specializing each agent via system prompt + tool allowlist. Single model across all four — we specialize by prompt and tool restrictions, not by backend. This supersedes the paper's DeepSeek-V3 / Qwen-Coder / DeepSeek-R1 hybrid. Follow-ups: (a) decide whether to use `claude -p` headless or `claude_agent_sdk`, (b) define where the per-agent system prompts live (probably `.claude/agents/timi-*.md` for reuse via the Agent tool, or inline strings in a Python orchestrator).

2. **Are we building these as first-class Claude Code subagents under `~/.claude/agents/` (global) or project-local under `.claude/agents/` (per-repo)?** Project-local is tidier for a research port but means no reuse. Global is reusable but pollutes your global agent list.

3. **Should A_be use the `crypto-competition` skill or bypass it?** The skill encodes our repo conventions; using it means A_be inherits every future update, but also every future skill bug. Bypass means duplication but isolation.

## Strict boundaries for "minimal intervention"

4. **[URGENT] How strict is the 3-laws enforcement: lint-level or AST-level?** Lint (ruff/mypy) is cheap and catches most violations but can be gamed (e.g., a cohesion-violating function that passes `ruff`). AST-level means writing a custom `ast.NodeVisitor` that checks cohesion, dependency direction, and parameter externalization. I recommend AST-level because the whole point of the laws is that `A_be` can't cheat them — but it's a week of work.

5. **Does A_fr have authority to kill a running testnet strategy, or only to propose changes to the next offline iteration?** The paper's live/optimization loop is ambiguous on this. I'd recommend "propose only" — any live kill-switch goes through a human or a pre-declared circuit breaker, never the LLM.

6. **What is the allowlist for function-layer swaps?** The paper says the escalation happens but doesn't define the swap library. We need a file `docs/research/timi/function_swaps.md` that enumerates swappable components (EMA↔SMA, ATR-stop↔chandelier-stop, fixed-size↔Kelly-fraction, etc.). Who owns writing this list — you, or a future task?

## Competition constraints vs TiMi continuous-evolution

7. **[ANSWERED 2026-04-09] Per-round reset (option a).** A_fr converges within round N's TRAIN+TEST, submission is frozen, eval happens on hidden window, next round starts cold. No warm-start of A_ma output, no inherited parameters, no memory across rounds. Tradeoff: throws away accumulated learning; benefit: cleanest hidden-eval-leak story (the only forbidden window is the current round's, nothing to track across rounds), simplest orchestration, matches competition cadence exactly.

8. **Are the TiMi agents a 6th competitor (`agent-6-timi`), or do they replace one of the existing 5 personas?** If replacement, which one? If addition, we need `evaluate_roundN.py` to handle a 6-agent field.

9. **If the competition requires hidden-eval-period submissions to be fully frozen, how do we prove that A_be/A_fr did not peek at the eval window during optimization?** The paper doesn't deal with this because it has no hidden-eval concept. We need a mechanical block, not just a prompt-level instruction. Possibly: A_fr's `Bash` tool gets a wrapper that refuses to read any parquet file whose date range overlaps the eval window for the current round.

## Data sources

10. **The paper's `A_ma` uses technical indicators, not news/sentiment.** We inherit that. But: do we want to add a **fifth, non-TiMi agent** that ingests news (RSS feeds, Twitter, on-chain data) and feeds it in as an auxiliary signal, fully outside the TiMi core? If yes, what's the news source given we have no paid feed (CryptoPanic free tier? RSS aggregator of Cointelegraph/Decrypt? Binance announcements?)

11. **What indicator universe `I` do we seed A_ma with?** The paper lists volume and amplitude as examples; we should probably start with: EMA(fast/slow), SMA, RSI(14), ATR(14), ADX(14), Bollinger(20,2), VWAP, Donchian(20), volume, amplitude. But this is a decision with real consequences — every indicator not in `I` is invisible to the system.

12. **Do we include multi-timeframe features?** The paper mentions "dynamically updated time windows `W`" but doesn't enumerate. Do we give A_ma 1-min + 5-min + 15-min + 1-hour, or just one base timeframe?

## Operational and safety

13. **Stopping criterion for the A_be ↔ A_fr loop.** The paper gives no concrete rule. Options: (a) max iterations (e.g., 5), (b) `J(π_Θ)` improvement < ε for K iterations, (c) wall-clock budget, (d) LLM token budget. I recommend (a)+(b)+(d) as an OR-combined hard stop. Your call on the actual values.

14. **Max LLM token budget per round.** This defines cost. The paper's API usage isn't disclosed. Rough envelope: if A_be does 5 iterations with Opus, you're looking at maybe $2–5 per round-pair. Multiply by pairs × rounds.

15. **Who owns `ranges.yml`?** A_fr needs this file to constrain its LP. If A_fr can write to it, A_fr can widen its own constraints — security hole. If a human owns it, the agent is bottlenecked. Recommendation: A_sa proposes initial ranges in its spec, a human promotes them into `ranges.yml`, A_fr reads-only.

16. **Sandbox for `scipy.optimize.linprog` calls.** The paper references "controlled sandboxes" `[source: arxiv-2510.04787-html]` but no detail. On our side: do we trust `Bash` with `uv run python -c "from scipy.optimize import linprog; ..."`, or do we want a separate subprocess with resource limits (memory, CPU time, no network)?

17. **What happens when A_fr and A_be disagree about whether a change is a parameter-level or function-level modification?** Need a tiebreaker. Recommendation: the diff-lint tool decides — if the AST diff only touches `TimiConfig` defaults, it's parameter-level; if it touches any method body, it's function-level.

## Architecture and scope

18. **[ANSWERED 2026-04-09] Per-pair strategies.** Each pair in a round gets its own `B*` — its own `S_p`, `Θ_p`, and its own `strategy.py`. Matches the paper faithfully. File layout: `competition/agent-N-timi/round<N>/<PAIR>/strategy.py` (subdirectory per pair, each a complete self-contained submission with its own `MANIFEST`, `tests/`, `research/`, `README.md`). Implications:
   - **Evaluator change**: `evaluate_round<N>.py` must treat `agent-N-timi` as a multi-submission entry. Either (i) score each `<PAIR>/` as a separate submission and aggregate by agent, or (ii) add a `portfolio_manifest.json` at `round<N>/` level that declares capital split across the pairs. Needs decision in a follow-up task.
   - **Validator change**: `validate_submission.py` currently validates a single directory. Needs either a `--per-pair` mode or a wrapper that walks `<PAIR>/` subdirs and validates each.
   - **A_be impact**: produces N submissions per round (one per pair in `ROUND_CONFIG.pairs`), not one. Loop: for pair in pairs → A_sa spec → A_be bot → A_fr LP → converge.

19. **Portfolio-level coordination across pairs.** The paper is silent on how capital is allocated across concurrently-running bots. Do we need a 5th "portfolio" agent, or does each pair get `1/N_pairs` of capital and call it a day?

20. **Warm-starting A_ma with prior-round reports.** Does A_ma read `docs/research/timi/macro/round<N-1>.md` as context, or start fresh each round? Fresh is safer for hidden-eval leakage; warm is closer to the paper.

21. **How often does the offline A_be ↔ A_fr loop run during a live testnet paper-trading period?** Paper model is "continuous". Ours could be "once per competition round" or "every N hours during paper trading". This matters for cost and for how quickly the system can react to live telemetry.

## Things the paper glosses over that we must handle

22. **The paper reports live ARR of 6.4/8.0/13.7%** but doesn't say whether those are post-fee, post-slippage, or how paper-trading vs real fills were distinguished. We should NOT treat these numbers as a success target — our goal is architectural fidelity, not beating them.

23. **Prompt templates for all four agents are not published.** We have to write them from scratch. Is there a prior-art repo or HuggingFace dataset we should mine?

24. **What does "converged" mean numerically?** The fixed-point notation `B* = A_be(B; A_fr(B, F, Θ))` implies iteration until a fixed point, but no tolerance is given. See Q13.

25. **Fees, slippage, partial fills in backtest.** Nautilus's `BacktestEngine` simulates these if configured. Does A_fr's LP account for them, or treat them as zero? Ignoring them is dangerous for high-turnover strategies.

26. **Short selling exists in the paper's stock/futures experiments.** Our Spot-only constraint means half the paper's risk scenarios (e.g., "position is net short during a bounce") are N/A. We need to filter the LP examples down to long-only variants before translating them.
