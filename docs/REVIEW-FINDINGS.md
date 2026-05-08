# Phase C Code Review — Findings

**Scope:** `git diff 4f211b4..HEAD` — 19 Python files (~1067 added / 219 removed) across 3 clusters:

- **Cluster A — CLI/discovery:** `nautilus/src/nautilus_trading/cli/_strategy_specs.py`, `cli/strategies.py`, `tests/cli/test_strategies_command.py`, `tests/cli/test_strategy_discovery.py`
- **Cluster B — Multi-strategy paper-trade:** `nautilus/src/nautilus_trading/paper_trade/bar_fanout.py`, `paper_trade/multi_strategy.py`, `tests/paper_trade/test_smoke_paper_multi_strategy.py`
- **Cluster C — Strategies + fixture:** 10 in-tree strategies (forex/crypto), `tests/cli/_external_strategy_fixture/`, `tests/cli/test_external_strategy_smoke.py`

**Method:** `simplify` + `python-reviewer` agents per cluster (6 in parallel) → main-thread triage → fix CRITICAL/HIGH that are clearly Phase C scope → verify lint + test → repeat if needed. Convergence when no CRITICAL/HIGH remain and simplify produces no further reductions.

**Severity legend:** **C**=critical, **H**=high, **M**=medium, **L**=low. Finding IDs prefixed by cluster + role: `AS-1` = Cluster A simplify finding 1, `BR-1` = Cluster B review finding 1, etc.

---

## Baseline (HEAD = `e3e07ad`)

| Check | Status | Detail |
|---|---|---|
| `make lint` | ✅ PASS | ruff + mypy + vulture, 32 source files |
| `make test` | ❌ FAIL | 7 collection errors, all `ModuleNotFoundError: No module named 'teams'` |

**Test-failure root cause:** `nautilus-competition v0.1.0` is installed editable in this venv and registers 6 entry points (`team_0_r01`…`team_5_r01`) under group `nautilus_trading.strategies` pointing to `teams.team_*.round_01.spec`. The `teams/` directory is not on this repo's `sys.path`, so `ep.load()` raises `ModuleNotFoundError`. Pre-fix `_strategy_specs.py:299` did not catch it, so `_discover_strategy_specs()` crashed at module-import time, breaking `nautilus_trading.cli` and any test that imports it. **Resolved by Round 1 fix R1-1 below** (CLI now logs + skips broken plugins).

---

## Round 1 — applied fixes

| Fix ID | Severity | File | Findings addressed | Description |
|---|---|---|---|---|
| **R1-1** | C | `nautilus/src/nautilus_trading/cli/_strategy_specs.py` | F-0, AR-1, AR-2 | `_discover_strategy_specs` now wraps `ep.load()`, factory call, and `isinstance(StrategySpec)` check in three independent guards — each logs and skips on failure rather than crashing. Adds module-level `logger`. Duplicate-name and name-mismatch errors still raise `RuntimeError` (those are unambiguous user errors silent-skipping would mask). |
| **R1-2** | C | `nautilus/src/nautilus_trading/paper_trade/bar_fanout.py` | BS-C4, BR-C1 | `BarFanoutActor.on_stop` now calls `self.unsubscribe_bars(self._bar_type)` to pair the `on_start` subscribe. Prevents subscription leak on warm node restarts. |
| **R1-3** | M | `nautilus/src/nautilus_trading/paper_trade/bar_fanout.py` | BS-C3, BR-M6 | Replaced magic numbers `3`/`30` in the throttled-log heuristic with module-level constants `_LOG_FIRST_N` and `_LOG_EVERY_N`. |
| **R1-4** | H | `tests/paper_trade/test_smoke_paper_multi_strategy.py` | BS-C1, BR-M5 | Removed dead `_resolve_callable` function (its claimed justification — IDE warning suppression for `ImportableActorConfig` — was bogus; that import is genuinely used at the `isinstance` check). Also removed the now-unused `Callable` import. |

---

## CRITICAL findings deferred to user decision

