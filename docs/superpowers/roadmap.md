# Refactor Roadmap

Three sub-projects, executed in strict order. Each has its own spec → plan → implementation cycle. Do not start a later sub-project before its predecessor lands.

## Sub-project A — Core + strategies consolidation (IN PROGRESS)

Consolidate `nautilus/src/nautilus_trading/` and `strategies/crypto/` under SOLID principles. Establishes the Protocol-based strategy contract, the characterization test harness, and the module boundaries that B and C build on.

- **Spec:** `specs/2026-04-17-subproject-a-design.md`
- **Plan:** `plans/2026-04-17-subproject-a-implementation.md` — 8 PRs, ~135 TDD steps
- **Branch:** `design/subproject-a-v2`

## Sub-project B — Binance paper-trade testbed (SHIPPED)

Delivered a paper-trade surface on Binance Spot Testnet as the on-ramp to real-money live trading. Everything in B is opt-in: normal `make test` does not talk to Binance.

What shipped:

- `nt paper-trade --config configs/paper/<strategy>.yaml` CLI (YAML-driven, one file per strategy).
- Eight YAML-wired paper-trade runners: `ema_cross`, `grid_bot`, `dca_bot`, `timesfm_swing`, `hybrid_sma_r10`, `timesfm_grid`, `rvs_swing`, `shock_guard`. Kronos ships as a `PaperTradeRunner` (actor + strategy + parity gate) but YAML + `_RUNNERS` wiring is tracked as task #42; until it lands, Kronos is reachable only via the Python API.
- Core wiring under `nautilus/src/nautilus_trading/paper_trade/`: `PaperTradeRunner` ABC, `build_paper_trade_node_config` (Ed25519 + InstrumentProvider defaults), `run_paper_trade` (SIGINT/SIGTERM lifecycle), `PaperRunConfig` msgspec schema, `round_to_tick` price helper.
- Opt-in pre-release smoke: `tests/paper_trade/test_smoke_paper.py` gated by the `binance_testnet` pytest marker; every runner must boot and receive at least one `Bar` within 30s.
- Order-path smoke: `make smoke-paper-order STRATEGY=<name>` submits + cancels an off-market LIMIT.
- Operator docs: `docs/runbooks/paper-trade.md` (this sub-project's user-facing runbook).

Out of scope, deferred to future work:

- Real-money production key rotation + promotion flow.
- Operator-invoked panic-close endpoint (moves into sub-project C).

## Sub-project C — Competition platform (PLANNED, not started)

Everything under `competition/` and `strategies/competition/`. R11+ submission contract where agents submit research + a `Strategy` subclass that is live-pluggable.

Scope (from A's §2 non-goals):
- Submission validators, leaderboard
- `strategies/competition/` tree
- Consumes A's Protocol surface

**Status:** No spec yet. Next sub-project to brainstorm now that B has shipped.

## Why this order

A unblocks B and C — its `StrategyConfigBuilder` Protocol, `BacktestRunner` ABC, `_grid_math.py` helper, and characterization test harness are the load-bearing interfaces the others depend on. Starting B or C before A would mean speculative abstractions or a double-refactor.

B precedes C because live trading delivers revenue potential; competition infrastructure is secondary.
