# TiMi — Paper Summary

> arxiv 2510.04787 — "Trade in Minutes! Rationality-Driven Agentic System for Quantitative Financial Trading"
> Zifan Song et al., Tongji University / Microsoft Research Asia / Bristol / Fudan.
> All citations below refer to `[source: arxiv-2510.04787-html]`.

## TLDR

TiMi is a four-agent LLM system that **decouples strategy reasoning from execution**. Three offline "policy + optimization" agents build and iteratively refine Python trading bots against historical / simulation data; a fourth "reflection" agent closes the feedback loop by converting observed risk events into linear-programming constraints on tunable parameters. The claim: by doing all the heavy LLM thinking before market hours and shipping a plain CPU-only Python bot for live execution, TiMi gets both agentic flexibility and quantitative-grade latency. They report live ARR of 6.4% on stock index futures, 8.0% on mainstream crypto, 13.7% on altcoins, with Sharpe 1.27 on altcoins `[source: arxiv-2510.04787-html]`.

## The problem TiMi attacks

Prior LLM trading agents "predominantly simulate anthropomorphic roles that inadvertently introduce emotional biases and rely on peripheral information, while being constrained by the necessity for continuous inference during deployment" `[source: arxiv-2510.04787-html]`. Three concrete failings:

1. **Anthropomorphic simulation** — "trader personas" hallucinate feelings and rely on news/sentiment that post-hoc rationalizes moves, not predicts them.
2. **Continuous inference at deployment** — keeping an LLM in the hot path means your trade latency is whatever Sonnet/GPT wants it to be that second. Unacceptable for minute-level crypto.
3. **Monolithic rule-based classical strategies** — stable in-regime, but "struggle to adapt to complex dynamics such as non-linear fluctuations and black swan events" `[source: arxiv-2510.04787-html]`.

TiMi's thesis: harmonize "strategic depth in agents with the mechanical rationality essential for quantitative trading" by using LLMs where they shine (semantic analysis, code generation, math reasoning) and shipping pure Python at runtime.

## The four agents

The paper formally denotes agents `A_ma, A_sa, A_be, A_fr` and gives each exactly one cognitive job:

