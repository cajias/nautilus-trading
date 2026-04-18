# Sub-project A — Core + Crypto Strategies Redesign

**Date:** 2026-04-17
**Status:** APPROVED — Option 2 scope, decisions recorded in §8
**Author:** architect (team `nautilus-a-core-strategies`)
**Inputs:** `docs/superpowers/audits/2026-04-17-strategies-crypto-audit.md`, `docs/superpowers/audits/2026-04-17-core-package-audit.md`, `docs/superpowers/audits/2026-04-17-test-coverage-map.md`

---

## 1. Goals

1. **Remove fallbacks/mocks/unnecessary files from runtime.** Runtime code paths (`nautilus/src/nautilus_trading/**`, `strategies/crypto/**`) must contain zero dead demos, synthetic-data fallbacks, or stubs. Tests may use synthetic data; runtime must fail loudly when real data is missing.
2. **SOLID-driven module boundaries.** Close the two HIGH findings in the core audit: SRP violation in `cli/live.py` (parsing + per-strategy config building), and OCP/DIP violation where runners string-match strategy names instead of using a typed discovery mechanism.
3. **Prune dead code + fix SRP/OCP violations in `strategies/crypto/` and core CLI.** Delete `backtest_demo.py` (341 LOC, no tests, no Makefile target). Split `kronos/backtest.py` (370 LOC) so engine config is separate from the runner. Replace string-matching strategy dispatch in `cli/live.py` and `cli/backtest.py` with a `StrategyConfigBuilder` Protocol + `STRATEGY_BUILDERS` registry. Extract a `BacktestRunner` base so kronos stops being a parallel code path. Do **not** impose a "promoted vs experimental" split — the audit does not justify one.
4. **Real-scenario tests covering the full production path.** Fill the coverage gap: `cli/live.py`, `cli/strategies.py`, `live/runner.py`, `data/providers.py`, `data/download.py` currently have zero tests. This is the pre-condition for refactoring any of them.
5. **Trustworthy end-to-end flows.** Backtest (`make backtest-crypto STRATEGY=...`) and Binance testnet paper trade (`make live STRATEGY=...`) must be exercised by tests — not just hand-verified.

## 2. Non-goals

- **Out of scope — owned by sub-project B:** real-money live trading, Ed25519 key rotation, Binance production environment wiring.
- **Out of scope — owned by sub-project B:** `strategies/crypto/kronos/paper_trade.py` — covered by sub-project B's live-trading track.
- **Out of scope — owned by sub-project C:** anything under `competition/`, `strategies/competition/`, submission validators, leaderboard.
- **No new strategies, no new indicators.** Existing strategies stay functionally identical.
- **No `strategies/forex/` changes.** The `ema_cross` reference strategy is untouched.
- **No consolidation of TimesFM/grid variants.** The audit flagged them as legitimately distinct (different risk profiles, different signals). Do not merge.
- **No DI container rewrite** unless option 3 is selected.

## 3. Current-state summary (from audits)

- `strategies/crypto/backtest_demo.py` (341 LOC) is a hardcoded-EMA demo with no tests and no Makefile target — **dead code**.
- `strategies/crypto/kronos/backtest.py` (370 LOC) mixes `BacktestEngine` instantiation, venue setup, and data-catalog wiring into a single script — SRP violation.
- Seven files exceed 300 LOC; the top three are `timesfm_grid.py` (538), `kronos/actor.py` (427), `kronos/backtest.py` (370). Only `kronos/backtest.py` is a clear SRP fault; `timesfm_grid.py` is large but cohesive.
- `RiskGuard` mixin is inherited by 6 strategies. This is healthy reuse — **do not touch**.
- `cli/live.py` (177 LOC) blends argument parsing with per-strategy config branches for `grid_bot`, `dca_bot`, EMA strategies, TimesFM (including `fallback_fast_ema_period`), and hybrid SMA (`Decimal` conversion). HIGH-severity SRP violation.
- `cli/backtest.py` (150 LOC) and `backtest/runner.py` (118 LOC) repeat the pattern at lower severity.
- Strategy discovery is string-based: `if module_name == "grid_bot": …`. No `Protocol`, no registry. HIGH OCP/DIP finding.
- Utility functions `_ensure_project_root_on_path()` and `_resolve_strategy_paths()` live in `cli/backtest.py` but are imported by `cli/live.py` — duplication hazard.
- Data provider abstraction (`data/providers.py` + `PROVIDERS` registry in `data/download.py`) is **already good**. Do not touch.
- Only one `fallback` token in runtime code (`cli/live.py:147`), and it is a legitimate strategy config parameter (`fallback_fast_ema_period`), not a code fallback. Runtime is clean of synthetic data.
- No circular imports. A dynamic import in `cli/live.py:111` is a precaution and can be resolved to a static import.
- **Test coverage — core package: ~25%.** `cli/live.py`, `cli/strategies.py`, `live/runner.py`, `data/providers.py`, `data/download.py`, `__main__.py` have **zero tests**.
- Strategy test coverage is strong (389 tests across 10 files), but `kronos/backtest.py`, `kronos/paper_trade.py`, `rvs_data.py`, and `backtest_demo.py` have no direct tests.
- `pytest --collect-only` **fails** in the repo: engine initialization imports time out during collection. Test counts today come from AST analysis, not pytest. This is a CI blocker.
- Competition path (R11+) is unrelated to this sub-project but consumes `Strategy` subclasses from `strategies/crypto/` — renames will ripple. Note in rollout.