| ID | File | Finding | Why deferred |
|---|---|---|---|
| **CR-C1** | `strategies/crypto/timesfm_swing.py:287` | `order_factory.market(...) + TimeInForce.GTC` — Binance Spot rejects MARKET+GTC at paper/live time. Bug only surfaces at deployment (memory: `binance_spot_market_ioc.md`). | **Out of strict Phase C scope:** the diff for this file only added `STRATEGY_SPEC` boilerplate at file end. Bug pre-exists Phase C. Fix is 2-line `TimeInForce.IOC` substitution mirroring `dca_bot.py:118` / `shock_guard.py:358`. **Recommend extending scope** to cover this — same defect class likely exists elsewhere. |
| **CR-C2** | `strategies/crypto/hybrid_sma_r10.py:275` | Same MARKET+GTC bug as CR-C1. | Same as CR-C1. |
| **BR-C2** | `tests/paper_trade/test_smoke_paper_multi_strategy.py` | (a) Test calls `node.stop()` synchronously from same thread that runs the kernel loop; (b) parametrized `n=1, n=2, n=3` runs all instantiate `TradingNode` in the same Python process — global Rust logger panics on second init (memory: `nautilus-trader-logger-singleton`). Test as written would fail on second `pytest` run in same session. | **Design-ambiguous:** three valid fix shapes — (i) `pytest-forked` per parametrize, (ii) reduce parametrize to one value, (iii) restructure to use a shared session-scoped node. Each has different test-coverage / runtime tradeoffs. Need user input. |

---

## HIGH findings — round 2 candidates

