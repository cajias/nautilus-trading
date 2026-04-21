# TiMi Refinement Directive Format

> Machine-readable protocol for handoffs from `A_fr` (feedback reflection
> agent) to `A_be` (bot evolution agent). This file is the contract both
> agents must respect.
>
> Cross-links: [`AGENT_SPECS/A_fr.md`](./AGENT_SPECS/A_fr.md) (producer),
> [`AGENT_SPECS/A_be.md`](./AGENT_SPECS/A_be.md) (consumer),
> [`DESIGN.md`](./DESIGN.md) §"Evolution loop" and §"Safety rails".
>
> **Scope reminder:** Spot / long-only only (see `DESIGN.md` §Scope). All
> LP examples below are rewritten from the paper's futures examples to
> long-only spot equivalents.

## Filename convention

One directive per pair per iteration (per `OPEN_QUESTIONS.md` Q18, ANSWERED
2026-04-09 — per-pair strategies):

```
docs/research/timi/reflection/round<N>__<PAIR>__<iter>.md
```

Example: `docs/research/timi/reflection/round11__BTCUSDT__02.md` — the
second refinement pass on the BTCUSDT submission in round 11.

**Why:** directives are addressable and diffable via plain git; orchestrator
can glob `round11__*__*.md` to replay a full round; pair is first-class in
the path because `A_be` is also invoked per pair.

---

## 1. Envelope

Every directive file opens with a YAML frontmatter block. Fields are
scalar-only — **no YAML anchors or merge keys** (msgspec/pydantic cannot
safely round-trip those).

```yaml
---
round: 11
pair: "BTCUSDT"
iter: 2
layer: parameter          # parameter | function | strategy
verdict: continue         # continue | converged | diverged
author: A_fr
timestamp: "2026-04-09T14:22:31Z"
human_review_required: false
---
```

| Field                   | Type    | Why                                                                                   |
|-------------------------|---------|---------------------------------------------------------------------------------------|
| `round`                 | int     | **Why:** binds the directive to `ROUND_CONFIG`; prevents cross-round leakage (Q7).    |
| `pair`                  | str     | **Why:** per-pair isolation; `A_be` only opens the corresponding `<PAIR>/` subdir.    |
| `iter`                  | int     | **Why:** iteration count inside the A_be↔A_fr loop; used for stop criterion (Q13).    |
| `layer`                 | enum    | **Why:** routes to A_be's parameter/function/strategy handler. Hierarchy is load-bearing. |
| `verdict`               | enum    | **Why:** `continue` hands back to A_be; `converged` promotes; `diverged` halts loop.  |
| `author`                | literal | **Why:** always `A_fr` — A_be refuses anything else. Prevents forged directives.     |
| `timestamp`             | ISO-8601| **Why:** auditability of the offline evolution loop; supports replay.                 |
| `human_review_required` | bool    | **Why:** strategy-layer always `true` (DESIGN.md §Safety rails); else `false`.        |

**Why YAML frontmatter over a separate JSON file?** The LP formulation,
prose rationale, and structured diff all belong in one reviewable artifact.
Git diffs stay coherent; orchestrator reads with `msgspec.yaml.decode` plus
a split on the `---` marker.

---

## 2. Parameter-layer directive (JSON-patch body)

Body is a single fenced JSON code block, each entry a dict. The block is
syntactically a JSON array of operations:

```json
[
  {
    "field": "fast_period",
    "old_value": 12,
    "new_value": 9,
    "justification": "Lagged EMA missed 3 of 5 reversals in TRAIN window; shortening period to 9 tightens response.",
    "lp_solution_ref": "#lp-1"
  },
  {
    "field": "stop_loss_pct",
    "old_value": 0.025,
    "new_value": 0.018,
    "justification": "Observed 14% MDD trade on 2024-08-05 breached LP constraint stop_loss_pct <= 2 * median_ATR_pct.",
    "lp_solution_ref": "#lp-1"
  }
]
```

### Field reference