## 4. Proposed structure — three options

### Option 1 — Minimal (recommended, see §4.4)

Scope: remove dead code; fix SRP violations in-place.

- Delete `strategies/crypto/backtest_demo.py`.
- Extract a `cli/_common.py` containing `_ensure_project_root_on_path()` and `_resolve_strategy_paths()`; import from both `cli/backtest.py` and `cli/live.py`.
- Split `kronos/backtest.py` into `kronos/backtest_config.py` (engine/venue/catalog builder) + a thin `kronos/backtest.py` runner (≤80 LOC).
- Resolve the dynamic import in `cli/live.py:111` to a static import (if it is a precaution, prove it can be static).
- Add characterization tests for `cli/live.py`, `cli/strategies.py`, `live/runner.py`, `data/providers.py`, `data/download.py` (see §5).
- No new abstractions. No registry. Strategy-name branches stay in CLI modules — just covered by tests now.

**Ships in 1–2 PRs after the test PR.** Risk: low. SOLID improvements: partial (closes SRP, defers OCP/DIP).

### Option 2 — Protocol-based

Everything in Option 1, plus:

- Introduce `cli/_strategy_configs.py` with a `StrategyConfigBuilder` Protocol and one builder per strategy (`GridBotConfigBuilder`, `DCABotConfigBuilder`, `EMAConfigBuilder`, `TimesFMConfigBuilder`, `HybridSMAConfigBuilder`).
- Replace the string-match branches in `cli/live.py` and `cli/backtest.py` with a `STRATEGY_BUILDERS` registry keyed by module name.
- Extract a `BacktestRunner` base/Protocol in `backtest/` that `kronos/backtest.py` can implement, so kronos is no longer a parallel code path.
- Move `backtest/runner.py`'s hardcoded EMA branch behind the same builder registry.

**Ships in 3–4 PRs after the test PR.** Risk: medium — touches the live-trading path that has no tests today, so Option 2 is strictly gated on the characterization-test PR landing first. SOLID improvements: closes both HIGH findings.

### Option 3 — Full DI container

Everything in Option 2, plus:

- Formal DI container (likely `dependency-injector` or hand-rolled) wiring runners, clients, and strategies.
- Plugin-based strategy loader discovering strategies via entry points / filesystem scan instead of imports in CLI.
- Config-driven wiring (YAML/TOML) replacing in-code dicts across the core package.

**Ships in 6+ PRs after the test PR.** Risk: high. Larger blast radius than current pain justifies; adds a framework that isn't earning its keep yet.

### Decision — Option 2 (Protocol-based), ordered test-first per §5

The user elected to fold the Protocol registry and `BacktestRunner` extraction into this sub-project rather than deferring. The core package sits at 25% test coverage with zero tests on the live path, so the justification for ordering PR 1 as characterization tests still stands — we are not refactoring an unverified production path. Once PR 1 is green, the remaining Option 2 PRs introduce `StrategyConfigBuilder` + registry and `BacktestRunner` incrementally, each behind the safety net of PR 1's tests. Option 3 (DI container) stays out of scope.

## 5. Test-first ordering

**PR 1 ("green the harness") lands before any structural change.** Scope:

