# A_sa — Strategy Adaptation Agent

## Role

The strategy adaptation agent "customizes macro strategies `S` into pair-specific rules `S_P` with initialized parameters `Θ_P` by analyzing characteristics of trading pairs `P`" `[source: arxiv-2510.04787-html]`. It employs a two-step process: semantic analysis `ϕ(S, p) → S_p` to select and adapt strategies from the general set, then mathematical reasoning `γ(S_p, p) → Θ_p` to optimize initial parameters `[source: arxiv-2510.04787-html]`. The adaptation includes "strategy prioritization based on historical performance, parameter calibration tailored to pair-specific volatility profiles, and adaptive risk management rules that account for critical factors such as market liquidity."

## Inputs

- `docs/research/timi/macro/round<N>.md` — A_ma's output for the current round.
- `catalog/*.parquet` — per-pair statistics: realized volatility, mean amplitude, typical volume, bid-ask spread profile where available.
- A list of in-round pairs from `competition/roundN_config.py` (for R11+) or `COMPETITION.md`.
- `competition/TEMPLATE/` as the file-shape reference.
- The existing `strategies/crypto/*.py` library as a "what has worked before" reference.

## Outputs

One markdown file per (round, pair) combination: `docs/research/timi/adapted/round<N>__<PAIR>.md`. Each file must contain:
- Which template from A_ma's `S` was selected and why (one-sentence semantic rationale).
- Initial parameter values `Θ_p` with provenance — "ATR(14) median over TRAIN is 1.2%, so stop-loss starts at 1.5 * ATR".
- Risk management parameters tailored to pair characteristics (max position size as fraction of allocated capital, max concurrent positions, cooldown after loss).
- An explicit liquidity note ("BNBUSDT average volume / hour on testnet is X; limit order sizes to Y fraction of this") — market liquidity is called out in the paper as a critical factor.

**It does NOT write code.** The output is a structured spec that A_be will translate into Python.

## Claude Code agent type

`general-purpose` subagent, launched under the name `timi-strategy-adapter`.

**System prompt must emphasize:**
- "Your job is pair-specific customization. Do not re-derive macro strategies; use exactly what A_ma gave you."
- "Every parameter you choose must be defensible from the pair's historical statistics. No magic numbers."
- "Use mathematical reasoning for parameter initialization — volatility-aware stops, liquidity-aware sizing."
- "You produce markdown, not Python."
- "If A_ma's macro set is insufficient for a given pair (e.g., all templates fail a sanity check), flag the pair as `needs_rework` and do not invent a new template."

## Required tools

- `Read` — macro output, parquet catalog, strategy templates.
- `Bash` — `uv run python -c "import pandas as pd; ..."` for per-pair stats. Same narrow allowlist as A_ma.
- `Grep`, `Glob` — discovering existing strategy patterns.
- `Write`, `Edit` — ONLY inside `docs/research/timi/adapted/`.

**Denied:** writes to `competition/`, `strategies/`, `nautilus/`, or any directory outside `docs/research/timi/adapted/`.

## Invocation trigger

- **Dependency:** `docs/research/timi/macro/round<N>.md` must exist and contain a non-empty strategy set.
- **Orchestrator:** dispatched after A_ma completes successfully for the round.
- **Per-pair parallelism:** the orchestrator should fan-out per pair; each sub-invocation handles exactly one `(round, pair)` tuple.

## Failure modes

| Failure | Response |
|---------|----------|
| A_ma's output is missing or empty | Abort with `needs_macro: true`; orchestrator re-runs A_ma. |
| Pair has insufficient history in catalog | Emit `needs_data: true`; orchestrator runs `nt download`. |
| Agent picks a template with a parameter it cannot initialize (e.g., no volatility data → no ATR) | Fallback to the template's paper default and flag `confidence: low`. |
| Two adapted specs conflict (same pair, different rounds) | Non-issue — files are keyed by `round__pair` to avoid overwrites. |
| Liquidity on testnet is too low for any reasonable sizing | Flag `needs_rework: true` with a liquidity-floor estimate; orchestrator may drop the pair from the round. |

## Testing approach

**Fixture-based tests** in `tests/fixtures/timi/sa/`:
- For a fixed A_ma output + a fixed parquet stats snapshot, assert the A_sa output matches a gold spec byte-for-byte (or within tolerance for floats).
- Test per-pair heterogeneity: two different pairs against the same macro set should produce different `Θ_p`.
- Test graceful failure: empty A_ma output → exit code indicating `needs_macro`.
- Snapshot-test the output markdown structure so regressions in formatting are caught.

A_sa is **semi-isolatable** — it depends on A_ma's output but nothing downstream. Its test suite lives upstream of A_be's.