| Key               | Type         | Purpose                                                                                 |
|-------------------|--------------|-----------------------------------------------------------------------------------------|
| `field`           | dotted path  | Dotted path into `TimiConfig`. Flat fields allowed: `fast_period`, `stop_loss_pct`, etc. |
| `old_value`       | JSON scalar  | Current value in `strategy.py` — audit/anti-race check; if mismatch, A_be refuses.      |
| `new_value`       | JSON scalar  | Proposed value. Must be within `ranges.yml` domain (see §6).                            |
| `justification`   | str          | One sentence English; human review crib. Not parsed by A_be.                            |
| `lp_solution_ref` | str          | Anchor link to an LP block defined later in the same file (e.g. `#lp-1`). Required.    |

### Why JSON-patch over structured-diff?

We considered three formats:

1. **Unified/`diff` text** — ambiguous across line-number shifts; `A_be` would need a fuzzy patcher; re-ordering fields in `TimiConfig` breaks the patch. Rejected.
2. **Structured code-diff (e.g. `{"old_ast": ..., "new_ast": ...}`)** — overkill for what's always a single-field scalar replacement; couples the directive to a specific Python AST version. Rejected.
3. **JSON-patch-style entries (RFC 6902 spirit, not strict)** — addresses fields by logical path; A_be parses one operation at a time; validator can assert the diff only touches `TimiConfig` defaults; trivially testable. **Selected.**

**Why:** parameter-layer is, by definition, a set of scalar mutations to
`TimiConfig` fields. A patch format that speaks in `(field, old, new)`
tuples is the smallest expressive unit that (a) survives `TimiConfig` field
reordering, (b) lets A_be run an old-value sanity check before writing, and
(c) composes cleanly with multi-field updates. This mirrors A_fr's
"diff that touches only `TimiConfig` field defaults" invariant called out
in `A_be.md` §Failure modes.

### Hard rule

A parameter-layer directive that names a `field` not declared in the target
pair's `TimiConfig` is a **schema violation** — A_be emits
`DIRECTIVE_SCHEMA_VIOLATION` and halts (see §6). This is what makes
`TimiConfig` the single source of truth for the allowed mutation surface
(task #54 will enforce "no magic numbers" via AST check; A_be's job is to
edit only defaults).

---

## 3. Function-layer directive

Structured swap, body is a single fenced YAML block (still no anchors):

```yaml
swap:
  from_component: "EMA"
  to_component: "KalmanFilter1D"
  rationale: >
    EMA reacts too slowly during the 2024-08 volatility regime;
    Kalman filter adapts its noise estimate and captured 4/5 tested
    reversals on the TRAIN-equivalent fixture.
  invariants_preserved:
    - "long-only"
    - "tick-aligned prices (no round() on Price)"
    - "single bar type (no multi-timeframe)"
    - "stateless w.r.t. symbols beyond this <PAIR>"
```

### Allowlist requirement

Both `from_component` and `to_component` must appear in
`docs/research/timi/function_swaps.md`.

> **TODO (cross-link):** `function_swaps.md` does not yet exist. It is
> owned by a future task tracked against `OPEN_QUESTIONS.md` Q6
> ("What is the allowlist for function-layer swaps?"). Until that file
> lands, A_be must refuse **all** function-layer directives with
> `DIRECTIVE_ALLOWLIST_MISSING`. Parameter- and strategy-layer directives
> are unaffected.

**Why a swap table and not free-form code?** Function layer changes have
to preserve the 3 laws (cohesion, unidirectional deps, externalization);
a closed allowlist means the code-reviewer gate (DESIGN.md §Safety rails)
can be a check-the-list operation rather than a full code review. A_fr
cannot invent swaps the system has not pre-vetted.

**Why `invariants_preserved` is required:** it forces A_fr to think about
what breaks. If A_fr cannot name the invariants that survive the swap,
the swap is probably a strategy-layer change in disguise — escalate.

---

## 4. Strategy-layer directive

Markdown prose plus a structured `preamble:` block. Because strategy-layer
requires human review (DESIGN.md §"Strategy-layer changes"), **this directive
is the INPUT to that review, not the actual rewrite**. A_be's job on a
strategy-layer directive is to draft a branch and HOLD — never commit.

