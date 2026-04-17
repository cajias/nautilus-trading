# Crypto Strategies Audit Report
**Date:** 2026-04-17  
**Auditor:** auditor-strategies  
**Scope:** `strategies/crypto/` and `strategies/crypto/kronos/`

---

## 1. File Inventory

### strategies/crypto/

| File | LOC | Purpose | External Deps | Test | Makefile | Notebook |
|------|-----|---------|-----------------|------|----------|----------|
| `__init__.py` | 1 | Package marker | - | - | - | - |
| `backtest_demo.py` | 341 | Demo backtest runner (hardcoded EMA strategy) | timesfm, kronos | ❌ NO | ❌ NO | - |
| `dca_bot.py` | 216 | Dollar-cost average bot with RSI timing | - | ✅ YES | ✅ YES | - |
| `grid_bot.py` | 247 | EMA-based grid trader with limit orders | - | ✅ YES | ✅ YES | - |
| `hybrid_sma_r10.py` | 279 | Dual SMA (fast/slow) cross, two independent subs | - | ✅ YES | ✅ YES | - |
| `risk_guard.py` | 225 | Mixin: max drawdown & position size limits | - | ✅ YES | ❌ NO (used by 6 others) | - |
| `rvs_data.py` | 33 | Data struct: reddit/whale/timesfm signals | - | 🔗 (via rvs_swing) | ❌ NO | - |
| `rvs_swing.py` | 267 | Swing trader: TimesFM + whale + reddit sentiment | timesfm | ✅ YES | - | ✅ YES (186 lines) |
| `shock_guard.py` | 360 | Multi-regime monitor: volatility hysteresis + shock detection | - | ✅ YES | - | ✅ YES (198 lines) |
| `timesfm_grid.py` | 538 | TimesFM P10/P90 grid with maker limit orders | timesfm, torch | ✅ YES | ✅ YES | ✅ YES (491 lines) |
| `timesfm_swing.py` | 317 | TimesFM P50 swing signal overlay | timesfm, torch | ✅ YES | ✅ YES | - |

### strategies/crypto/kronos/

| File | LOC | Purpose | External Deps | Test | Makefile | Mixed Concerns |
|------|-----|---------|-----------------|------|----------|-----------------|
| `__init__.py` | 41 | Package exports | - | 🔗 (via test_kronos_strategy) | - | - |
| `actor.py` | 427 | ML actor wrapper (TimesFM P50/P10/P90 forecasts) | torch, timesfm | 🔗 | - | ⚠️ Actor + data prep |
| `backtest.py` | 370 | **Standalone backtest runner** | torch, kronos | ❌ NO | ✅ YES (dedicated target) | 🚩 **MIXING**: Contains BacktestEngine instantiation |
| `data.py` | 87 | KronosSignal dataclass | - | 🔗 | - | - |
| `paper_trade.py` | 187 | Live paper trading harness | torch, kronos | ❌ NO | ✅ YES (dedicated target) | ⚠️ Paper trade harness, not strategy |
| `strategy.py` | 369 | Core strategy: uses KronosActor for signals | torch, timesfm | ✅ YES | - | ⚠️ Contains backtest engine config |

**Total LOC (crypto):** 4,305  
**Total LOC (kronos):** 1,481

---

## 2. Duplicates & Overlaps

### Group A: TimesFM-Based Forecasting Strategies

**Overlap:** Both use TimesFM predictions for signal generation.

| Strategy | Type | LOC | Last Update | Notebook | Distinct Use Case |
|----------|------|-----|-------------|----------|-------------------|
| `timesfm_grid.py` | Grid trader | 538 | 2026-04-14 | 491 lines (detailed) | P10/P90 confidence gate, 8-10 grid levels, maker-only |
| `timesfm_swing.py` | Swing signal | 317 | 2026-04-11 | None | Standalone overlay, CPU-only, <500 annually |
| `rvs_swing.py` | Swing + sentiment | 267 | 2026-04-11 | 186 lines | TimesFM + whale + reddit (multi-signal fusion) |

**Recommendation:** All three are distinct enough. `timesfm_grid` is most mature (newest, largest notebook). `rvs_swing` adds non-model data. `timesfm_swing` is simpler overlay. Consider whether all three should co-exist, or consolidate swing strategies.

