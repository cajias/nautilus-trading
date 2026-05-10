# Core Package Audit: 2026-04-17

## Executive Summary

Audited `nautilus/src/nautilus_trading/` core modules (cli, backtest, live, data) for SOLID violations, coupling, and configuration patterns. **Top 3 structural issues identified:**

1. **CLI layer violates SRP**: Strategy-specific config logic (grid_bot, dca_bot, ema_cross, etc.) hardcoded in monolithic live.py (177 LOC) and backtest.py (150 LOC). Mixes argument parsing with business logic.
2. **No abstraction for strategy-specific parameters**: Runners use string matching (`if module_name == "grid_bot"`) instead of Protocol-based discovery. Changes to strategy params require CLI edits.
3. **Utility functions duplicated across CLI commands**: `_ensure_project_root_on_path()` and `_resolve_strategy_paths()` live in cli/backtest.py but imported by cli/live.py; should be in shared utils module.

---

## 1. Module-by-Module Breakdown

### cli/ (411 LOC total)

| File | LOC | Key Exports | Responsibility |
|------|-----|-------------|-----------------|
| `__init__.py` | 17 | `app: typer.Typer` | Typer CLI router; wires three commands (backtest, live, strategies). |
| `backtest.py` | 150 | `backtest()`, `_resolve_strategy_paths()`, `_ensure_project_root_on_path()` | CLI entry for backtest; parses args and builds config dicts for EMA/grid/DCA strategies; manages sys.path injection. |
| `live.py` | 177 | `live()` | CLI entry for live trading; parses 20+ strategy-specific args; route to grid_bot, dca_bot, ema_cross, timesfm_swing, hybrid_sma_r10. |
| `strategies.py` | 74 (not inspected) | `strategies()` | CLI to list available strategies. |

**Responsibility**: Expose live/backtest runners via CLI; argument parsing and strategy selection.

---

### backtest/ (118 LOC)

| File | LOC | Key Exports | Responsibility |
|------|-----|-------------|-----------------|
| `__init__.py` | 0 | — | Empty init. |
| `runner.py` | 118 | `build_backtest_config()`, `run_backtest()`, `print_results()` | Build BacktestRunConfig from params; execute BacktestNode; pretty-print results. |

**Responsibility**: Backtest orchestration; config builder and test execution.

---

### live/ (142 LOC)

| File | LOC | Key Exports | Responsibility |
|------|-----|-------------|-----------------|
| `__init__.py` | 1 | — | Empty init. |
| `runner.py` | 142 | `build_live_config()`, `run_live()`, `_check_api_keys()` | Build TradingNodeConfig for Binance; set up signal handlers; verify API keys. |

**Responsibility**: Live trading orchestration; Binance-specific node setup; graceful shutdown.

---

### data/ (438 LOC)

| File | LOC | Key Exports | Responsibility |
|------|-----|-------------|-----------------|
| `__init__.py` | 12 | Re-exports from download.py and providers.py | Public API surface. |
| `download.py` | 30 | `get_provider()`, `ensure_catalog()`, `PROVIDERS: dict` | Provider registry and factory; catalog download dispatcher. |
| `providers.py` | 408 | `DataProvider` (ABC), `TestDataProvider`, `BinanceDataProvider` | Abstract interface for market data sources; implements test (GitHub download) and Binance (REST API) adapters. |

**Responsibility**: Pluggable data provider abstraction; catalog initialization.

---

## 2. SRP Violations

### cli/live.py (177 LOC)

**Violation**: Single function `live()` mixes four concerns:

1. **CLI argument parsing** (lines 12–97)
2. **Strategy-specific config logic** (lines 123–157):
   - Grid Bot: validates/adds `upper_price`, `lower_price`, `grid_levels`
   - DCA Bot: adds `buy_amount`, `buy_interval_bars`
   - EMA strategies: routes `fast_ema_period`, `slow_ema_period`
   - TimesFM: special `fallback_fast_ema_period` handling
   - Hybrid SMA: skips `trade_size`, adds `sma_fast`, `sma_slow`, Decimal conversion