```yaml
preamble:
  current_strategy_family: "trend-following / EMA-cross"
  proposed_strategy_family: "mean-reversion / Bollinger-band-pullback"
  rationale_summary: "Trend-following lost 8.4% on BTCUSDT during chop regime; mean-reversion hypothesis fits TRAIN distribution better."
  sections_to_rewrite:
    - "signal generation (on_bar entry logic)"
    - "exit rule (take-profit at midband, stop at 2-sigma)"
    - "position sizing (fixed 0.5 × allocated capital)"
  unchanged:
    - "MANIFEST keys (instrument, bar type stay on 1-hour LAST)"
    - "long-only spot constraint"
    - "Decimal money, tick-aligned prices"
```

Below the `preamble:` block, A_fr writes 1–3 short markdown sections:

- **Risk evidence** — which backtest rows, which drawdown, which trades.
- **Hypothesis** — what class of strategy the evidence implies.
- **Draft acceptance criteria** — what the human reviewer would need to see to approve.

The envelope must carry `human_review_required: true` and
`layer: strategy`. A_be reads the draft, prepares (but does not commit)
a branch under `competition/agent-N-timi/round<N>/<PAIR>/_draft_strategy.py`,
and emits a `STRATEGY_DRAFT_READY` signal for the orchestrator to ping a
human reviewer.

**Why prose + structured preamble, not pure prose?** The `preamble:` gives
the orchestrator and the reviewer a one-glance summary of what's at stake
before they read the explanation; the prose is where mathematical reasoning
lives that does not compress into YAML. Hybrid shape mirrors PR description
conventions.

---

## 5. LP block format

Every parameter-layer entry carries an `lp_solution_ref` pointing at an LP
block defined later in the same file. LP blocks are anchor-linked fenced
YAML (schema notation, not Python code):

```yaml
lp:
  id: "lp-1"
  description: "Tighten stop_loss_pct to bound worst-case MDD."
  variables:
    - name: "stop_loss_pct"
      type: "float"
      domain: "[0.005, 0.05]"   # from ranges.yml
    - name: "fast_period"
      type: "int"
      domain: "[5, 50]"
  objective:
    sense: "minimize"
    expression: "max_drawdown_estimate(stop_loss_pct, fast_period)"
  constraints:
    - "stop_loss_pct <= 2 * median_ATR_pct"      # median_ATR_pct = 0.009 on TRAIN
    - "stop_loss_pct >= 0.005"                    # ranges.yml lower bound
    - "fast_period >= 5"                          # ranges.yml lower bound
    - "fast_period <= slow_period - 2"            # strategy invariant: fast < slow
  solver: "scipy.optimize.linprog"
  solver_method: "highs"
  solution:
    stop_loss_pct: 0.018
    fast_period: 9
  feasibility: "feasible"         # feasible | infeasible | unbounded
  objective_value: 0.061          # estimated max drawdown at the solution
```

**Why YAML dict schema and not Python code?** Avoids exec risk, keeps A_fr
LLM-generable without a code parser, stays diffable in git. The keys above
are also the minimum inputs `scipy.optimize.linprog` needs.

**Why an `id:` anchor inside the body (not a YAML anchor)?** YAML anchors
can't round-trip safely through msgspec/pydantic; a string id keyed by the
block is the safe alternative. Parameter-layer entries reference it with
a markdown fragment (`#lp-1`).

### Worked example 1 — "stop-too-loose" (long-only rewrite)

> Paper scenario (futures): one trade took 14% MDD during a volatility
> spike; shorts failed to cover. We rewrite to long-only spot: the bot
> held a long position through a drawdown and the stop was too wide.

Risk identification: single trade `trade_id=87` on 2024-08-05 took 14% drawdown between entry and stop-out. Median ATR% on TRAIN window is 0.9%. Stop was 2.5% — ratio 2.78× median ATR. LP constraint: `stop_loss_pct <= 2 * median_ATR_pct`. See `lp-1` above. Solution: `stop_loss_pct = 0.018`, `fast_period = 9` (co-moved by the solver). This is `#lp-1`.

### Worked example 2 — "position-too-large" under volatility (long-only)

> Paper scenario: position size didn't adapt to volatility spike. On spot
> long-only this becomes: the bot sized to 100% of allocated capital into
> a high-volatility bar and ate the next bar's drawdown.

