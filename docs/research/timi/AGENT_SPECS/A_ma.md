# A_ma — Macro Analysis Agent

## Role

The macro analysis agent "identifies macro-level market patterns and formulates general trading strategies `S` based on technical indicators" `[source: arxiv-2510.04787-html]`. Initialized through a definition of technical indicators `I`, it captures the observable state space across time scales `W` and emits a strategy set `S` oriented toward "patterns demonstrating statistical significance" `[source: arxiv-2510.04787-html]`. Crucially, it operates on **technical indicators**, not news or sentiment — the paper frames peripheral information as an anti-pattern of prior LLM traders.

## Inputs

- `catalog/*.parquet` — Binance klines for the TRAIN window of the current competition round.
- A fixed indicator universe `I` (to be defined in `docs/research/timi/indicator_universe.md`, future task). Initial candidates: EMA, SMA, RSI, ATR, ADX, Bollinger bands, VWAP, Donchian channels, volume, amplitude.
- Time-window grid `W`: e.g., 1-min / 5-min / 15-min / 1-hour bars.
- Prior round's regime diagnosis if one exists (`docs/research/timi/macro/round<N-1>.md`).

## Outputs

A single markdown file per round: `docs/research/timi/macro/round<N>.md`. It must contain:
- A regime classification (trend / mean-reverting / choppy / volatile breakout) with evidence (indicator values, chart snapshots if available).
- A general strategy set `S` — typically 3–6 candidate templates drawn from the indicator universe (e.g., "EMA fast/slow cross", "RSI mean reversion", "Donchian breakout with ATR stop").
- For each template: a one-sentence rationale tied to the regime and a list of tunable parameters.

**It does NOT write code.** It produces specs for A_sa to customize.

## Claude Code agent type

`general-purpose` subagent, launched under the name `timi-macro-analyst`.

**System prompt must emphasize:**
- "Use only technical indicators. Do not ingest news, social media, or sentiment."
- "Your output is markdown; you do not write Python."
- "Prefer statistical claims with numeric evidence (e.g., 'RSI median is 42 over the TRAIN window, mean-reverting regime') over narrative."
- "Time-window sensitivity matters — state which `W` each claim applies to."
- "You are one of four TiMi agents. Your downstream consumer is A_sa, not a human."

## Required tools

- `Read` — parquet catalog, existing strategy files.
- `Bash` — `cd nautilus && uv run python -c "..."` for computing indicator stats over klines. Limited to a narrow allowlist of python one-liners; no writes outside `docs/research/timi/macro/`.
- `Grep`, `Glob` — navigating `strategies/crypto/` for existing indicator patterns.
- `ctx_search` (context-mode MCP) — for looking up the paper or reference material if needed.
- `Write`, `Edit` — ONLY inside `docs/research/timi/macro/`.

**Denied:** `Edit` on `competition/`, `strategies/`, or `nautilus/`. No `WebFetch`. No `Bash` with unrestricted shell.

## Invocation trigger

- **Manual:** `timi run macro --round N` (future CLI; for now, an orchestrator-dispatched task).
- **Automatic:** when a new `roundN_config.py` lands in `competition/` and no `docs/research/timi/macro/round<N>.md` yet exists.
- **Dependency:** none — this agent runs first in the pipeline.

## Failure modes

| Failure | Response |
|---------|----------|
| Parquet catalog missing for the TRAIN window | Agent aborts, emits a `needs_data: true` flag. Orchestrator triggers `nt download`. |
| Indicator values are degenerate (NaN, zero variance) | Agent must report the degenerate indicators explicitly rather than silently drop them. No downstream agent should receive a silent "use all indicators" signal. |
| Regime is ambiguous (no clear trend or mean-reversion) | Output `regime: mixed` with a conservative strategy set (e.g., "small-size grid around VWAP"). |
| Agent hallucinates a strategy template that does not correspond to an existing indicator in `I` | Caught by a downstream lint: A_sa must reject any template whose indicator is not in `I`. |
| LLM cost budget exceeded | Hard-kill after N tokens; emit partial output with a `truncated: true` flag. |

## Testing approach

**Pure-logic tests** against a fixture TRAIN window:
- Give the agent a known-trending synthetic series (e.g., `price = 100 * 1.001**t`). Assert the regime classification is `trend`.
- Give it a known mean-reverting series (OU process). Assert regime is `mean-reverting`.
- Give it high-vol random walk. Assert regime is `volatile` or `mixed`, not `trend`.
- Replay a prior real round (e.g., round 11) and check the agent's macro report against a human-curated gold file in `tests/fixtures/timi/macro_gold_round11.md`.

These tests run without invoking downstream agents — A_ma is the cleanest agent to unit-test in isolation because it has no side effects outside its output markdown file.