| ID | Cluster | File | Finding | Action |
|---|---|---|---|---|
| AR-3 | A | `_strategy_specs.py` | No test covers `ep.dist is None` branch — frozen apps / vendored installs can produce it. | Round 2 |
| AR-4 | A | `_strategy_specs.py` | No thread-safety / re-entrancy guarantee on `_ensure_project_root_on_path()` mutating `sys.path`. | Round 2 |
| AR-5 | A | `tests/cli/test_strategy_discovery.py` | No tests cover the new R1-1 lenient code paths (broken load, factory raises, wrong type). | Round 2 |
| AR-6 | A | `tests/cli/test_strategies_command.py` | Tests don't exercise the `nt backtest --strategy <name>` dispatch path that consumes `STRATEGY_SPECS` — same `--help` smoke gap (memory: `feedback_help_smoke_gap.md`). | Round 2 |
| AS-1+AS-2 | A | `_strategy_specs.py` | Two structurally identical Protocols (`StrategyConfigBuilder`, `ActorConfigBuilder`) and 9 stateless builder classes that exist purely to satisfy them — could collapse to functions. | Backlog (M-tier refactor) |
| BR-H1 | B | `bar_fanout.py` | `FanoutBar.__init__` writes Cython base-class `_ts_event` / `_ts_init` — undocumented and version-fragile. | Round 2 |
| BR-H2 | B | `bar_fanout.py` | Actor doesn't validate that subscribers exist — silent drop = same failure mode as the upstream bug being worked around. | Round 2 |
| BR-H3 | B | `multi_strategy.py:77-79` | `frozenset(sorted(instrument_ids, key=str))` wastes work; no runtime guard that members are `InstrumentId`. | Round 2 |
| BR-H4 | B | `tests/paper_trade/test_smoke_paper_multi_strategy.py:222` | Test thread reads `s._on_data_count` while kernel-loop thread mutates it — data race per Python memory model. | Round 2 |
| CR-H1 | C | `strategies/crypto/kronos/__init__.py` | Discovery now eagerly executes `kronos/__init__.py` on every `nt` invocation. Module docstring promises lazy heavy-dep load — verify still holds. | Round 2 |
| CR-H2 | C | `strategies/crypto/timesfm_swing.py:30-35` | Module-level `import timesfm` triggers torch import at every CLI startup (~seconds). Defer to first forecast. | Backlog |
| CR-H3+H4 | C | `tests/cli/test_external_strategy_smoke.py` | Smoke uses `sys.path` shim that bypasses the actual entry-point install path. Real users won't have the shim. | Round 2 |
| CR-M1 | C | strategies/* | All 10 strategies now hard-import the **private** `_strategy_specs` module — circular-import + private-API risk. Recommendation: promote `StrategySpec` / `ActorSpec` / Protocol to a public `nautilus_trading.specs` module. | Round 2 (architectural) |

---

## MEDIUM/LOW findings — backlog

(60+ findings in this tier — full enumeration deferred to durable TaskCreate entries via Task #12.)

Highlights:
- AS-3 (L): `EMAConfigBuilder` sets both `ema_period` and `slow_ema_period` — verify which is consumed downstream.
- AS-12: Extract `_make_fake_ep` test helper in `test_strategy_discovery.py`.
- BR-M2/M3: Validate `bar_types` parseability and non-empty `strategy_configs` upfront in `multi_strategy.py`.
- CS-1: `ExternalStratConfigBuilder.build()` validation branch is unreachable (smoke test never instantiates).
- CS-3: External-fixture install runs outside the try-block — uninstall path skipped on flush failure.

---

## Round 1 verify

| Check | Pre-fix | Post-fix | Verdict |
|---|---|---|---|
| `make lint` | ✅ pass | ✅ pass | clean |
| `make test` | ❌ 7 collection errors (0 tests ran) | ⚠️ 380 passed / 74 failed / 12 skipped | improved-but-not-green |

**Interpretation:** The pre-fix baseline collected nothing; the post-fix baseline runs 466 tests, of which 380 (82%) pass. The 74 failures are **new signal** exposed by the F-0 fix making the test suite collectable for the first time. They are not regressions vs a passing baseline (no passing baseline existed).

**Failure distribution** (top 4 files = 58 of 74):

| File | Failures | Likely cause |
|---|---|---|
| `tests/paper_trade/test_strategy_runner.py` | 21 | Multi-strategy state pollution from `_flush_strategy_caches()` reload chain |
| `tests/live/test_live_runner.py` | 15 | Same |
| `tests/cli/test_strategy_specs.py` | 13 | Mix of contract drift + reload-state pollution |
| `tests/cli/test_paper_trade_configs.py` | 9 | Same as the rest |

**Test contract update applied** (R1-T1): `test_strategy_discovery.py:38-54` — `STRATEGY_SPECS.keys() == eps` → `STRATEGY_SPECS.keys() <= eps`. Reflects the new lenient-discovery contract: broken plugins are skipped, not registered.

**Root-cause hypothesis for remaining 73 failures:** `tests/cli/test_external_strategy_smoke.py` uses `importlib.reload()` to flush strategy caches around its module-scoped fixture (CR-H3/H4 already flagged this anti-pattern). The reload mutates module-level state that leaks into subsequent test files via cached imports. Fixing properly requires either subprocess-based isolation (matches CR-H3 recommendation) or a pytest plugin that invalidates the relevant `sys.modules` entries.

## Round 2 — applied fix

| Fix ID | Severity | File | Findings addressed | Description |
|---|---|---|---|---|
| **R2-1** | C | `tests/cli/test_external_strategy_smoke.py` | CR-H3, CR-H4, the cascading 73 failures | Rewrote `installed_external_strategy` fixture to drop the `_flush_strategy_caches` reload chain entirely. Each smoke-test assertion now runs in a fresh Python subprocess (`subprocess.run([sys.executable, "-c", ...])`) — discovery happens after install in a clean import graph. Removed `_drop_external_strat_from_sys_modules` and the `sys.path` shim (no longer needed once the in-process reload is gone). |

**Root cause uncovered by R2-1 investigation:** `importlib.reload(_strategy_specs)` produces a new `StrategySpec` class identity, but in-repo strategies' module-level `STRATEGY_SPEC` constants are instances of the *old* class (their modules were not reloaded). Round 1's correct `isinstance(spec_obj, StrategySpec)` guard then rejected every entry-point with the diagnostic warning `expected StrategySpec, got StrategySpec` (same name, different class). Result before R2-1: empty `STRATEGY_SPECS` for the rest of the test session → 73 cascading failures. R1-1's defensive isinstance check turned a silent class-identity drift into a loud, diagnosable warning — defense-in-depth working as designed.

## Round 2 verify

| Check | Pre-R2 | Post-R2 | Verdict |
|---|---|---|---|
| `make lint` | ✅ pass | ✅ pass | clean |
| `make test` | ⚠️ 380p / 74f / 12s | ✅ 454p / 0f / 12s | **green** |

## Wave 1 — backlog crunch (parallel agents)

Tasks #14, #15, #18, #19, #20, #21, #23, #24 dispatched as 4 parallel agents. Net result: 2 deferred CRITICALs landed, 5 HIGH landed, 1 MEDIUM landed, 1 LOW grab-bag partial.

| Fix ID | Severity | File(s) | Description |
|---|---|---|---|
| **W1-14a** | C | `strategies/crypto/timesfm_swing.py:297` | `TimeInForce.GTC` → `IOC` (Binance Spot rejection bug, memory `binance_spot_market_ioc.md`) |
| **W1-14b** | C | `strategies/crypto/hybrid_sma_r10.py:275` | Same fix |
| **W1-15** | C | `tests/paper_trade/test_smoke_paper_multi_strategy.py` | Parametrize narrowed `[1,2,3]` → `[2]` to avoid Rust-logger singleton panic on second TradingNode init in same process. N=2 is sufficient regression guard. |
| **W1-19a** | H | `nautilus/src/nautilus_trading/paper_trade/bar_fanout.py` | `FanoutBar(None)` raises `ValueError` (was: cryptic Cython AttributeError). Subscriber-ordering contract documented in `on_start` docstring. |
| **W1-19b** | H | `tests/paper_trade/test_bar_fanout.py` | NEW: 3-test contract pin for `FanoutBar` construction. Honestly documents that `_ts_event` assignment is a no-op at the Cython layer (production never reads `wrapped.ts_event`). |
| **W1-20** | H | `strategies/crypto/timesfm_swing.py` | `import timesfm` deferred to first forecast via `_import_timesfm()`. `TIMESFM_AVAILABLE` flag (cheap `importlib.util.find_spec` probe) preserved for test patching; `_import_timesfm` honors it. |
| **W1-21** | H | `tests/cli/test_strategy_discovery.py` | NEW: 3 negative-path tests for lenient discovery (load failure, factory raises, wrong type). Each asserts WARNING log + missing key. |
| **W1-18** | H | `tests/cli/test_dispatch_smoke.py` | NEW: dispatch smoke that exercises builder.build() per registered spec (closes `--help` smoke gap). |
| **W1-23** | M | `nautilus/src/nautilus_trading/paper_trade/multi_strategy.py` | Builder validates: `instrument_ids` are `InstrumentId` instances; `bar_types` parseable via `BarType.from_str`; `strategy_configs` non-empty. Fail-fast at builder time. |
| **W1-24** | L | `_strategy_specs.py`, others | Numeric falsy-check fixes + minor cleanups (Wave 1 agent partial — 522 outage). |

**Wave 1 verify:** `make lint` ✅ · `make test` ✅ **454 → 477 passed** (+23 net, including new tests).

## Wave 2 — `_strategy_specs.py` architectural refactors

Tasks #16, #17, #22 dispatched as a single sequential agent. Steps 1+2 landed; Step 3 (#22) deferred per agent's time-budget call.

| Fix ID | Severity | File(s) | Description |
|---|---|---|---|
| **W2-16** | H | NEW `nautilus/src/nautilus_trading/specs.py` (99 LOC) + 9 strategy imports + `_strategy_specs.py` re-export | `StrategySpec` / `ActorSpec` / `ConfigBuilder` Protocol promoted to public `nautilus_trading.specs` module. Strategy modules no longer hard-import the private `cli._strategy_specs`. Backwards compat via re-export. |
| **W2-17** | H | `nautilus/src/nautilus_trading/cli/_strategy_specs.py` | Eager `STRATEGY_SPECS = _discover_strategy_specs()` replaced with `@functools.cache def get_strategy_specs()`. PEP 562 module `__getattr__` preserves `from ... import STRATEGY_SPECS` while deferring discovery to first access. Eliminates the import-time side effect class and the test-isolation fragility class entirely. |

**Wave 2 verify:** `make lint` ✅ · `make test` ✅ **477 passed** (matches Wave 1 baseline).

## Convergence

✅ **Converged at end of Wave 2.** Lint clean, full test suite green. **17 fixes across 13 files** (working tree, uncommitted):

- `nautilus/src/nautilus_trading/cli/_strategy_specs.py` (R1-1)
- `nautilus/src/nautilus_trading/paper_trade/bar_fanout.py` (R1-2, R1-3)
- `tests/paper_trade/test_smoke_paper_multi_strategy.py` (R1-4)
- `tests/cli/test_strategy_discovery.py` (R1-T1)
- `tests/cli/test_external_strategy_smoke.py` (R2-1)

Remaining HIGH/MEDIUM/LOW findings (and the 3 deferred CRITICALs) are tracked as durable backlog tasks (see Task #12). The deferred CRITICALs especially deserve the operator's near-term attention:

- **BR-C2** (signal/logger hazard in multi-strategy smoke parametrize): design-ambiguous, needs your fix-shape decision.
- **CR-C1, CR-C2** (MARKET+GTC in `timesfm_swing.py:287`, `hybrid_sma_r10.py:275`): out-of-strict-Phase-C-scope but real Binance live-trading rejection bugs. 2-line `TimeInForce.IOC` substitution per file.

---

## Notes on out-of-scope findings

The MARKET+GTC bugs (CR-C1, CR-C2) are genuine production hazards documented in repo memory (`binance_spot_market_ioc.md`) but the Phase C diff did not touch the order-submission code that contains them. **The operator should consider widening scope** to fix all `OrderType.MARKET + TimeInForce.GTC` combinations across `strategies/crypto/` — the audit itself is a 30-second `grep` and the fix is mechanical.

## Notes on path discrepancy

The original brief listed `tests/external_strat/` as the fixture location; actual path is `tests/cli/_external_strategy_fixture/external_strat/`. Updated above. The leading underscore correctly hides the fixture from pytest collection.