```yaml
lp:
  id: "lp-2"
  description: "Constrain long-exposure fraction by realized volatility percentile."
  variables:
    - name: "position_size_pct"
      type: "float"
      domain: "[0.1, 1.0]"
    - name: "vol_scale_k"
      type: "float"
      domain: "[0.0, 2.0]"
  objective:
    sense: "maximize"
    expression: "expected_return(position_size_pct, vol_scale_k)"
  constraints:
    - "position_size_pct * vol_percentile_90 <= 0.30"   # no more than 30% exposure at p90 vol
    - "position_size_pct <= 1.0"                        # spot long-only clamp (no leverage)
    - "vol_scale_k * stop_loss_pct <= 0.05"             # stop budget
  solver: "scipy.optimize.linprog"
  solver_method: "highs"
  solution:
    position_size_pct: 0.45
    vol_scale_k: 1.2
  feasibility: "feasible"
  objective_value: 0.082
```

Note: the paper's `λ > 1` leverage term is absent here — clamped to
`λ ∈ (0, 1]` by spot-only scope (DESIGN.md §Scope).

### Worked example 3 — "trend-reversal drawdown" caught by a late exit

> Paper scenario: trend flipped and profit-take triggered too late.
> On our long-only spot bot: a winning long position round-tripped because
> the `exit_lag` (bars held after signal flips) was too high.

```yaml
lp:
  id: "lp-3"
  description: "Tighten exit_lag so the bot releases winners before mean-revert."
  variables:
    - name: "exit_lag"
      type: "int"
      domain: "[0, 10]"
    - name: "profit_take_pct"
      type: "float"
      domain: "[0.005, 0.10]"
  objective:
    sense: "maximize"
    expression: "realized_pnl_after_exit_cost(exit_lag, profit_take_pct)"
  constraints:
    - "exit_lag <= 2"                                   # observed: lag 4 round-tripped
    - "profit_take_pct >= 0.01"                          # below 1% gets eaten by fees
    - "profit_take_pct <= 3 * median_ATR_pct"            # symmetric with stop
  solver: "scipy.optimize.linprog"
  solver_method: "highs"
  solution:
    exit_lag: 2
    profit_take_pct: 0.022
  feasibility: "feasible"
  objective_value: 0.053
```

---

## 6. Parsing rules for A_be

When A_be opens a directive file it runs this algorithm. **Any deviation
halts with `DIRECTIVE_SCHEMA_VIOLATION`** — A_be refuses, does not guess,
does not clip.

```
1. Read frontmatter block between the two `---` markers.
   If missing, emit DIRECTIVE_SCHEMA_VIOLATION. Halt.
2. Validate against directive.schema.json (§7).
   If fails, emit DIRECTIVE_SCHEMA_VIOLATION. Halt.
3. Assert envelope.author == "A_fr".
   If not, emit DIRECTIVE_SCHEMA_VIOLATION. Halt.
4. Load ranges.yml (see TODO below).
5. Dispatch on envelope.layer:

   layer == "parameter":
     a. Parse the JSON body block as a list of patch entries.
     b. For each entry:
        - Assert `field` exists in the target pair's TimiConfig
          (use importlib + inspect on the <PAIR>/ submission).
          Missing -> DIRECTIVE_SCHEMA_VIOLATION. Halt.
        - Assert current TimiConfig default matches `old_value`.
          Mismatch -> DIRECTIVE_STALE. Halt. (Prevents races.)
        - Assert `new_value` is within ranges.yml[field] domain.
          Out of range -> DIRECTIVE_OUT_OF_RANGE. Halt.
        - Assert `lp_solution_ref` resolves to a present LP block whose
          `feasibility == "feasible"`.
          Missing / infeasible -> DIRECTIVE_SCHEMA_VIOLATION. Halt.
     c. For each entry, assign the new default to TimiConfig.
     d. Run `python competition/validate_submission.py <PAIR>/`.
     e. Run `cd nautilus && uv run pytest tests/test_strategy.py -x`.
     f. Run TRAIN backtest; write artifact directory.
     g. Emit BACKTEST_COMPLETE for orchestrator; return.

   layer == "function":
     a. Load allowlist from docs/research/timi/function_swaps.md.
        Missing file -> DIRECTIVE_ALLOWLIST_MISSING. Halt.
     b. Assert (from_component, to_component) is a permitted pair.
        Not in allowlist -> DIRECTIVE_SWAP_DENIED. Halt.
     c. Assert all `invariants_preserved` entries are in a known set
        (the validator's invariant vocabulary).
        Unknown invariant -> DIRECTIVE_SCHEMA_VIOLATION. Halt.
     d. Apply the swap. Run validator + tests + backtest (same as e/f above).
     e. Emit BACKTEST_COMPLETE or FUNCTION_SWAP_FAILED.

   layer == "strategy":
     a. Assert envelope.human_review_required == true.
        If false -> DIRECTIVE_SCHEMA_VIOLATION. Halt.
     b. Parse the preamble block. Copy prose into the <PAIR>/_draft_strategy.md.
     c. Create a branch directory `<PAIR>/_draft_strategy.py` as a sketch
        only. DO NOT validate, DO NOT backtest, DO NOT commit to MANIFEST.
     d. Emit STRATEGY_DRAFT_READY for the orchestrator — human ping.
     e. Return.
```