| Agent | Role (paper's definition) | Core LLM capability |
|-------|---------------------------|---------------------|
| **A_ma — macro analysis agent** | "Identifies macro-level market patterns and formulates general trading strategies `S` based on technical indicators" `[source: arxiv-2510.04787-html]` | Semantic analysis |
| **A_sa — strategy adaptation agent** | "Customizes macro strategies `S` into pair-specific rules `S_P` with initialized parameters `Θ_P` by analyzing characteristics of trading pairs `P`" `[source: arxiv-2510.04787-html]` | Semantic analysis + math reasoning |
| **A_be — bot evolution agent** | "Creates and optimizes programmatic trading bots `B`" from pair-specific strategies `[source: arxiv-2510.04787-html]` | Code programming (a Code LLM) |
| **A_fr — feedback reflection agent** | Deconstructs feedback `F` from simulations and "formulates precise optimization plans, which are then transmitted to `A_be` for programmatic refinement" `[source: arxiv-2510.04787-html]` | Mathematical reasoning |

**Critical correction to our starter assumptions:** `A_ma` is NOT a news/sentiment agent. The paper is explicit that macro analysis is **technical-indicator-driven**: "we utilize objective technical indicators of target pairs (e.g., volume and amplitude) with dynamically updated time windows" `[source: arxiv-2510.04787-html]`. Sentiment is explicitly what TiMi is fleeing from — it calls anthropomorphic systems "emotional biases".

### Concrete LLM assignments (from Section 4.1)

"We strategically adopt DeepSeek-V3 for semantic analysis, Qwen2.5-Coder-32B-Instruct for code programming, and DeepSeek-R1 for mathematical reasoning" `[source: arxiv-2510.04787-html]`. So `A_ma` + `A_sa` → DeepSeek-V3; `A_be` → Qwen2.5-Coder; `A_fr` → DeepSeek-R1. A hybrid local/API inference stack is used to keep costs down.

## Policy → Optimization → Deployment chain

The entire system is formalized as a tuple `(M, W, S, F, J)`: market, time window, strategy space, feedback signals, evaluation functions, with three maps `[source: arxiv-2510.04787-html]`:

1. **Analysis**: `M × W → S` (pattern → strategies)
2. **Deployment**: `M × S → F` (strategies → fills, PnL, risk events)
3. **Optimization**: `S × F → S*` (refined strategies)

These are wired into three temporal stages:

- **Policy stage (offline)** — `A_ma`, `A_sa`, `A_be` cooperate to produce a prototype bot `B` with initial parameters `Θ`. Complex reasoning happens here.
- **Optimization stage (offline)** — `B` runs in historical / simulated markets. Feedback `F` includes "technical execution traceback and risk corner cases". `A_fr` analyzes it, `A_be` applies refinements. The loop produces an advanced bot: `B* = A_be(B; A_fr(B, F, Θ))`  `[source: arxiv-2510.04787-html]`.
- **Deployment stage (online)** — the refined bot `B*` runs on CPU against the exchange API. No LLM in the hot path. Live trading needs nothing but Python + `ccxt`-style connectors.

## Layered bot abstraction

`A_be` is required to emit bots that decompose into three layers `[source: arxiv-2510.04787-html]`:

- **Strategy layer** — "decision-making logic derived from `S_p`, including signal generation, position sizing, and entry/exit criteria".
- **Function layer** — "computational mechanisms required by the strategy, implementing technical indicators, data preprocessing, and order execution routines that are reusable across different strategies".
- **Parameter layer** — "adjustable parameters that fine-tune the behavior of the trading strategy and its functions".

The parameter layer is the feedback loop's target; higher layers get touched only when parameter tweaks are insufficient.

## The three programming laws `L`

Quoted verbatim from Section 2.4 `[source: arxiv-2510.04787-html]`:

1. **Functional cohesion law** — "each functional component must address exactly one responsibility".
2. **Unidirectional dependency law** — "dependencies flow strictly from higher to lower layers". (Strategy may import Function and Parameter; Function may import Parameter; NEVER the reverse.)
3. **Parameter externalization law** — "all adjustable values must be extracted from implementation code and centrally managed". No magic numbers in strategy or function layers — everything tunable lives in a dataclass / config object.

These laws exist so `A_be` can make surgical edits (swap one function, bump one parameter) without cascading changes. They make the system mutation-friendly.

## Hierarchical optimization and the "minimal intervention" principle

When `A_fr` proposes a fix, it escalates as follows `[source: arxiv-2510.04787-html]`:

1. **Parameter-level** (default) — tune numbers inside existing constraints via LP. No code changes.
2. **Function-level** (escalation 1) — substitute algorithmic components (e.g., swap EMA for a Kalman filter) when parameter tweaks are insufficient (e.g., failing risk simulations).
3. **Strategy-level** (escalation 2) — structural modifications to decision rules encoded in `S_p`. Only if the function-level fix still doesn't meet thresholds.

The principle: "prioritizing lower-level adjustments that preserve strategic continuity" `[source: arxiv-2510.04787-html]`. In practice this means the loop keeps ~90% of its changes in the parameter layer, which is the cheapest to test and the hardest to break.

## Linear programming for parameter solving

`A_fr` runs a three-step mathematical procedure `[source: arxiv-2510.04787-html]`:

1. **Organize risk scenarios** from feedback `F` — e.g., observed that order density & size didn't adapt to a volatility spike → identify it as a concrete failure mode.
2. **Transform into a linear programming problem** — e.g., for a position-size case, impose `Σ Q_i ≤ Q_max` as a constraint on order-quantity distribution, where `Q_i = A × M_Q[i] × c_m × c_f` and `A` is allocated capital.
3. **Solve within constraints** to maximize objective (return / utility) — the LP solver gives new parameter values.

**The objective and constraints are specific to each risk scenario** — the paper shows three worked examples in Appendix A (position-size control under volatility, ATR-based stop tightening, and trend-adaptive profit taking). The LP is not one fixed form; it's whatever `A_fr` formulates given the observed pathology. The value is that you get provably-feasible parameter updates instead of LLM guesses.

## Results claimed

Backtest (2024 historical data) and live trading across **200+ trading pairs** covering U.S. stock index futures, mainstream cryptocurrencies, and altcoins `[source: arxiv-2510.04787-html]`.

- **Live ARR**: 6.4% (stock index futures), 8.0% (mainstream crypto), 13.7% (altcoin) `[source: arxiv-2510.04787-html]`.
- **Sharpe**: notably 1.27 in Altcoins backtests `[source: arxiv-2510.04787-html]`.
- **Profit/loss ratio per unit invested capital**: 1.53 vs Grid 1.22 vs TradingAgents 1.32.
- **Action latency**: on par with classical quant methods — since no LLM runs at inference time, the only latency source is exchange RTT `[source: arxiv-2510.04787-html]`.
- **Capital utilization rate** (deployed / available): "clear advantages among learning-based approaches".

Ablation (Appendix C): removing `A_sa` still runs, but risk-adjusted returns drop and variance across pairs increases — its contribution is primarily stability, not raw ARR. `A_ma` and `A_be` cannot be ablated because the system is non-operational without them `[source: arxiv-2510.04787-html]`.

## What the paper does NOT say

- **Exact macro-indicator universe.** The paper cites "volume and amplitude" as examples; it never enumerates the indicator set `I`. We would have to pick our own.
- **News-source feed, if any.** The paper says `A_ma` uses technical indicators and explicitly frames "peripheral information" (news/sentiment) as the failure mode of prior work. It does not specify any news ingestion pipeline. (Our current assumption that `A_ma` consumes news is **wrong** per the paper.)
- **Prompt templates** for any agent. XML envelopes with JSON payloads are described as the communication protocol, but the actual prompt texts are not published.
- **Sandbox implementation.** It says "controlled sandboxes" with posterior checks before deployment — no detail on how scripts are isolated, which runtime, resource limits.
- **How many optimization iterations** the loop runs before declaring convergence. The paper shows the fixed-point recursion `B* = A_be(B; A_fr(B, F, Θ))` but does not state a stopping criterion.
- **Paper trading vs real money.** The live trading results are described as "live trading comparison" without explicitly stating whether orders were filled in a sandbox or with real capital. The reported ARR implies either is plausible.
- **Cost of the system.** LLM API spend, sandbox compute, data fees — none disclosed.
- **Failure stories.** The paper is a success narrative; no discussion of strategies that `A_fr` couldn't rescue or optimization cycles that diverged.
- **Portfolio-level coordination.** Each bot appears to be pair-specific (`S_p` and `Θ_p` per pair `p ∈ P`). How capital is allocated across pairs at the portfolio level is not described.
- **Warm-start vs cold-start per round.** Does a new market regime throw out all prior `B*` or do they evolve continuously? Unclear.
- **Short selling / leverage.** The LP examples include position-size constraints that look long-only, but the system is applied to futures markets, so shorting is clearly in scope for the paper. Not something we can inherit on Binance Spot testnet.
