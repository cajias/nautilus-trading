# TiMi → NautilusTrader Design Mapping

> How TiMi's four-agent architecture would be ported onto our stack:
> NautilusTrader v1.224.0 + Binance Spot testnet paper trading + the
> existing `competition/` R11+ submission contract.
>
> This is a mapping document, not an implementation plan. No file here
> specifies sprints, PRs, or code — only the shape of the port.

## Scope (hard lines)

**In scope:**
- NautilusTrader `BacktestEngine` for offline simulation (the "optimization stage" substrate).
- Binance **Spot testnet** paper trading via `TradingNode` + `BinanceLive*Factory` for the "deployment stage".
- Long-only or rebalance-to-zero strategies. Enter long, exit to cash.
- `Decimal`-only money, tick-aligned prices, existing `competition/validate_submission.py` gate as our sandbox guard.
- Existing `strategies/crypto/*.py` universe as the initial "function layer" library.

**Out of scope (and why):**
- **Real-money deployment** — competition constraints forbid it; this port is paper-only.
- **Futures, margin, isolated margin, options, perpetuals.** The paper benchmarks on "NQ index futures" and "altcoin futures"; we cannot replicate that on Spot. Any LP constraint we port must be rewritten as a long-spot equivalent.
- **Short selling.** Spot allows no shorts. TiMi's "position size under volatility" LP case must be reinterpreted as "maximum long exposure" (see "Safety rails" below).
- **Leverage `λ > 1`.** The paper's `λ` capital multiplier must be clamped to `λ ∈ (0, 1]`.
- **Alternative venues** (Coinbase, Kraken, DEXes) — Binance Spot testnet only for now.
- **News/sentiment ingestion.** The paper explicitly excludes this from `A_ma`. We should too, at least for v1. (If we add it later, it belongs in a *fifth* agent outside the TiMi core.)
- **Minute-level live inference.** TiMi's "Trade in Minutes" claim refers to **action cadence**, not LLM latency — the LLMs run offline, the bot runs on minute bars. We inherit this. No LLM in the Nautilus `on_bar()` hot path ever.

## Agent → Claude-Code-subagent mapping table

| TiMi agent | Paper role | Our subagent type | Key tools | Reads | Writes |
|-----------|-----------|-------------------|-----------|-------|--------|
| **A_ma** — macro analyst | Identify market patterns from technical indicators, formulate general strategy set `S` | `general-purpose` ("timi-macro-analyst") | `Read`, `Bash` (for `nt` CLI / `uv run`), `ctx_search` (reference papers), `mcp__plugin_context-mode_context-mode__ctx_search` | `catalog/*.parquet` klines, `strategies/crypto/*.py` (existing patterns), paper references | `docs/research/timi/macro/S_<regime>.md` — a catalog of general strategy templates keyed by observed regime (trend, mean-reverting, choppy) |
| **A_sa** — strategy adapter | Customize `S` → `S_p` with initial `Θ_p` per pair | `general-purpose` ("timi-strategy-adapter") | `Read`, `Bash`, `Grep`, `ctx_search` | `docs/research/timi/macro/*.md`, `catalog/*.parquet` (pair-specific stats), `competition/TEMPLATE/` | `docs/research/timi/adapted/<pair>__<regime>.md` — pair-adapted strategy spec + initial params |
| **A_be** — bot evolution | Emit Python bot code respecting the 3 laws; accept refinement directives from `A_fr` | `general-purpose` ("timi-bot-engineer") with Code-LLM-style system prompt | `Read`, `Write`, `Edit`, `Bash` (`make lint`, `make test`, `python competition/validate_submission.py`) | `docs/research/timi/adapted/*.md`, `competition/TEMPLATE/strategy.py` | `competition/agent-N-timi/roundX/strategy.py`, `tests/test_strategy.py`, `README.md` — ONLY via the `crypto-competition` skill. Never touches production `strategies/crypto/`. |
| **A_fr** — feedback reflection | LP-based parameter refinement; hierarchical escalation | `general-purpose` ("timi-reflection") with mathematical-reasoning system prompt | `Read`, `Bash` (run `BacktestEngine`, read `PortfolioAnalyzer` output), optional `scipy.optimize.linprog` via a constrained Python tool | `competition/agent-N-timi/roundX/` backtest artifacts, `round*_results.txt`, `PortfolioAnalyzer` outputs | `docs/research/timi/reflection/<round>__<iter>.md` — LP formulation, proposed parameter diff, escalation decision. Does NOT write code. |