1. Fix the pytest-collection-timeout issue (see §7 for options). Without this, CI cannot enforce anything we add.
2. Characterization tests for:
   - `data/providers.py` — `TestDataProvider.ensure_catalog()` against a fixture parquet dir; registry lookup; error on unknown provider.
   - `data/download.py` — `PROVIDERS` dict; CLI dispatch; catalog path resolution.
   - `backtest/runner.py` — `build_backtest_config()` round-trip for one strategy per current branch (EMA, grid_bot, dca_bot, hybrid_sma, timesfm variant); `run_backtest()` smoke test against fixture data; `print_results()` golden-file snapshot.
   - `cli/live.py` — Typer `CliRunner` invocations per strategy, asserting the built `TradingNodeConfig` (captured via monkeypatch of `run_live()`) matches a snapshot. No network calls. Uses `BinanceEnvironment.TESTNET`.
   - `cli/strategies.py` — `nt strategies` lists all discovered strategies; absent strategies raise clearly.
   - `live/runner.py` — `build_live_config()` returns correct factories for Binance testnet; `_check_api_keys()` fails cleanly when env vars absent; `run_live()` is invoked via a `TradingNode` test double that records `.run()` without blocking.
3. One end-to-end scenario per flow:
   - `make backtest-crypto STRATEGY=crypto.grid_bot` against a fixture catalog finishes and produces expected result columns.
   - `make live STRATEGY=crypto.grid_bot` (testnet-stubbed) builds a valid `TradingNodeConfig` and calls `TradingNode.build()` without side effects, then exits before `.run()`.

**Test data source.** Default: committed parquet fixtures under `tests/fixtures/` (hermetic, fast, no network) — matches the `tests/competition/fixtures/` pattern already used elsewhere. **Additionally**, an opt-in Binance testnet smoke test is wired into CI, gated by presence of `BINANCE_TESTNET_API_KEY`/`BINANCE_TESTNET_API_SECRET`; it runs the full `make live STRATEGY=crypto.grid_bot` path against testnet and asserts a valid `TradingNode` lifecycle without placing real orders. Absent the secret, CI skips the job — no synthetic fallback.

Target: the changed core modules reach ≥70% line coverage before any refactor in PR 2+.

## 6. Concrete file-level change list (Option 2)

### DELETE
- `strategies/crypto/backtest_demo.py` (341 LOC, no tests, no Makefile target).
- Any Makefile target referencing `backtest_demo` (grep at execution time).

### MOVE
- `cli/backtest.py::_ensure_project_root_on_path` → `cli/_common.py` (new file).
- `cli/backtest.py::_resolve_strategy_paths` → `cli/_common.py`.
- Update imports in `cli/backtest.py` and `cli/live.py` to use `_common`.

### EXTRACT
- Split `strategies/crypto/kronos/backtest.py` (370 LOC) into:
  - `strategies/crypto/kronos/backtest_config.py` — engine config, venue setup, catalog wiring, default params (pure data/builders, no I/O except catalog load).
  - `strategies/crypto/kronos/backtest.py` — thin runner (≤80 LOC) that composes `backtest_config` + the shared `BacktestRunner` base.
- `cli/_strategy_configs.py` (new) — `StrategyConfigBuilder` Protocol plus one concrete builder per strategy currently string-matched in `cli/live.py` / `cli/backtest.py`: `GridBotConfigBuilder`, `DCABotConfigBuilder`, `EMAConfigBuilder`, `TimesFMConfigBuilder` (handles `fallback_fast_ema_period`), `HybridSMAConfigBuilder` (handles `Decimal` conversion, skip `trade_size`). Export a `STRATEGY_BUILDERS` registry keyed by module name. Remove all per-strategy branches from `cli/live.py:123-157` and their `cli/backtest.py` counterparts.
- `backtest/runner_base.py` (new) — `BacktestRunner` abstract base defining `build_config()`, `add_data()`, `run()`, `print_results()`. Migrate `backtest/runner.py`'s EMA-specific branches behind this interface. Update `strategies/crypto/kronos/backtest.py` to subclass `BacktestRunner` so kronos stops being a parallel code path.
- `strategies/crypto/_grid_math.py` (new) — pure-function helpers for grid level computation, price bucketing, and grid rebalancing math, extracted from `timesfm_grid.py` (538 LOC). If the implementation reveals that `grid_bot.py` duplicates any of this math, route it through `_grid_math.py` too; otherwise leave `grid_bot.py` alone. `timesfm_grid.py` itself stays but shrinks (expected target: <400 LOC).
- Replace the dynamic import at `cli/live.py:111` with a static `from nautilus_trading.live import runner` unless the commit history documents a circular-import reason.