### Group B: RiskGuard-Based Strategies

**Overlap:** All 7 strategies below inherit `RiskGuard` for position size & drawdown limits.

```
RiskGuard (base mixin)
├── dca_bot (216 LOC)           - DCA timing via RSI
├── grid_bot (247 LOC)          - EMA-based grid
├── rvs_swing (267 LOC)         - TimesFM + sentiment swing
├── shock_guard (360 LOC)       - Regime hysteresis monitor
├── timesfm_grid (538 LOC)      - TimesFM grid
└── timesfm_swing (317 LOC)     - TimesFM swing overlay
```

This is healthy code reuse (inheritance, not duplication). No action needed.

### Group C: Grid-Based Traders

**Overlap:** Two variants of grid trading.

| Strategy | Type | Signal | Levels | Status |
|----------|------|--------|--------|--------|
| `grid_bot.py` | EMA grid | EMA crossover | N (configurable) | Tested, in Makefile |
| `timesfm_grid.py` | ML grid | TimesFM P10/P90 | 8-10 (fixed) | Tested, in Makefile, newest |

**Distinct:** `grid_bot` uses technical indicators; `timesfm_grid` uses ML predictions + confidence gate. Both valid approaches for $500/month budget.

### Group D: Kronos (Separate Architecture)

**5 files, 1,481 LOC, uses torch + TimesFM exclusively.**  
Isolated subsystem with dedicated backtest and paper-trade runners. Not duplicating main strategies; alternative architecture.

---

## 3. Dead Code Candidates

### 🚩 STRONG: `backtest_demo.py` (341 LOC)

- **Status:** Demo only
- **Tests:** None
- **Makefile:** Not referenced by strategy name (only generic `backtest-crypto` target)
- **Imports:** Hard-coded EMA strategy, no generalization
- **Recommendation:** **DELETE** — Obsolete demo. Users should use:
  - `make backtest-crypto STRATEGY=crypto.grid_bot`
  - Or Jupyter notebooks for exploration

### ⚠️ WEAK: `rvs_data.py` (33 LOC)

- **Status:** Data structure only
- **Tests:** Not directly; only used by `test_rvs_swing.py`
- **Imports:** Imported by `rvs_swing.py` (required)
- **Recommendation:** **KEEP** — Tightly coupled to `rvs_swing.py` strategy. No independent value.

---

## 4. SOLID Red Flags (Code Quality)

### 🚩 CRITICAL: Files >300 LOC

| File | LOC | Concern |
|------|-----|---------|
| `timesfm_grid.py` | 538 | Largest; check for refactorable segments (grid logic, ML signal, risk logic) |
| `kronos/actor.py` | 427 | ML actor wrapper; complex forecast logic—ensure testable |
| `kronos/backtest.py` | 370 | **MIXING CONCERNS**: Backtest runner + engine config (see below) |
| `kronos/strategy.py` | 369 | High coupling to KronosActor; backtest config embedded |
| `shock_guard.py` | 360 | Multi-regime hysteresis; single responsibility? |
| `timesfm_swing.py` | 317 | Signal overlay; acceptable size for feature richness |

### 🚩 **CRITICAL: Mixing Concerns**

#### `kronos/backtest.py` (370 LOC)
- Runs **BacktestEngine instantiation** directly (not just strategy)
- Contains engine config, venue setup, data catalog logic
- **Should be:** Configuration file (YAML) + minimal runner
- **Impact:** Hard to reuse config, tightly coupled to demo

**Example fix:**
```python
# CURRENT (bad): backtest.py contains all logic
engine = BacktestEngine(...)
engine.add_venue(...)
engine.run()

# BETTER: separate config.yaml + minimal runner
config = load_yaml('kronos_backtest_config.yaml')
engine = BacktestEngine.from_config(config)
engine.run()
```

#### `kronos/strategy.py` (369 LOC)
- Contains strategy logic **AND** references to backtest engine initialization
- Should only define `Strategy` subclass
- **Action:** Extract backtest config to `kronos/backtest.py` properly

### ⚠️ **HIGH: Multiple Responsibilities**

#### `timesfm_grid.py` (538 LOC)
- Strategy definition ✅
- Risk guard inheritance ✅
- Grid level math ✅
- TimesFM forecast handling ✅
- Order management ✅
- Calibration gate logic ✅