> **TODO (cross-link):** `ranges.yml` does not yet exist. It is owned by
> a future task tracked against `OPEN_QUESTIONS.md` Q15 ("Who owns
> `ranges.yml`?"). Until that file lands, A_be must refuse **all**
> parameter-layer directives with `DIRECTIVE_RANGES_MISSING`. This is a
> fail-safe, not a nuisance — without `ranges.yml` A_fr has nothing to
> constrain its LP against.

### Refusal behavior (specific)

A_be's refusal is a concrete operation, not a prompt-level "please don't":

1. Write `<PAIR>/reflection_errors/<iter>.md` with the error code and the
   offending directive snippet.
2. Do not touch `<PAIR>/strategy.py`, `<PAIR>/tests/`, or `MANIFEST`.
3. Return a non-zero exit code to the orchestrator.
4. Do NOT attempt a second parse with "relaxed" rules.

**Why fail-closed:** A_fr is trusted for math, not for code safety. The
parser is the boundary and it errs on the side of rejection.

---

## 7. JSON Schema

Formal draft 2020-12 schema for the envelope + per-layer payloads. A_be
validates every incoming directive against this before any other check.

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://nautilus-trading.local/schemas/timi/directive.schema.json",
  "title": "TiMi Refinement Directive",
  "type": "object",
  "required": ["envelope", "layer_payload"],
  "properties": {
    "envelope": {
      "type": "object",
      "required": [
        "round", "pair", "iter", "layer", "verdict",
        "author", "timestamp", "human_review_required"
      ],
      "properties": {
        "round": {"type": "integer", "minimum": 1},
        "pair": {"type": "string", "pattern": "^[A-Z0-9]{3,20}$"},
        "iter": {"type": "integer", "minimum": 0, "maximum": 20},
        "layer": {"enum": ["parameter", "function", "strategy"]},
        "verdict": {"enum": ["continue", "converged", "diverged"]},
        "author": {"const": "A_fr"},
        "timestamp": {"type": "string", "format": "date-time"},
        "human_review_required": {"type": "boolean"}
      },
      "additionalProperties": false
    },
    "layer_payload": {
      "oneOf": [
        {
          "title": "ParameterPayload",
          "type": "object",
          "required": ["patches", "lp_blocks"],
          "properties": {
            "patches": {
              "type": "array",
              "minItems": 1,
              "items": {
                "type": "object",
                "required": [
                  "field", "old_value", "new_value",
                  "justification", "lp_solution_ref"
                ],
                "properties": {
                  "field": {"type": "string", "pattern": "^[a-z_][a-z0-9_.]*$"},
                  "old_value": {"type": ["number", "integer", "string", "boolean"]},
                  "new_value": {"type": ["number", "integer", "string", "boolean"]},
                  "justification": {"type": "string", "minLength": 1, "maxLength": 500},
                  "lp_solution_ref": {"type": "string", "pattern": "^#lp-[0-9]+$"}
                },
                "additionalProperties": false
              }
            },
            "lp_blocks": {
              "type": "array",
              "minItems": 1,
              "items": {"$ref": "#/$defs/lp_block"}
            }
          },
          "additionalProperties": false
        },
        {
          "title": "FunctionPayload",
          "type": "object",
          "required": ["swap"],
          "properties": {
            "swap": {
              "type": "object",
              "required": [
                "from_component", "to_component",
                "rationale", "invariants_preserved"
              ],
              "properties": {
                "from_component": {"type": "string"},
                "to_component": {"type": "string"},
                "rationale": {"type": "string", "minLength": 1},
                "invariants_preserved": {
                  "type": "array",
                  "minItems": 1,
                  "items": {"type": "string"}
                }
              },
              "additionalProperties": false
            }
          },
          "additionalProperties": false
        },
        {
          "title": "StrategyPayload",
          "type": "object",
          "required": ["preamble", "prose"],
          "properties": {
            "preamble": {
              "type": "object",
              "required": [
                "current_strategy_family", "proposed_strategy_family",
                "rationale_summary", "sections_to_rewrite", "unchanged"
              ],
              "properties": {
                "current_strategy_family": {"type": "string"},
                "proposed_strategy_family": {"type": "string"},
                "rationale_summary": {"type": "string"},
                "sections_to_rewrite": {
                  "type": "array", "minItems": 1, "items": {"type": "string"}
                },
                "unchanged": {
                  "type": "array", "minItems": 1, "items": {"type": "string"}
                }
              },
              "additionalProperties": false
            },
            "prose": {"type": "string", "minLength": 1}
          },
          "additionalProperties": false
        }
      ]
    }
  },
  "$defs": {
    "lp_block": {
      "type": "object",
      "required": [
        "id", "description", "variables", "objective",
        "constraints", "solver", "solver_method",
        "solution", "feasibility"
      ],
      "properties": {
        "id": {"type": "string", "pattern": "^lp-[0-9]+$"},
        "description": {"type": "string"},
        "variables": {
          "type": "array", "minItems": 1,
          "items": {
            "type": "object",
            "required": ["name", "type", "domain"],
            "properties": {
              "name": {"type": "string"},
              "type": {"enum": ["float", "int"]},
              "domain": {"type": "string"}
            },
            "additionalProperties": false
          }
        },
        "objective": {
          "type": "object",
          "required": ["sense", "expression"],
          "properties": {
            "sense": {"enum": ["maximize", "minimize"]},
            "expression": {"type": "string"}
          },
          "additionalProperties": false
        },
        "constraints": {
          "type": "array", "minItems": 1, "items": {"type": "string"}
        },
        "solver": {"const": "scipy.optimize.linprog"},
        "solver_method": {"enum": ["highs", "highs-ds", "highs-ipm", "simplex"]},
        "solution": {"type": "object"},
        "feasibility": {"enum": ["feasible", "infeasible", "unbounded"]},
        "objective_value": {"type": "number"}
      },
      "additionalProperties": false
    }
  }
}
```

**Why draft 2020-12 and not something more exotic?** It's the current
stable baseline, `jsonschema>=4` supports it out of the box, and
`oneOf` on `layer_payload` gives us per-layer structural validation
without a discriminator hack.

**Note on encoding:** the markdown file has frontmatter (YAML) for the
envelope and a fenced JSON block for the parameter patches. A_be
synthesizes the `{envelope, layer_payload}` object in memory before
feeding it to the JSON-schema validator — the on-disk file does not
have to match the schema byte-for-byte, only its parse tree does.

---

## 8. Example directives (round-trip fixtures)

Three complete fixtures follow. Each is self-contained: envelope +
body + (for parameter) LP block referenced by `lp_solution_ref`.

### 8.1 Parameter example — EMA fast-period tweak

Filename: `docs/research/timi/reflection/round11__BTCUSDT__02.md`

```yaml
---
round: 11
pair: "BTCUSDT"
iter: 2
layer: parameter
verdict: continue
author: A_fr
timestamp: "2026-04-09T14:22:31Z"
human_review_required: false
---
```

**Risk scenario.** On the TRAIN window (2024-07-01 → 2024-09-30), the
bot's EMA(12,26) cross missed 3 of 5 reversal points; average lag was
4 bars. A single BTCUSDT trade `trade_id=87` on 2024-08-05 took a 14.2%
drawdown from entry to stop — the widest loss in the run. Median ATR
over the window is 0.9%. Stop was set to 2.5% — 2.78× median ATR.

**LP derivation.** See `#lp-1` below. Solution co-moves `fast_period`
and `stop_loss_pct`: the tighter stop requires a more responsive fast
EMA so the strategy still fires into moves, not after them.