### KEEP-AS-IS
- `strategies/crypto/risk_guard.py` (mixin used by 6 strategies).
- `strategies/crypto/timesfm_grid.py` — stays in place; only the grid-math helpers are lifted out. Strategy class and lifecycle methods unchanged.
- `strategies/crypto/shock_guard.py` (360 LOC, well-tested, multi-regime logic is genuinely one responsibility).
- `strategies/crypto/kronos/actor.py`, `strategy.py`, `data.py` (no SRP violation).
- `strategies/crypto/kronos/paper_trade.py` — owned by sub-project B; untouched here.
- `data/providers.py`, `data/download.py` (clean registry pattern — do not touch).
- All strategy lifecycle code, `BacktestNode`/`TradingNode` wiring, forex/ tree.

## 7. Rollout plan

**Pre-work: pytest collection timeout.** Proper fix only (the user rejected the `addopts` shortcut). Refactor test imports so collection is hermetic: move engine-initializing imports inside test fixtures/functions, lazy-import Nautilus in module-level code paths that tests hit during collection, and verify `pytest --collect-only` returns ≥389 items in <5s. If implementation surfaces a genuine blocker (e.g. a third-party import that initializes at import time), escalate before falling back.

**PR-by-PR:**

1. **PR 1 — Test harness + characterization.** Hermetic pytest collection fix. Add tests for `cli/live.py`, `cli/strategies.py`, `live/runner.py`, `data/providers.py`, `data/download.py`, `backtest/runner.py`, plus the two make-target smoke tests (fixture-based). Land the opt-in Binance testnet smoke job wired behind `BINANCE_TESTNET_API_KEY`. No production code changes except minimal lazy-import adjustments. Tree stays green.
2. **PR 2 — Delete `backtest_demo.py` + Makefile cleanup.** Depends only on PR 1. Tree stays green.
3. **PR 3 — Extract `cli/_common.py`.** Move `_ensure_project_root_on_path` and `_resolve_strategy_paths`, update imports, remove cross-module dependency from `cli/live.py` → `cli/backtest.py`. Tests from PR 1 are the safety net. Tree stays green.
4. **PR 4 — Split `kronos/backtest.py`.** Introduce `kronos/backtest_config.py`, shrink `kronos/backtest.py` to ≤80 LOC (still concrete, not yet subclassing `BacktestRunner`). Add a test that loads the config and runs one bar of data. Tree stays green.
5. **PR 5 — `StrategyConfigBuilder` Protocol + registry (the big one).** Introduce `cli/_strategy_configs.py` with Protocol and five concrete builders. Replace string-match branches in `cli/live.py` and `cli/backtest.py` with registry dispatch. Builder unit tests per strategy. Snapshot tests from PR 1 must still pass byte-for-byte on the built configs. Tree stays green.
6. **PR 6 — `BacktestRunner` base + kronos migration.** Introduce `backtest/runner_base.py` with the abstract base; migrate `backtest/runner.py` and `strategies/crypto/kronos/backtest.py` onto it. Parallel code path collapses into one. Tree stays green.
7. **PR 7 — `_grid_math.py` extraction.** Lift pure grid-computation helpers out of `timesfm_grid.py` (538 LOC) into `strategies/crypto/_grid_math.py`. Add unit tests for the helpers. If the diff reveals `grid_bot.py` duplicates any helper, route that through `_grid_math.py` in the same PR; otherwise leave it. Existing `test_timesfm_grid.py` tests must pass unchanged. Tree stays green.
8. **PR 8 — Resolve dynamic import in `cli/live.py`.** Small cleanup; only lands if PR 1 / PR 5 tests still pass statically.

Each PR is self-contained, tested, and revertable. The competition path is unaffected because no strategy class/module names move.

## 8. Resolved decisions

The five questions posed in the draft were resolved by the user on 2026-04-17:

1. **Scope → 1b.** Extend to Option 2 now (Protocol-based `StrategyConfigBuilder` registry + `BacktestRunner` base). Option 3 (DI container) stays out of scope.
2. **Test data source → 2c.** Parquet fixtures under `tests/fixtures/` are the default, plus an opt-in Binance testnet smoke job in CI gated by `BINANCE_TESTNET_API_KEY`.
3. **Pytest collection fix → 3a.** Proper hermetic-import refactor. No `addopts` shortcut unless implementation surfaces a genuine blocker (escalate first).
4. **`timesfm_grid.py` grid-math extraction → 4b.** In scope for this sub-project. New `strategies/crypto/_grid_math.py` helper module, landed as PR 7.
5. **`kronos/paper_trade.py` → 5b.** Out of scope — moved to sub-project B (live-trading track). Reflected in §2 non-goals.
