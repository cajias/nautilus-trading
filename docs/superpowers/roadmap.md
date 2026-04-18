# Refactor Roadmap

Three sub-projects, executed in strict order. Each has its own spec → plan → implementation cycle. Do not start a later sub-project before its predecessor lands.

## Sub-project A — Core + strategies consolidation (IN PROGRESS)

Consolidate `nautilus/src/nautilus_trading/` and `strategies/crypto/` under SOLID principles. Establishes the Protocol-based strategy contract, the characterization test harness, and the module boundaries that B and C build on.

- **Spec:** `specs/2026-04-17-subproject-a-design.md`
- **Plan:** `plans/2026-04-17-subproject-a-implementation.md` — 8 PRs, ~135 TDD steps
- **Branch:** `design/subproject-a-v2`

## Sub-project B — Binance live trading (PLANNED, not started)

Paper-trade → testnet → production promotion path for live crypto trading. Consumes A's refactored strategy surface.

Scope (from A's §2 non-goals):
- Real-money Binance live trading
- Ed25519 key rotation, production environment wiring
- `strategies/crypto/kronos/paper_trade.py` migration (quarantined in A)
- Live node config + secrets handling

**Status:** No spec yet. Brainstorm after A ships.

## Sub-project C — Competition platform (PLANNED, not started)

Everything under `competition/` and `strategies/competition/`. R11+ submission contract where agents submit research + a `Strategy` subclass that is live-pluggable.

Scope (from A's §2 non-goals):
- Submission validators, leaderboard
- `strategies/competition/` tree
- Consumes A's Protocol surface

**Status:** No spec yet. Brainstorm after B ships.

## Why this order

A unblocks B and C — its `StrategyConfigBuilder` Protocol, `BacktestRunner` ABC, `_grid_math.py` helper, and characterization test harness are the load-bearing interfaces the others depend on. Starting B or C before A would mean speculative abstractions or a double-refactor.

B precedes C because live trading delivers revenue potential; competition infrastructure is secondary.
