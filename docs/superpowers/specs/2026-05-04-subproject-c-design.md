# Sub-project C — External Strategy Plugin Surface

**Status:** APPROVED 2026-05-04 (brainstorming).
**Supersedes:** GH issue #25 ("Competition platform — submission contract, validators, leaderboard").

## Context

Sub-project B.5 (shipped 2026-04-30, PRs #40–#44) consolidated strategy execution under one `Strategy` class + three runners (Backtest / PaperTrade / Live) sharing a `StrategySpec` registry at `nautilus/src/nautilus_trading/cli/_strategy_specs.py`. Today that registry is a hardcoded dict — external strategy authors cannot add new strategies without editing this repo.

The original sub-project C scope (competition platform with submission contract, validators, leaderboard, eval harness) is **withdrawn**. Competitions will live in a separate repo. This repo's scope is reframed: become a **runtime tool** that loads external strategies via a stable plugin surface.

## Goal

Make this repo a tool that runs external strategies via Python entry-point discovery, without hardcoded registration. The 9 in-repo strategies (`ema_cross`, `grid_bot`, `dca_bot`, `timesfm_swing`, `hybrid_sma_r10`, `timesfm_grid`, `rvs_swing`, `shock_guard`, `kronos`) become the reference implementation and proof-of-contract — they migrate to entry-point registration in this sub-project, and stay in-repo for now.

## Non-goals (explicitly out of scope)

- Building a competition platform (validators, leaderboard, eval harness, round configs). Lives in a separate repo.
- Migrating the 9 in-repo strategies *out* of this repo. Tracked as a future sub-project once the external surface is proven.
- Real-money execution. Already deferred per the 2026-04-21 directive.
- Hosted infra, web UIs, public-facing endpoints.
- Sandboxing or untrusted-code protections — solo use, all installed strategies are trusted.

## Architecture

### Strategy registration via Python entry-points

Strategies are registered via the standard Python entry-points mechanism (`importlib.metadata`).

**Entry-point group:** `nautilus_trading.strategies`

**Each entry-point** maps a strategy name (the value used in YAML's `strategy:` field) to a fully-qualified import path resolving to a `StrategySpec` constant or factory.

Example external repo:
```toml
# external-strategies/pyproject.toml
[project.entry-points."nautilus_trading.strategies"]
my_swing = "external_strategies.my_swing:STRATEGY_SPEC"
```

After `pip install -e .` in the worktree, `nt backtest --config configs/backtest/my_swing.yaml` works. No code changes in this repo.

### `StrategySpec` shape (unchanged from B.5)

```python
@dataclass(frozen=True)
class StrategySpec:
    name: str
    builder: StrategyConfigBuilder
    strategy_path: str
    config_path: str
    actor_specs: tuple[ActorSpec, ...] = ()
```

The `builder` field is preserved. It is the adapter seam that lets this repo's internal `StrategyConfig` shapes evolve without breaking external strategies. External strategies must implement a `StrategyConfigBuilder` Protocol class — same contract as the 9 reference impls.

### Discovery at startup

`cli/_strategy_specs.py` is refactored from a hardcoded dict into a discovery + caching module:

```python
def _discover_strategy_specs() -> dict[str, StrategySpec]:
    specs: dict[str, StrategySpec] = {}
    sources: dict[str, str] = {}  # spec.name -> source package
    for ep in importlib.metadata.entry_points(group="nautilus_trading.strategies"):
        spec = ep.load()
        if callable(spec):
            spec = spec()  # support factory functions
        if spec.name in specs:
            raise RuntimeError(
                f"Duplicate strategy registration: '{spec.name}' "
                f"declared by both '{sources[spec.name]}' and '{ep.dist.name}'. "
                f"Uninstall or rename one to resolve."
            )
        specs[spec.name] = spec
        sources[spec.name] = ep.dist.name
    return specs

# Discovery happens at module import; cached for the process lifetime.
STRATEGY_SPECS: dict[str, StrategySpec] = _discover_strategy_specs()
```

Discovery happens at import time (cached for the process lifetime). Duplicate names across packages raise on import.

### This repo's own strategies

`nautilus/pyproject.toml` declares 9 entry-points under the same group:

```toml
[project.entry-points."nautilus_trading.strategies"]
ema_cross      = "strategies.forex.ema_cross:STRATEGY_SPEC"
grid_bot       = "strategies.crypto.grid_bot:STRATEGY_SPEC"
dca_bot        = "strategies.crypto.dca_bot:STRATEGY_SPEC"
timesfm_swing  = "strategies.crypto.timesfm_swing:STRATEGY_SPEC"
hybrid_sma_r10 = "strategies.crypto.hybrid_sma_r10:STRATEGY_SPEC"
timesfm_grid   = "strategies.crypto.timesfm_grid:STRATEGY_SPEC"
rvs_swing      = "strategies.crypto.rvs_swing:STRATEGY_SPEC"
shock_guard    = "strategies.crypto.shock_guard:STRATEGY_SPEC"
kronos         = "strategies.crypto.kronos:STRATEGY_SPEC"
```

Each strategy module gains a top-level `STRATEGY_SPEC = StrategySpec(...)` constant. The previous hardcoded `STRATEGY_SPECS` dict in `cli/_strategy_specs.py` is replaced with the discovered version.

### `nt strategies` CLI subcommand

A new debugging/discovery aid: `nt strategies` lists all discovered specs with their source package. Helpful when an external strategy isn't loading as expected.

```
$ nt strategies
ema_cross       (nautilus-trading)  → strategies.forex.ema_cross:EMACrossStrategy
grid_bot        (nautilus-trading)  → strategies.crypto.grid_bot:GridBotStrategy
...
my_swing        (my-strategies)     → external_strategies.my_swing:MySwingStrategy
```

## Components — files to change

**Create / modify (per-strategy):**
- For each of the 9 in-repo strategies: add `STRATEGY_SPEC = StrategySpec(...)` constant at module level.
- For `kronos` (a package, not a single module): export `STRATEGY_SPEC` from `strategies/crypto/kronos/__init__.py`.

**Modify:**
- `nautilus/pyproject.toml` — add the 9-entry `[project.entry-points."nautilus_trading.strategies"]` block.
- `nautilus/src/nautilus_trading/cli/_strategy_specs.py` — rewrite as discovery module (entry-point scan replaces hardcoded dict). The Protocol definitions, `StrategySpec`/`ActorSpec` dataclasses, and the 9 builder classes stay where they are.
- `nautilus/src/nautilus_trading/cli/__init__.py` — register the new `nt strategies` subcommand.

**Create:**
- `nautilus/src/nautilus_trading/cli/strategies.py` — implements `nt strategies` listing.
- `nautilus/tests/cli/test_strategies_command.py` — tests for the listing CLI.
- `nautilus/tests/cli/test_strategy_discovery.py` — tests entry-point discovery (using a synthetic external strategy fixture for the round-trip test).

**No changes to:**
- The 9 strategy `Strategy` / `StrategyConfig` / `Actor` classes.
- The 9 `*ConfigBuilder` classes (they keep working; they're now imported by the strategy modules' `STRATEGY_SPEC` constants instead of being aggregated in `_strategy_specs.py`).
- All YAMLs in `configs/{paper, backtest, live}/`.
- The runners (`PaperTradeStrategyRunner`, `BacktestStrategyRunner`, `LiveStrategyRunner`).
- The CLI dispatchers (`cli/paper_trade.py`, `cli/backtest.py`, `cli/live.py`).

## Backward compat

- All 9 existing YAMLs work unchanged.
- `nt {backtest, paper-trade, live} --config <yaml>` semantics unchanged.
- Existing tests that `from nautilus_trading.cli._strategy_specs import STRATEGY_SPECS` keep working — `STRATEGY_SPECS` is still exported, just derived from entry-points instead of hardcoded.

## Risks and mitigations

1. **Entry-point discovery cost.** `importlib.metadata.entry_points()` walks installed metadata. For a small dependency footprint this is microseconds; acceptable. Mitigation: cache the discovered dict at module-import time (already in the design).

2. **Discovery failure modes.** A broken external package's `STRATEGY_SPEC` (import error, malformed `StrategySpec`) would crash `nt` startup. *Decision:* fail-fast at startup with a clear error message naming the offending entry-point and source package. Solo use means a broken strategy is a real problem the user wants surfaced immediately, not silently skipped. The `nt strategies` listing also serves as the diagnostic surface — if discovery fails, the user runs that to see exactly which entry-point broke.

3. **Plugin name collisions.** Two installed packages registering the same strategy name. *Mitigation:* `_discover_strategy_specs()` records the source package (via `ep.dist.name`) alongside each spec and raises `RuntimeError` on duplicate names, naming both packages in the error so the user knows which to uninstall or rename.

4. **Editable install ergonomics.** External strategies must be `pip install -e .`'d alongside this repo for discovery to work. *Mitigation:* document in `docs/runbooks/external-strategies.md` (created in this sub-project). Solo use is well-served by editable installs.

## Future migration path (out of scope for sub-project C)

A subsequent cleanup PR (or separate sub-project) moves the 9 in-repo strategies to a sibling repo (e.g., `nautilus-trading-reference-strategies`). At that point this repo:
- Removes `strategies/` directory entirely.
- Removes the 9 entry-points from `nautilus/pyproject.toml`.
- Becomes purely the runtime tool — zero strategies of its own.

That migration is straightforward once the entry-point contract is proven by this sub-project.

## PR sequencing (rough — formal plan in writing-plans phase)

**PR 1 — Entry-point discovery + 9 in-repo strategies migrate**
- Add `STRATEGY_SPEC` constant to each of the 9 strategy modules.
- Rewrite `cli/_strategy_specs.py` as discovery module.
- Register the 9 entry-points in `nautilus/pyproject.toml`.
- Tests verify all 9 specs discovered correctly + duplicate-name detection.

**PR 2 — `nt strategies` CLI + external smoke test**
- Implement `nt strategies` listing.
- Bring in a tiny synthetic external strategy via test fixture (installed editable for the test session); verify discovery + dispatch round-trip.
- Add `docs/runbooks/external-strategies.md` documenting the contract.

Likely 2 PRs total — significantly smaller than B.5.

## Verification

- All existing tests pass (`cd nautilus && uv run python -m pytest`).
- `make lint` clean.
- `nt {backtest, paper-trade, live} --config <yaml>` works for all 9 strategies (proves discovery preserves dispatch).
- New `nt strategies` lists 9 strategies with `nautilus-trading` as the source package.
- External-strategy fixture test (PR 2) proves a third-party package can register and dispatch.

## Why this design

Entry-points are the standard Python plugin mechanism — battle-tested, no custom discovery code, transparent to `pip` users. Keeping the `StrategySpec` shape from B.5 unchanged means zero blast radius on existing runners and dispatchers; this sub-project is purely a registration-mechanism swap. The builder layer stays as the adapter seam (per user's call), so external strategies have a stable contract while this repo retains internal flexibility.