**LLM backend (decided 2026-04-09, supersedes paper's DeepSeek/Qwen hybrid):** all four agents run on **Claude Code in programmatic mode** (`claude -p` headless or `claude_agent_sdk`). Single model across the board; specialization happens via system prompt + tool allowlist + per-agent file-write restrictions (see AGENT_SPECS/*.md). No separate code LLM, no separate math LLM. The orchestrator is plain Python glue that invokes `claude` once per agent and collects its output.

## Data flow

```
                               ┌─────────────────────┐
                               │  catalog/*.parquet  │
                               │  (Binance klines)   │
                               └──────────┬──────────┘
                                          │
                                          ▼
┌──────────────────────────┐   ┌─────────────────────┐
│ Indicator library (static)│──▶│   A_ma  (offline)   │
│  strategies/crypto/*.py   │   │ macro regime + S    │
└──────────────────────────┘   └──────────┬──────────┘
                                          │ S (macro strategy templates)
                                          ▼
         ┌───────────────────┐   ┌─────────────────────┐
         │ pair stats (vol,  │──▶│   A_sa  (offline)   │
         │ amplitude, depth) │   │  S_p + initial Θ_p  │
         └───────────────────┘   └──────────┬──────────┘
                                          │ pair-specific spec
                                          ▼
                               ┌─────────────────────┐
                               │   A_be  (offline)   │
                               │  layered bot code   │
                               └──────────┬──────────┘
                                          │ competition/agent-N-timi/roundX/strategy.py
                                          ▼
          ┌────────────────┐     ┌─────────────────────┐
          │  validator +   │◀────│  validate_submission│
          │  3 laws gate   │─────▶│   + make lint/test  │
          └────────────────┘     └──────────┬──────────┘
                                          │ passes
                                          ▼
                               ┌─────────────────────┐
                               │  BacktestEngine     │
                               │  on TRAIN + TEST    │
                               │  PortfolioAnalyzer  │
                               └──────────┬──────────┘
                                          │ F (metrics, trade log, risk events)
                                          ▼
                               ┌─────────────────────┐
                               │   A_fr  (offline)   │
                               │  LP → new Θ (or     │
                               │  escalate layer)    │
                               └──────────┬──────────┘
                                          │ diff
                                          │
                            ┌─────────────┴────────────┐
                            │                          │
                            ▼ (parameter level)        ▼ (escalation)
                    edit Θ in config                apply to A_be
                    re-run BacktestEngine           as a rewrite order
                                          │
                                          ▼ converged B*
                               ┌─────────────────────┐
                               │  Binance Spot       │
                               │  testnet paper      │
                               │  via TradingNode    │
                               │  (runner.py)        │
                               └─────────────────────┘
```

**Input sources (be concrete):**
- `A_ma`'s only data source is `catalog/*.parquet` — historical klines. It does NOT consume news. This matches the paper.
- `A_sa`'s additional inputs come from computing per-pair stats on the same parquet files (volatility, amplitude, typical volume profile).
- `A_be` reads `competition/TEMPLATE/strategy.py` and the `crypto-competition` skill as its "patterns library".
- `A_fr` reads the output of `PortfolioAnalyzer` (which we already use in the evaluator) plus the raw trade log and can optionally call `scipy.optimize.linprog` in a tight sandbox for LP solving.

## Strategy layer ↔ NautilusTrader `Strategy` class

TiMi's three layers map cleanly onto our existing file shape:

| TiMi layer | Our file / class | Who mutates it | Gate |
|-----------|------------------|----------------|------|
| **Strategy layer** | `class TimiStrategy(Strategy):` — `on_bar`, `on_quote_tick`, `on_event`. Signal logic, entry/exit, position sizing rule. | A_be only, **on strategy-level escalation from A_fr** | `validate_submission.py` + `make test` + human review |
| **Function layer** | `strategies/crypto/*.py` helper modules (EMA, RSI, etc.) already used by agent strategies — OR vendored `nautilus_trader.indicators` classes. | A_be only, on function-level escalation | Unit tests in `tests/test_<helper>.py` |
| **Parameter layer** | `class TimiConfig(StrategyConfig, frozen=True): sma_fast: int = 10; ...` — all tunables as typed fields. | A_fr directly (via parameter-level LP) and A_be indirectly | Range checks in `validate_submission.py`, tick-size rounding on any `Price` |

**The LLM only gets free rein on the parameter layer.** Function and strategy layers go through a code-review gate. This is our interpretation of "minimal intervention" — see "Safety rails" below.

## Evolution loop — concrete flow

1. **Trigger**: a new competition round opens (`round_configs/round11.py` is the current baseline). Orchestrator spawns a `timi-macro-analyst` subagent.
2. **A_ma** reads the TRAIN window from `catalog/`, detects regime, writes `docs/research/timi/macro/round<N>.md` with a short list of candidate strategy templates (EMA cross, VWAP pullback, Donchian breakout, etc.). One macro regime analysis per round, shared across pairs.
3. **A_sa** spawns next. **For each pair in the round** (per Q18, per-pair strategies), it picks a template from `A_ma`'s output and writes initial `Θ_p`. Output: `docs/research/timi/adapted/round<N>__<PAIR>.md` (one file per pair, e.g., `round11__BTCUSDT.md`, `round11__ETHUSDT.md`, ...).
4. **A_be** is invoked once per pair via the `crypto-competition` skill. For each pair it copies `competition/TEMPLATE/`, renames to `competition/agent-N-timi/round<N>/<PAIR>/`, and writes a `strategy.py` that reflects that pair's `A_sa` spec. Per pair it runs:
   - `python competition/validate_submission.py competition/agent-N-timi/round<N>/<PAIR>/` (3-laws gate)
   - `make lint` and `make test`
   - `cd nautilus && uv run nt backtest --submission competition/agent-N-timi/round<N>/<PAIR>/ --train` (the TRAIN window only, per-round, no leakage across rounds because per Q7 each round is cold-started)
5. **A_fr** is invoked per pair with that pair's backtest report. It reads `PortfolioAnalyzer` output (remember: monetary cols are strings — strip currency before LP solving), identifies risk scenarios, formulates an LP, and proposes a parameter diff. If the LP is infeasible, it escalates: "swap EMA → Kalman" → reinvokes A_be for that pair only.
6. **Loop per pair**: A_be applies the diff (or rewrite), re-runs validation + backtest, hands back to A_fr. Stop when either (a) max N=5 iterations hit, (b) `J(π_Θ)` improvement < 0.5% for 2 consecutive iterations, or (c) token budget hit. **Exact thresholds are open** — see OPEN_QUESTIONS Q13.
7. **Promote**: for each pair, the final `B*<PAIR>` (validated `strategy.py`) goes into `competition/agent-N-timi/round<N>/<PAIR>/` as the round submission. **The evaluator needs a per-pair-capable mode** (see Q18 follow-up) to score the aggregate `agent-N-timi` result across its N per-pair strategies. Each `strategy.py` is ALSO its pair's live-paper-trading binary on Binance Spot testnet — same class, no rewrite. Per Q7, nothing persists into round<N+1>; next round starts fresh.

## Safety rails

The paper's "minimal intervention" is an optimization heuristic. On our side it is a **security boundary**. We enforce it with teeth:

### Parameter-layer changes (default, cheap)

- **Allowlist:** only numeric fields declared in `TimiConfig` can be mutated.
- **Gate:** `validate_submission.py` already runs on every submission; extend it to enforce a per-field range (e.g., `fast_period ∈ [5, 200]`) declared in a sibling `ranges.yml`.
- **LP post-condition:** after A_fr emits new values, a helper re-runs `ranges.yml` check + a tick-size rounding pass on anything that becomes a `Price`. Competition validator already blocks `round()` on `Price` — new values must be produced already-aligned.
- **Review:** none required. Fully automated.

### Function-layer changes (escalation 1)

- **Allowlist:** substitute only from a curated set of interchangeable components (EMA↔SMA↔WMA, RSI↔Stochastic, ATR-stop↔chandelier-stop). The list lives in `docs/research/timi/function_swaps.md` (to be created by a future task).
- **Gate:** same validator + `make test` + run the function's unit tests.
- **Review:** required. A `code-reviewer` subagent must sign off before the bot is re-backtested.

### Strategy-layer changes (escalation 2)

- **Allowlist:** none. Any new strategy is a de-novo submission.
- **Gate:** full validator + new test suite + notebook proof in `research/`.
- **Review:** **human-in-the-loop.** A_fr can propose, A_be can draft, but a human must merge. The paper's continuous-evolution model does not fit our round-based competition cadence well; we need a stop between strategy rewrites.

### Hard blocks (independent of layer)

- `MANIFEST` must remain valid.
- `round()` on `Price` → validator rejects.
- Any `margin`, `short`, `borrow` keyword → validator rejects.
- Any `Strategy` subclass without `on_bar` → validator rejects.
- Per-submission budget: A_fr may spend ≤ K LLM tokens before forced termination. (K is an open question.)

## Backtesting substrate

TiMi's optimization loop maps 1:1 onto our `BacktestEngine` flow:

| TiMi concept | Our binding |
|-------------|-------------|
| `M` — market | `InstrumentId` + Binance Spot venue |
| `W` — time window | `start`/`end` args to `engine.add_data()` |
| `S` — strategy space | Human-curated templates in `docs/research/timi/macro/` |
| `Θ` — parameters | `StrategyConfig` fields |
| `F` — feedback | `PortfolioAnalyzer` outputs: total return, Sharpe, Sortino, max drawdown, win rate, profit factor, trade log dataframe |
| `B*` — refined bot | The final `competition/agent-N-timi/roundX/strategy.py` |

**A_fr consumes these metrics:** `total_pnl`, `total_return`, `sharpe_ratio`, `sortino_ratio`, `max_drawdown`, `win_rate`, `profit_factor`, plus the raw trade log for identifying corner cases (consecutive losses, volatility spike that ate through stop, etc.).

**Gotcha:** `realized_pnl` and `commissions` come out of `PortfolioAnalyzer` as strings like `"-9.48 USD"`. A_fr must strip the currency suffix with `.str.replace(r"\s+\w+$", "", regex=True).astype(float)` before any numeric comparison. We have this bitten us before (see memory note `reports-strings`).

## Deployment

Once A_fr converges on a `B*` that passes `validate_submission.py` + backtest on the TRAIN+TEST windows:

1. Submission is committed to `competition/agent-N-timi/roundX/`.
2. `evaluate.py --round X` scores it on the hidden eval window (this is the competition's evaluator, unchanged).
3. If it is round-winning, or we want to dry-run it, `nautilus/src/nautilus_trading/live/runner.py` reads the same `MANIFEST` and instantiates the identical class against Binance Spot **testnet**.
4. Testnet runs on real-time WebSocket klines from Binance sandbox with Ed25519 auth (see memory: `project_live_trading_gotchas`). No real USDT at risk ever. The runner defaults to `testnet=True`; we do not expose a production toggle to any TiMi agent.
5. Any live-trading state (fills, orphaned orders, price deviation alarms) is logged and fed back into `A_fr` on the next offline cycle as a new source of `F`.

**The same `Strategy` class runs in both engines.** This is NautilusTrader's killer feature and the reason this port is worth doing. `A_be` doesn't have to emit two versions.

## Things we are explicitly NOT replicating

- Real-money futures deployment.
- News/sentiment ingestion for `A_ma`.
- Full 200+ pair coverage — we'll start with 2-3 pairs per round.
- The paper's hybrid local/API LLM stack (DeepSeek-V3 + Qwen + DeepSeek-R1). We use Claude Code subagents, single provider, until proven otherwise.
- Continuous (always-on) evolution. Our competition is round-based; each round resets.
- The reported live ARR numbers as a success target. Our goal is "correctly reproduce the architecture", not "beat the paper".
