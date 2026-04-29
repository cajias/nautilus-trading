# Refactor Roadmap

Three sub-projects, executed in strict order. Each has its own spec → plan → implementation cycle. Do not start a later sub-project before its predecessor lands.

## Sub-project A — Core + strategies consolidation (SHIPPED 2026-04-21)

Consolidated `nautilus/src/nautilus_trading/` and `strategies/crypto/` under SOLID principles. Established the Protocol-based strategy contract, the characterization test harness, and the module boundaries that B and C build on.

- **Spec:** `specs/2026-04-17-subproject-a-design.md`
- **Plan:** `plans/2026-04-17-subproject-a-implementation.md` — 8 PRs, merged GH #11–#18 (+ #19 pr8.5 hygiene sweep)

## Sub-project B — Binance paper-trade testbed (SHIPPED)

Delivered a paper-trade surface on Binance Spot Testnet as the on-ramp to real-money live trading. Everything in B is opt-in: normal `make test` does not talk to Binance.

What shipped:

- `nt paper-trade --config configs/paper/<strategy>.yaml` CLI (YAML-driven, one file per strategy).
- Nine YAML-wired paper-trade runners: `ema_cross`, `grid_bot`, `dca_bot`, `timesfm_swing`, `hybrid_sma_r10`, `timesfm_grid`, `rvs_swing`, `shock_guard`, `kronos` — each with a `configs/paper/<name>.yaml` and an entry in `_RUNNERS` (retired in B.5).
- Core wiring under `nautilus/src/nautilus_trading/paper_trade/`: `PaperTradeRunner` ABC (retired in B.5), `build_paper_trade_node_config` (Ed25519 + InstrumentProvider defaults), `run_paper_trade` (SIGINT/SIGTERM lifecycle), `PaperRunConfig` msgspec schema, `round_to_tick` price helper.
- Opt-in pre-release smoke: `tests/paper_trade/test_smoke_paper.py` gated by the `binance_testnet` pytest marker; every runner must boot and receive at least one `Bar` within 30s.
- Order-path smoke: `make smoke-paper-order STRATEGY=<name>` submits + cancels an off-market LIMIT.
- Operator docs: `docs/runbooks/paper-trade.md` (this sub-project's user-facing runbook).

Out of scope, deferred to future work:

- Real-money production key rotation + promotion flow.
- Operator-invoked panic-close endpoint (moves into sub-project C).

## Sub-project B.5 — Runner unification (IN PROGRESS)

Unify strategy execution under one generic `Strategy` class + three runners (`BacktestStrategyRunner`, `PaperTradeStrategyRunner`, `LiveStrategyRunner`) sharing a YAML-driven interface. Replaces the 8 near-identical `strategies/crypto/*_paper.py` shims with a single generic runner driven by a `StrategySpec` registry. Prerequisite for C — the R11+ submission contract requires strategies to be live-pluggable across all three modes without per-strategy runner code.

- **Plan:** `/Users/rc/.claude/plans/o-strategies-why-do-logical-fiddle.md` (approved 2026-04-23)
- **PRs:** PR 0 roadmap, PR 1 ✅ generic paper runner + delete shims (#41), PR 2 ✅ generic backtest runner + 8 backtest YAMLs (#42), PR 3 kronos parity + retire `KronosBacktestRunner` + ship `kronos.yaml`, PR 4 scaffold `LiveStrategyRunner` (raises `NotImplementedError` per 2026-04-21 no-real-money directive).

## Sub-project C — Competition platform (PLANNED, not started)

Everything under `competition/` and `strategies/competition/`. R11+ submission contract where agents submit research + a `Strategy` subclass that is live-pluggable.

Scope (from A's §2 non-goals):
- Submission validators, leaderboard
- `strategies/competition/` tree
- Consumes A's Protocol surface

**Status:** No spec yet. Next sub-project to brainstorm now that B has shipped.

## Why this order

A unblocks B, B.5, and C — its `StrategyConfigBuilder` Protocol, `BacktestRunner` ABC, `_grid_math.py` helper, and characterization test harness are the load-bearing interfaces the others depend on.

B precedes B.5 because B's 9 paper-trade runners are the raw material that B.5 unifies. B.5 precedes C because C's submission contract requires live-pluggable strategies across all three modes — B.5's generic runners are that surface. Real-money execution remains out of scope per the 2026-04-21 directive.