Parameter patch:

```json
[
  {
    "field": "fast_period",
    "old_value": 12,
    "new_value": 9,
    "justification": "Co-solution with tighter stop: 9-period EMA cuts average entry lag from 4 bars to 2.",
    "lp_solution_ref": "#lp-1"
  },
  {
    "field": "stop_loss_pct",
    "old_value": 0.025,
    "new_value": 0.018,
    "justification": "Observed 14% MDD on trade 87 breached stop_loss_pct <= 2 * median_ATR_pct.",
    "lp_solution_ref": "#lp-1"
  }
]
```

LP block (anchored as `lp-1`):

```yaml
lp:
  id: "lp-1"
  description: "Tighten stop and shorten fast EMA to bound MDD."
  variables:
    - name: "stop_loss_pct"
      type: "float"
      domain: "[0.005, 0.05]"
    - name: "fast_period"
      type: "int"
      domain: "[5, 50]"
  objective:
    sense: "minimize"
    expression: "max_drawdown_estimate(stop_loss_pct, fast_period)"
  constraints:
    - "stop_loss_pct <= 2 * 0.009"
    - "stop_loss_pct >= 0.005"
    - "fast_period >= 5"
    - "fast_period <= slow_period - 2"
  solver: "scipy.optimize.linprog"
  solver_method: "highs"
  solution:
    stop_loss_pct: 0.018
    fast_period: 9
  feasibility: "feasible"
  objective_value: 0.061
```

