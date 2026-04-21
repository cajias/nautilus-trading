# A_be — Bot Evolution Agent

## Role

The bot evolution agent "creates and optimizes programmatic trading bots `B`" `[source: arxiv-2510.04787-html]`. It constructs bots by "decomposing them into three hierarchical layers: strategy, function, and parameter" `[source: arxiv-2510.04787-html]`. Its code generation `ψ` is governed by the three programming laws `L`: functional cohesion, unidirectional dependency, parameter externalization `[source: arxiv-2510.04787-html]`. In the TiMi paper it is a Code LLM (they use Qwen2.5-Coder-32B-Instruct); on our stack it is a Claude Code subagent with a code-focused system prompt.

## Inputs

- `docs/research/timi/adapted/round<N>__<PAIR>.md` — A_sa's pair-specific spec (when bootstrapping a new bot).
- `docs/research/timi/reflection/round<N>__<PAIR>__<iter>.md` — A_fr's refinement directive (when iterating on an existing bot).
- `competition/TEMPLATE/` — the canonical submission layout.
- `competition/COMPETITION.md` — the R11+ contract.
- Existing reference ports like `strategies/crypto/hybrid_sma_r10.py` as code patterns.
- The `crypto-competition` skill which encodes our submission conventions.

## Outputs

Writes one full competition submission **per pair** (per Q18, answered 2026-04-09) under `competition/agent-N-timi/round<N>/<PAIR>/`:
- `strategy.py` — `TimiStrategy<PAIR>` + `TimiConfig<PAIR>` + module-level `MANIFEST` with all six required keys, `instrument_id` and `bar_type` scoped to that pair.
- `tests/__init__.py` and `tests/test_strategy.py` — pytest-runnable behavioural tests for that pair's strategy.
- `research/notes.md` — rationale, parameter provenance (copied from A_sa spec for that pair), any A_fr refinement history.
- `README.md` — 1-paragraph summary + MANIFEST values.

A_be is invoked once per pair in `ROUND_CONFIG.pairs`. The existing `crypto-competition` skill and `validate_submission.py` treat each `<PAIR>/` subdir as a complete standalone submission — no changes required to those tools. The evaluator needs a per-pair mode (tracked as a follow-up to Q18).

Optionally edits function-layer helpers in a designated `competition/agent-N-timi/round<N>/<PAIR>/_helpers.py` (per-pair, not shared across pairs in the same round — keeps each pair's `B*` fully isolated). NEVER edits production `strategies/crypto/*.py` — those are shared library code, not A_be's turf.

## Claude Code agent type

`general-purpose` subagent, launched under the name `timi-bot-engineer`, with a code-generation-focused system prompt.

**System prompt must emphasize:**
- "You are subject to three programming laws, enforced by our validator:
  1. **Functional cohesion** — each function, one responsibility. No god-functions.
  2. **Unidirectional dependency** — strategy layer imports function layer imports parameter layer, never the reverse.
  3. **Parameter externalization** — every tunable number lives in `TimiConfig`. Zero magic numbers in `TimiStrategy` method bodies."
- "The strategy you emit must be runnable unchanged in BOTH `BacktestEngine` AND a Binance Spot testnet `TradingNode`. You must never `import binance` or `import ccxt` directly — all venue wiring is in `runner.py`, not your file."
- "Spot-only. Long-only or rebalance-to-zero. No `short`, `margin`, `borrow`, `leverage`, or `futures` keywords anywhere. The validator will reject these."
- "Money is `Decimal` only. Prices must already be tick-aligned before you pass them to `order_factory`; never call `round()` on a `Price`. Use `instrument.make_price(x)`."
- "You MUST run `python competition/validate_submission.py <your_dir>` and fix any failures before declaring done. Failing validation is not a soft error."
- "You MUST run `make test` and fix any failures."
- "You work via the `crypto-competition` skill, not by hand-editing paths."

## Required tools

- `Read`, `Write`, `Edit` — under `competition/agent-N-timi/round<N>/` only.
- `Bash` — specifically:
  - `python competition/validate_submission.py <dir>`
  - `cd nautilus && uv run pytest tests/test_strategy.py -x`
  - `make lint`
  - `cd nautilus && uv run nt backtest --strategy ...`
- `Grep`, `Glob` — for code reference.
- `Skill` — invoke `crypto-competition` skill for repo-convention compliance.

**Denied:** writes to `strategies/crypto/` (shared library, not A_be's turf), `nautilus/` (framework), `competition/TEMPLATE/` (reference only), `competition/evaluate_*.py` (evaluator is not the agent's business).

## Invocation trigger

Two modes:

1. **Bootstrap mode** — when `docs/research/timi/adapted/round<N>__<PAIR>.md` exists but `competition/agent-N-timi/round<N>/strategy.py` does not. A_be creates the submission from scratch.

2. **Refinement mode** — when A_fr has written a directive file and the existing submission needs to be updated. The directive specifies which layer is affected:
   - **parameter**: A_be edits `TimiConfig` defaults (or `research/notes.md` justification) and re-runs validator.
   - **function**: A_be substitutes a helper from the allowlist (`docs/research/timi/function_swaps.md`) and re-runs validator + tests.
   - **strategy**: HOLD. A_be prepares a draft but does NOT commit until a human code-reviewer signs off (see DESIGN.md "Strategy-layer changes").

**Dependency:** A_sa (bootstrap) or A_fr (refinement).

## Failure modes

| Failure | Response |
|---------|----------|
| `validate_submission.py` fails with a 3-laws violation | A_be must fix and re-run; no escape hatch. If it cannot fix within N iterations, emit `needs_rework: true` and hand back to A_fr. |
| `make test` fails | Same: fix it or escalate. |
| Tick-size mismatch (Binance instrument cache not loaded) | A_be must call `instrument.make_price(x)` rather than `Price.from_str(...)`. This is a known landmine (see memory: `project_live_trading_gotchas`). |
| Agent tries to import a venue-specific module | Validator catches this; A_be must rewrite using generic types only. |
| Agent writes outside its allowed directory | Hook-level block; agent cannot recover, orchestrator restarts it with a sharper system prompt. |
| LLM drifts and produces a "friendlier" single-layer strategy | Validator enforces the layered shape; drift is caught. |
| Parameter-only refinement requested but agent restructures the whole file | Forbidden. A_fr directives that are `layer: parameter` must result in a diff whose AST touches only `TimiConfig` field defaults. Enforced by a diff-lint step. |

## Testing approach

**Fixture replay tests** in `tests/test_timi_bot_engineer.py`:
- Given a fixed A_sa spec fixture + a stub validator, assert A_be produces a directory tree that matches a gold snapshot (for a trivial EMA-cross strategy).
- Assert `strategy.py` passes `ast.parse` and has a valid `MANIFEST`.
- Assert the generated `strategy.py` imports nothing from `binance`, `ccxt`, or `nautilus_trader.adapters`.
- Smoke-test: the generated strategy can be instantiated against a `BarType`-only fixture without raising.

**End-to-end:** run A_be on a fixture spec, then pipe the result into the existing `competition/validate_submission.py` → must exit 0.

**Refinement test:** given an existing `strategy.py` and an A_fr directive "reduce `stop_loss_pct` from 2.0 to 1.5", assert the diff touches only one line in `TimiConfig`.