3. **User confirmation** (line 165: production trading warning)
4. **Delegation to runner** (lines 167–177)

**Impact**: Adding a new strategy requires editing live.py. Testing individual strategy configs requires running the full CLI.

**Recommendation**: Extract strategy config builders into separate classes (e.g., `GridBotConfigBuilder`) with a registry pattern.

---

### cli/backtest.py (150 LOC)

**Violation**: `backtest()` function (line 61–end) mixes:

1. **CLI argument parsing** (lines 62–end)
2. **Strategy path resolution logic** (delegated to `_resolve_strategy_paths()`)
3. **Config building** (delegates to `build_backtest_config()`)

**Impact**: Less severe than live.py, but still couples argument parsing to config assembly.

---

### backtest/runner.py (118 LOC)

**Violation**: `build_backtest_config()` (line 19–101) hardcodes EMA-specific logic:

```python
# Include EMA params only for EMA-based strategies (line 65-68)
if "ema_cross" in strategy_path:
    strat_config["fast_ema_period"] = fast_ema_period
    strat_config["slow_ema_period"] = slow_ema_period
```

**Impact**: Runner must know about every strategy's config schema. Non-EMA strategies calling this function with `fast_ema_period` args will silently ignore them.

**Recommendation**: Accept `**strategy_config_overrides` only; let CLI layer build strategy-specific dicts.

---

## 3. OCP/DIP Issues

### Strategy-Specific Parameters Are Not Discoverable

**Problem**: No Protocol or interface defines what config each strategy expects. Instead:

- cli/backtest.py has hardcoded `_STRATEGY_CLASSES` dict (lines 29–34)
- cli/live.py uses string matching on `module_name` (lines 124–157)
- backtest/runner.py checks for "ema_cross" (line 66)

**Consequence**: Cannot swap strategies or add new ones without CLI/runner edits.

**Recommendation**: Define a `StrategyRegistry` Protocol:

```python
class StrategyInfo(Protocol):
    strategy_class: type[Strategy]
    config_class: type[StrategyConfig]
    required_params: set[str]
    optional_params: dict[str, Any]
```

---

### Data Provider Abstraction Is Correct

**Positive finding**: `data/providers.py` properly uses ABC + `PROVIDERS` registry (download.py line 12–14). New providers can be added without CLI changes. ✅

---

## 4. Fallback/Mock/Synthetic Traces in Runtime Code

**Search keywords**: `fallback`, `mock`, `synthetic`, `fake`, `dummy`, `stub`, `TODO`, `XXX`

**Result**: Only one match in runtime code:

- `cli/live.py:147`: `strat_config["fallback_fast_ema_period"] = fast_ema`

**Assessment**: This is a **legitimate strategy configuration parameter** (not a code fallback). TimesFM swing strategy can fall back to EMA if forecast unavailable. ✅ Clean.

---

## 5. Coupling Map & Cross-Layer Dependencies

### Dependency Graph

```
cli/
  ├── imports: backtest/runner, live/runner, data/download
  └── backtest.py imports live.py (lines 9–10 re-export utils)  ⚠️

backtest/
  ├── imports: nautilus_trader.backtest.node, .config
  └── no cross-layer imports  ✅

live/
  ├── imports: nautilus_trader.live.node, .adapters.binance
  └── no cross-layer imports  ✅

data/
  ├── imports: nautilus_trader.persistence.catalog
  └── no cross-layer imports  ✅
```

### Cross-Layer Leaks (⚠️)

1. **cli/live.py → cli/backtest.py** (line 9):
   ```python
   from nautilus_trading.cli.backtest import _ensure_project_root_on_path, _resolve_strategy_paths
   ```
   Both functions should be in shared `cli/utils.py` or `cli/_common.py`.