### 8.2 Function example — EMA → Kalman filter swap (stub)

Filename: `docs/research/timi/reflection/round11__ETHUSDT__03.md`

```yaml
---
round: 11
pair: "ETHUSDT"
iter: 3
layer: function
verdict: continue
author: A_fr
timestamp: "2026-04-09T15:02:10Z"
human_review_required: false
---
```

**Escalation rationale.** Iteration 2's LP on ETHUSDT was **infeasible**:
no combination of `fast_period` and `slow_period` in `ranges.yml` reduced
the MDD below the 8% target while keeping Sharpe > 0.5. Per the hierarchy,
we escalate from parameter to function layer.

```yaml
swap:
  from_component: "EMA"
  to_component: "KalmanFilter1D"
  rationale: >
    EMA's fixed-period smoothing cannot adapt to the ETHUSDT 2024-08
    volatility regime. A 1-D Kalman filter with adaptive process-noise
    estimate tracks the trend with lower lag during high-vol bars and
    wider bands during chop — empirically caught 4/5 tested reversals
    on the TRAIN fixture vs 2/5 for EMA.
  invariants_preserved:
    - "long-only"
    - "tick-aligned prices (no round() on Price)"
    - "single bar type (no multi-timeframe)"
    - "stateless w.r.t. symbols beyond this <PAIR>"
    - "parameter externalization (Kalman Q, R externalized to TimiConfig)"
```

**Note.** This directive is a stub per this document's scope — the actual
Kalman implementation is A_be's job and is NOT defined here. All this
directive does is authorize the swap. A_be will refuse until
`function_swaps.md` exists and contains the EMA ↔ KalmanFilter1D entry
(TODO tracked against Q6). The `infeasibility` evidence lives in
iteration 2's directive file (`round11__ETHUSDT__02.md`).

### 8.3 Strategy example — trend-following → mean-reversion (human review input)

Filename: `docs/research/timi/reflection/round11__BTCUSDT__05.md`

```yaml
---
round: 11
pair: "BTCUSDT"
iter: 5
layer: strategy
verdict: continue
author: A_fr
timestamp: "2026-04-09T16:44:00Z"
human_review_required: true
---
```