**Recommendation:** Refactor grid math → `GridLevels` utility class (reusable by `grid_bot.py`).

---

## 5. Test Coverage Summary

### ✅ Well-Tested

| Test File | Coverage | Strategy |
|-----------|----------|----------|
| `test_dca_bot.py` | Full | dca_bot |
| `test_grid_bot.py` | Full | grid_bot |
| `test_hybrid_sma_r10.py` | Full | hybrid_sma_r10 |
| `test_kronos_strategy.py` | Full | kronos.strategy + actor |
| `test_risk_guard.py` | Comprehensive | risk_guard (tests 6 strategies) |
| `test_rvs_swing.py` | Full | rvs_swing, rvs_data |
| `test_shock_guard.py` | Full | shock_guard |
| `test_timesfm_grid.py` | Full | timesfm_grid |
| `test_timesfm_swing.py` | Full | timesfm_swing |

### ❌ Not Tested

| File | Reason | Risk |
|------|--------|------|
| `backtest_demo.py` | Demo only | **HIGH** — should be deleted (see §3) |
| `kronos/backtest.py` | Backtest runner (e.g., script) | **MED** — integration tested via `make backtest-kronos` |
| `kronos/paper_trade.py` | Paper trading harness (live-like) | **MED** — integration tested via `make paper-trade-kronos` |

---

## 6. External Dependencies

### By Feature

| Feature | Files | Tools | Annual Cost Estimate |
|---------|-------|-------|----------------------|
| TimesFM ML | timesfm_{grid,swing}, rvs_swing, rvs_data | torch, timesfm | $12-48 (CPU) |
| Kronos system | kronos/* | torch, timesfm, kronos | $0 (internal) |
| Traditional TA | dca_bot, grid_bot, hybrid_sma_r10, shock_guard | nautilus only | $0 |

---

## 7. Summary & Actions

### ✅ Healthy Patterns
- Good inheritance hierarchy (RiskGuard mixin)
- Distinct strategies for different risk profiles
- Comprehensive test coverage (9/11 strategies)
- Notebooks for exploration

### 🚩 Issues to Fix

| Priority | Issue | Action | Effort |
|----------|-------|--------|--------|
| 🔴 P0 | `backtest_demo.py` is dead code | Delete (341 LOC cleanup) | 1h |
| 🔴 P0 | `kronos/backtest.py` mixes concerns | Refactor to separate config (YAML) + runner | 4h |
| 🟠 P1 | `timesfm_grid.py` too large (538 LOC) | Extract grid logic → reusable module | 2h |
| 🟠 P1 | `shock_guard.py` high complexity (360 LOC) | Review regime logic—split if >5 methods | 2h |
| 🟡 P2 | Duplicate grid strategies | Evaluate consolidation vs. co-existence | 1h (decision) |

---

## Appendix: File Purposes

```
strategies/crypto/
├── SIGNAL GENERATORS (timesfm, sentiment)
│   ├── timesfm_grid.py       ML grid (P10/P90 confidence gate)
│   ├── timesfm_swing.py      ML swing (standalone overlay)
│   ├── rvs_swing.py          TimesFM + whale + reddit (sentiment fusion)
│   └── rvs_data.py           (data struct for rvs_swing)
│
├── EXECUTION TACTICS (position management)
│   ├── grid_bot.py           EMA-based grid (configurable levels)
│   ├── dca_bot.py            DCA with RSI timing
│   └── hybrid_sma_r10.py     Dual SMA cross (long-only)
│
├── RISK/REGIME MONITORING
│   ├── risk_guard.py         Mixin: max drawdown + position size limits
│   └── shock_guard.py        Regime hysteresis + shock detection
│
├── UTILITY/DEMO
│   └── backtest_demo.py      (DEAD CODE — delete)
│
└── kronos/                   Alternative architecture (torch + TimesFM)
    ├── strategy.py           Core strategy (uses KronosActor)
    ├── actor.py              ML actor (forecast wrapper)
    ├── backtest.py           Standalone backtest runner (concerns mixed)
    ├── paper_trade.py        Live paper trading harness
    ├── data.py               KronosSignal dataclass
    └── __init__.py           Exports
```

---

**Generated:** 2026-04-17 — [End of Audit]