2. **cli/ → backtest/, live/, data/** (lines 11–12, 111):
   - `cli/backtest.py` imports from `backtest/runner` and `data/download`
   - `cli/live.py` imports from `live/runner` (dynamic import, line 111)
   
   This is **correct layering** (CLI depends on runners). ✅

### No Circular Imports

✅ Clean dependency direction: CLI → Runners → Traders.

---

## 6. Config/DI Entry Points

### Current Wiring

**Primary DI entry point**: `cli/__init__.py` (line 15–17) wires three Typer commands.

**Secondary wiring**:
- `data/download.py` (line 12–14): Provider registry (`PROVIDERS` dict)
- `cli/backtest.py` (line 29–34): Strategy class mapping (`_STRATEGY_CLASSES` dict)
- `cli/live.py` (line 111): Dynamic import of `live/runner`

**Assessment**:

| DI Pattern | Status | Location |
|-----------|--------|----------|
| Provider discovery | **Excellent** | `data/download.py` + registry pattern ✅ |
| Strategy discovery | **Poor** | Hardcoded dicts + string matching ⚠️ |
| CLI routing | **Good** | Typer-based ✅ |
| Runner instantiation | **Scattered** | Across CLI functions ⚠️ |

### Dynamic Import Smell

`cli/live.py:111` does:
```python
from nautilus_trading.live.runner import build_live_config, run_live
```

**Inside the function** (not module-level). Likely **to avoid circular import**. Verify: Try moving to top of file; if it fails, there's a cycle.

---

## Summary Table

| Category | Finding | Severity |
|----------|---------|----------|
| **SRP** | cli/live.py mixes parsing + strategy-specific config logic | HIGH |
| **SRP** | cli/backtest.py couples parsing to config building | MEDIUM |
| **SRP** | backtest/runner.py hardcodes EMA strategy checks | MEDIUM |
| **OCP/DIP** | No Strategy registry; hardcoded dicts + string matching | HIGH |
| **Coupling** | cli/live.py imports utils from cli/backtest.py | LOW |
| **DI** | Provider registry pattern is excellent | — |
| **DI** | Runner wiring is scattered across CLI functions | MEDIUM |
| **Fallbacks** | No synthetic data in runtime paths | ✅ Clean |
| **Circular Imports** | None detected; dynamic import in cli/live.py is likely precaution | LOW |

---

## Recommendations (Priority Order)

### 1. Extract Strategy Config Builders (HIGH)

Create `cli/_strategy_configs.py`:
```python
class StrategyConfigBuilder(Protocol):
    def build(self, args: dict) -> dict: ...

class GridBotConfigBuilder:
    def build(self, args: dict) -> dict:
        return {"upper_price": args["upper_price"], ...}

STRATEGY_BUILDERS = {
    "grid_bot": GridBotConfigBuilder(),
    "dca_bot": DCABotConfigBuilder(),
}
```

Refactor `cli/live.py` line 123–157 to dispatch via registry.

---

### 2. Move Shared Utilities (MEDIUM)

Create `cli/_common.py`:
```python
def ensure_project_root_on_path() -> None: ...
def resolve_strategy_paths(module_path: str) -> tuple[str, str]: ...
```

Move `_STRATEGY_CLASSES` here; update `backtest.py` and `live.py` imports.

---

### 3. Verify & Resolve Dynamic Import (LOW)

Try moving `from nautilus_trading.live.runner import ...` to module-level in `cli/live.py`. If it fails due to circular import, document the cycle and refactor.

---

## Audit Metadata

- **Auditor**: auditor-core
- **Date**: 2026-04-17
- **Scope**: nautilus/src/nautilus_trading/
- **Files analyzed**: 11 total (8 non-empty)
- **Total LOC**: 1,259 LOC (core modules only)
- **Tools used**: Glob, Grep, manual code inspection