```yaml
preamble:
  current_strategy_family: "trend-following / EMA-cross with ATR stop"
  proposed_strategy_family: "mean-reversion / Bollinger-band pullback (long-only)"
  rationale_summary: >
    Four iterations of parameter and one function-layer swap failed to
    clear the 5% MDD target on BTCUSDT's TRAIN window. Evidence points
    to regime mismatch — BTCUSDT in the window is range-bound, not
    trending. Mean-reversion is the hypothesized better fit.
  sections_to_rewrite:
    - "on_bar signal: buy at lower BB (2σ below 20-SMA), exit at mid-band"
    - "no hard stop — exit rule is band-based (time-in-trade cap as safety)"
    - "position sizing: 0.5 × allocated capital, fixed"
  unchanged:
    - "MANIFEST (instrument BTCUSDT.BINANCE, bar type 1-HOUR-LAST-EXTERNAL)"
    - "long-only spot constraint"
    - "Decimal money, tick-aligned prices via instrument.make_price"
    - "test file location and pytest conventions"
```

**Risk evidence.** Iterations 1–4 parameter LP solutions all landed on
`feasibility: feasible` individually, but cumulative MDD across the
backtest windows remained > 7%. Function-layer swap (EMA → Kalman)
improved Sharpe from 0.41 to 0.58 but MDD stayed at 6.9%. The TRAIN
period shows 14 oscillations around a 65k midpoint with no sustained
trend — exactly the regime EMA-cross is weakest on. Distribution of
`per_trade_return` is symmetric with a mode at ~-0.4% (losing tail of
late reversals).

**Hypothesis.** Mean-reversion strategies profit from exactly this
oscillation shape. Bollinger-band pullback (buy at -2σ, sell at midband)
is the canonical long-only version. The same `Strategy` class stays;
only `on_bar` logic and the helper registration change.

**Draft acceptance criteria.**
- TRAIN-window MDD ≤ 5%.
- Sharpe ≥ 0.8 on TRAIN.
- Win rate ≥ 55%.
- No change to MANIFEST.
- Validator + `make test` green.
- Manual spot-check that `on_bar` signal logic compiles to ≤ 40 lines and touches no `round()` on `Price`.

A_be receives this directive, creates
`competition/agent-6-timi/round11/BTCUSDT/_draft_strategy.py` with a
skeleton Bollinger-band-pullback implementation, and emits
`STRATEGY_DRAFT_READY`. The orchestrator then pages a human reviewer.
Nothing is committed to `strategy.py` or `MANIFEST` until the human
approves.

---

## Contradictions to reconcile

Scan of `AGENT_SPECS/A_fr.md` and `AGENT_SPECS/A_be.md` against this
format. Anything not silently resolvable is listed here.

- **None found.** `A_fr.md` states its outputs are markdown files under
  `docs/research/timi/reflection/round<N>__<PAIR>__<iter>.md` with risk
  scenario, LP formulation, LP solution, escalation decision, and
  stop/continue. `A_be.md` lists three refinement modes (parameter,
  function, strategy) with the exact HOLD-on-strategy semantics this
  document encodes. The envelope, layer enum, verdict enum, hold
  semantics, and filename convention all reconcile cleanly.

If a future edit to either spec introduces a conflict, add a bullet here
and stop — do not silently resolve.

---

## TODOs (tracked against OPEN_QUESTIONS)

- **`docs/research/timi/function_swaps.md` does not exist.** Owned by a
  follow-up to `OPEN_QUESTIONS.md` Q6. Until it lands, A_be rejects all
  function-layer directives with `DIRECTIVE_ALLOWLIST_MISSING`.
- **`docs/research/timi/ranges.yml` does not exist.** Owned by a
  follow-up to `OPEN_QUESTIONS.md` Q15. Until it lands, A_be rejects all
  parameter-layer directives with `DIRECTIVE_RANGES_MISSING`.
- **Stop criterion numerics** (`max iter`, `Δ J(π_Θ)` threshold, token
  budget) are referenced by the `iter` field in the envelope but not
  fixed here. Owned by `OPEN_QUESTIONS.md` Q13.
- **Invariant vocabulary** for function-layer `invariants_preserved`
  needs a canonical list. Until then, A_be accepts any string but logs
  unknown entries for later review.
