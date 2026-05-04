# Sub-project C — External Strategy Plugin Surface — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make this repo a runtime tool that loads strategies via Python entry-point discovery, with the 9 in-repo strategies migrating to entry-point registration as the reference impl.

**Architecture:** Standard Python entry-points (`importlib.metadata`) under group `nautilus_trading.strategies`. Each strategy module exports a top-level `STRATEGY_SPEC = StrategySpec(...)` constant; an entry-point in `pyproject.toml` registers it by name. The previous hardcoded `STRATEGY_SPECS` dict in `cli/_strategy_specs.py` becomes a discovered dict (computed at module import). Spec at `docs/superpowers/specs/2026-05-04-subproject-c-design.md`.

**Tech Stack:** Python 3.13, uv, msgspec, NautilusTrader 1.224.0, Typer CLI, pytest, ruff/mypy/vulture.

**Spec:** `docs/superpowers/specs/2026-05-04-subproject-c-design.md` (APPROVED 2026-05-04).

**Branch convention.** Each PR on its own branch. PR 1 on `subproject-c/pr1-entry-point-discovery`. PR 2 on `subproject-c/pr2-strategies-cli-and-smoke`. This branch (`subproject-c/pr0-spec-and-plan`) is doc-only and lands first.

**Global commands.** Unless noted, all shell commands run from the repo root. Pytest runs from the `nautilus/` directory via `cd nautilus && uv run python -m pytest …` because that is where `pyproject.toml` + `.venv` live.

---

## File map

**Modified (PR 1):**
- `nautilus/pyproject.toml` — add `[project.entry-points."nautilus_trading.strategies"]` block.
- `nautilus/src/nautilus_trading/cli/_strategy_specs.py` — replace hardcoded `STRATEGY_SPECS` dict with discovery via `_discover_strategy_specs()`. Keep Protocols, dataclasses, all 9 builder classes, `_base()` helper.
- `strategies/forex/ema_cross.py`, `strategies/crypto/{grid_bot,dca_bot,timesfm_swing,hybrid_sma_r10,timesfm_grid,rvs_swing,shock_guard}.py` — add `STRATEGY_SPEC = StrategySpec(...)` constant at module level.
- `strategies/crypto/kronos/__init__.py` — export `STRATEGY_SPEC` (kronos is a package; spec lives at package level).

**Created (PR 1):**
- `nautilus/tests/cli/test_strategy_discovery.py` — new tests for entry-point discovery + duplicate detection.

**Modified (PR 2):**
- `nautilus/src/nautilus_trading/cli/__init__.py` — register `nt strategies` subcommand.

**Created (PR 2):**
- `nautilus/src/nautilus_trading/cli/strategies.py` — `nt strategies` listing implementation.
- `nautilus/tests/cli/test_strategies_command.py` — listing CLI tests.
- `nautilus/tests/cli/test_external_strategy_smoke.py` — third-party plugin smoke test using a synthetic fixture package.
- `nautilus/tests/cli/_external_strategy_fixture/` — synthetic external strategy package + minimal pyproject.toml for the smoke test.
- `docs/runbooks/external-strategies.md` — runbook for external strategy authors.

**Untouched:**
- All `Strategy` / `StrategyConfig` / `Actor` classes in `strategies/`.
- All YAMLs in `configs/{paper, backtest, live}/`.
- The runners (`PaperTradeStrategyRunner`, `BacktestStrategyRunner`, `LiveStrategyRunner`).
- The dispatch CLIs (`cli/paper_trade.py`, `cli/backtest.py`, `cli/live.py`).

---

## PR 1 — Entry-point discovery + 9 strategies migrate

Branch: `subproject-c/pr1-entry-point-discovery`. Base: `main`. Goal: replace the hardcoded `STRATEGY_SPECS` dict with discovery via Python entry-points; migrate the 9 in-repo strategies to register themselves as entry-points pointing at module-level `STRATEGY_SPEC` constants.

### Task 1.1 — RED: assert each strategy module exports `STRATEGY_SPEC`

**Files:**
- Create: `nautilus/tests/cli/test_strategy_discovery.py`

- [ ] **Step 1: Write the failing test**

```python
# nautilus/tests/cli/test_strategy_discovery.py
"""Sub-project C — entry-point-based strategy discovery."""

from __future__ import annotations

import importlib

import pytest

from nautilus_trading.cli._strategy_specs import StrategySpec

# All 9 in-repo strategies and their module paths.
STRATEGY_MODULES: dict[str, str] = {
    "ema_cross": "strategies.forex.ema_cross",
    "grid_bot": "strategies.crypto.grid_bot",
    "dca_bot": "strategies.crypto.dca_bot",
    "timesfm_swing": "strategies.crypto.timesfm_swing",
    "hybrid_sma_r10": "strategies.crypto.hybrid_sma_r10",
    "timesfm_grid": "strategies.crypto.timesfm_grid",
    "rvs_swing": "strategies.crypto.rvs_swing",
    "shock_guard": "strategies.crypto.shock_guard",
    "kronos": "strategies.crypto.kronos",
}


@pytest.mark.parametrize("name,module_path", list(STRATEGY_MODULES.items()))
def test_strategy_module_exports_strategy_spec(name: str, module_path: str) -> None:
    """Each in-repo strategy module exports a top-level STRATEGY_SPEC constant."""
    module = importlib.import_module(module_path)
    spec = getattr(module, "STRATEGY_SPEC", None)
    assert spec is not None, f"{module_path} must export STRATEGY_SPEC"
    assert isinstance(spec, StrategySpec), f"{module_path}.STRATEGY_SPEC must be a StrategySpec"
    assert spec.name == name, f"{module_path}.STRATEGY_SPEC.name must be '{name}', got '{spec.name}'"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd nautilus && uv run python -m pytest tests/cli/test_strategy_discovery.py::test_strategy_module_exports_strategy_spec -v
```

Expected: 9 failures with `AttributeError` (module has no `STRATEGY_SPEC` attribute) or `assert spec is not None`.

- [ ] **Step 3: Commit RED**

```bash
git add nautilus/tests/cli/test_strategy_discovery.py
git commit -m "test(c): RED — assert each strategy module exports STRATEGY_SPEC"
```

### Task 1.2 — GREEN: add `STRATEGY_SPEC` to `ema_cross`

**Files:**
- Modify: `strategies/forex/ema_cross.py`

- [ ] **Step 1: Read the current `ema_cross.py` and the existing `STRATEGY_SPECS["ema_cross"]` entry**

```bash
cat strategies/forex/ema_cross.py | head -40
grep -n -A6 '"ema_cross"' nautilus/src/nautilus_trading/cli/_strategy_specs.py
```

This shows the current strategy definition and the spec shape we need to mirror.

- [ ] **Step 2: Append `STRATEGY_SPEC` constant at the bottom of `ema_cross.py`**

```python
# Append to strategies/forex/ema_cross.py

from nautilus_trading.cli._strategy_specs import EMAConfigBuilder, StrategySpec

STRATEGY_SPEC = StrategySpec(
    name="ema_cross",
    builder=EMAConfigBuilder(),
    strategy_path="strategies.forex.ema_cross:EMACrossStrategy",
    config_path="strategies.forex.ema_cross:EMACrossConfig",
)
```

The exact `name`, `strategy_path`, and `config_path` values must match the existing entry in `cli/_strategy_specs.py:STRATEGY_SPECS["ema_cross"]` byte-for-byte.

- [ ] **Step 3: Run the parametric test for ema_cross**

```bash
cd nautilus && uv run python -m pytest tests/cli/test_strategy_discovery.py::test_strategy_module_exports_strategy_spec -v -k ema_cross
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add strategies/forex/ema_cross.py
git commit -m "feat(c): export STRATEGY_SPEC from strategies.forex.ema_cross"
```

### Tasks 1.3–1.9 — GREEN: add `STRATEGY_SPEC` to remaining 7 simple strategies

For each of `grid_bot`, `dca_bot`, `timesfm_swing`, `hybrid_sma_r10`, `timesfm_grid`, `rvs_swing`, `shock_guard`:

**Files:** Modify `strategies/crypto/<strategy>.py`.

Repeat Task 1.2's 4 steps, substituting:
- `<strategy>` for the strategy name.
- The matching `*ConfigBuilder` class from `cli/_strategy_specs.py` (e.g., `GridBotConfigBuilder`, `DCABotConfigBuilder`, `TimesFMConfigBuilder`, `HybridSMAConfigBuilder`, `TimesFMGridConfigBuilder`, `RVSSwingConfigBuilder`, `ShockGuardConfigBuilder`).
- `strategy_path = "strategies.crypto.<strategy>:<StrategyClass>"` (cross-check with `STRATEGY_SPECS["<strategy>"].strategy_path`).
- `config_path = "strategies.crypto.<strategy>:<ConfigClass>"`.

After each task, run `uv run python -m pytest tests/cli/test_strategy_discovery.py -v -k <strategy>` and commit `feat(c): export STRATEGY_SPEC from strategies.crypto.<strategy>`.

- [ ] **Task 1.3** — `grid_bot` (`GridBotConfigBuilder`)
- [ ] **Task 1.4** — `dca_bot` (`DCABotConfigBuilder`)
- [ ] **Task 1.5** — `timesfm_swing` (`TimesFMConfigBuilder`)
- [ ] **Task 1.6** — `hybrid_sma_r10` (`HybridSMAConfigBuilder`)
- [ ] **Task 1.7** — `timesfm_grid` (`TimesFMGridConfigBuilder`)
- [ ] **Task 1.8** — `rvs_swing` (`RVSSwingConfigBuilder`)
- [ ] **Task 1.9** — `shock_guard` (`ShockGuardConfigBuilder`)

After Task 1.9, all 7 simple strategies have `STRATEGY_SPEC` constants. Run the full parametric test:

```bash
cd nautilus && uv run python -m pytest tests/cli/test_strategy_discovery.py -v
```

Expected: 8 of 9 parametric cases PASS (kronos still fails).

### Task 1.10 — GREEN: add `STRATEGY_SPEC` to kronos package

**Files:**
- Modify: `strategies/crypto/kronos/__init__.py`

- [ ] **Step 1: Read current kronos `__init__.py`**

```bash
cat strategies/crypto/kronos/__init__.py
```

- [ ] **Step 2: Read the kronos entry in `STRATEGY_SPECS`**

```bash
grep -n -A12 '"kronos"' nautilus/src/nautilus_trading/cli/_strategy_specs.py
```

Note the actor wiring: kronos has `actor_specs=(ActorSpec(actor_path="strategies.crypto.kronos.actor:KronosActor", config_path="strategies.crypto.kronos.actor:KronosActorConfig", builder=KronosActorConfigBuilder()),)`.

- [ ] **Step 3: Append `STRATEGY_SPEC` to kronos `__init__.py`**

```python
# Append to strategies/crypto/kronos/__init__.py

from nautilus_trading.cli._strategy_specs import (
    ActorSpec,
    KronosActorConfigBuilder,
    KronosConfigBuilder,
    StrategySpec,
)

STRATEGY_SPEC = StrategySpec(
    name="kronos",
    builder=KronosConfigBuilder(),
    strategy_path="strategies.crypto.kronos.strategy:KronosStrategy",
    config_path="strategies.crypto.kronos.strategy:KronosStrategyConfig",
    actor_specs=(
        ActorSpec(
            actor_path="strategies.crypto.kronos.actor:KronosActor",
            config_path="strategies.crypto.kronos.actor:KronosActorConfig",
            builder=KronosActorConfigBuilder(),
        ),
    ),
)
```

- [ ] **Step 4: Run the parametric test for kronos**

```bash
cd nautilus && uv run python -m pytest tests/cli/test_strategy_discovery.py::test_strategy_module_exports_strategy_spec -v -k kronos
```

Expected: PASS.

- [ ] **Step 5: Run the full parametric test**

```bash
cd nautilus && uv run python -m pytest tests/cli/test_strategy_discovery.py -v
```

Expected: all 9 cases PASS.

- [ ] **Step 6: Commit**

```bash
git add strategies/crypto/kronos/__init__.py
git commit -m "feat(c): export STRATEGY_SPEC from strategies.crypto.kronos"
```

### Task 1.11 — RED: assert `STRATEGY_SPECS` is discovered from entry-points

**Files:**
- Modify: `nautilus/tests/cli/test_strategy_discovery.py`

- [ ] **Step 1: Append the discovery + duplicate-detection test**

```python
# Append to nautilus/tests/cli/test_strategy_discovery.py

import importlib.metadata


def test_strategy_specs_dict_is_populated_from_entry_points() -> None:
    """STRATEGY_SPECS is populated from `nautilus_trading.strategies` entry-points."""
    from nautilus_trading.cli._strategy_specs import STRATEGY_SPECS

    # Every key has a discoverable entry-point.
    eps = {ep.name for ep in importlib.metadata.entry_points(group="nautilus_trading.strategies")}
    assert set(STRATEGY_SPECS.keys()) == eps, (
        f"STRATEGY_SPECS keys ({sorted(STRATEGY_SPECS)}) must match registered entry-points ({sorted(eps)})"
    )

    # Every spec.name matches its dict key.
    for name, spec in STRATEGY_SPECS.items():
        assert spec.name == name, f"STRATEGY_SPECS['{name}'].name must equal key, got '{spec.name}'"


def test_discover_strategy_specs_raises_on_duplicate_names() -> None:
    """_discover_strategy_specs() raises RuntimeError when two entry-points share a name."""
    from dataclasses import replace
    from unittest.mock import MagicMock, patch

    from nautilus_trading.cli._strategy_specs import _discover_strategy_specs
    from strategies.crypto.grid_bot import STRATEGY_SPEC as GRID_BOT_SPEC

    duplicate_spec = replace(GRID_BOT_SPEC, name="duplicate_name")

    fake_ep_1 = MagicMock()
    fake_ep_1.name = "duplicate_name"
    fake_ep_1.load.return_value = duplicate_spec
    fake_ep_1.dist = MagicMock()
    fake_ep_1.dist.name = "package-a"

    fake_ep_2 = MagicMock()
    fake_ep_2.name = "duplicate_name"
    fake_ep_2.load.return_value = duplicate_spec
    fake_ep_2.dist = MagicMock()
    fake_ep_2.dist.name = "package-b"

    with patch(
        "nautilus_trading.cli._strategy_specs.importlib.metadata.entry_points",
        return_value=[fake_ep_1, fake_ep_2],
    ):
        with pytest.raises(RuntimeError, match="package-a.*package-b|package-b.*package-a"):
            _discover_strategy_specs()
```

- [ ] **Step 2: Run the new tests to verify they fail**

```bash
cd nautilus && uv run python -m pytest tests/cli/test_strategy_discovery.py::test_strategy_specs_dict_is_populated_from_entry_points tests/cli/test_strategy_discovery.py::test_discover_strategy_specs_raises_on_duplicate_names -v
```

Expected: both fail. The first because `STRATEGY_SPECS` is currently the hardcoded dict (not discovered from entry-points; entry-points group is empty pre-PR-1.12). The second because `_discover_strategy_specs` doesn't exist yet.

- [ ] **Step 3: Commit RED**

```bash
git add nautilus/tests/cli/test_strategy_discovery.py
git commit -m "test(c): RED — discovery + duplicate-name detection contract"
```

### Task 1.12 — GREEN: rewrite `cli/_strategy_specs.py` to discover

**Files:**
- Modify: `nautilus/src/nautilus_trading/cli/_strategy_specs.py`

- [ ] **Step 1: Read the current file** (to see what to keep vs replace)

```bash
sed -n '1,30p; $p' nautilus/src/nautilus_trading/cli/_strategy_specs.py
wc -l nautilus/src/nautilus_trading/cli/_strategy_specs.py
```

- [ ] **Step 2: Add `_discover_strategy_specs()` and replace the hardcoded `STRATEGY_SPECS` block**

Find the existing `STRATEGY_SPECS = {...}` block (ends the file). Replace it with:

```python
# At the top of the file, add:
import importlib.metadata


def _discover_strategy_specs() -> dict[str, StrategySpec]:
    """Discover strategies registered via the ``nautilus_trading.strategies`` entry-point group.

    Each entry-point resolves to a :class:`StrategySpec` constant or a zero-arg factory
    returning one. Discovery happens at module import; the result is cached in the module-level
    ``STRATEGY_SPECS`` dict for the process lifetime.

    Raises
    ------
    RuntimeError
        If two installed packages register the same strategy name (the error message
        names both packages so the user knows which to uninstall or rename).
    """
    specs: dict[str, StrategySpec] = {}
    sources: dict[str, str] = {}  # spec.name -> source distribution name
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


# Replace the entire hardcoded STRATEGY_SPECS = {...} block with:
STRATEGY_SPECS: dict[str, StrategySpec] = _discover_strategy_specs()

# Backward-compat: derived `name -> builder` projection used by `backtest/runner.py`.
STRATEGY_BUILDERS: dict[str, StrategyConfigBuilder] = {
    name: spec.builder for name, spec in STRATEGY_SPECS.items()
}
```

Keep everything else in the file intact: the Protocols, the `StrategySpec` and `ActorSpec` dataclasses, all 8 + kronos + KronosActor builder classes, the `_base()` helper.

- [ ] **Step 3: Run the discovery tests**

```bash
cd nautilus && uv run python -m pytest tests/cli/test_strategy_discovery.py -v
```

Expected: `test_strategy_specs_dict_is_populated_from_entry_points` passes once Task 1.13 registers the entry-points; until then this test FAILS (entry-points group is empty, but the hardcoded dict was the source of truth — now it's empty). The duplicate-name test PASSES because `_discover_strategy_specs` exists and the contract holds.

If `test_strategy_specs_dict_is_populated_from_entry_points` fails because `STRATEGY_SPECS` is empty (`set() != {ema_cross, grid_bot, ...}`), that's expected — Task 1.13 fixes it.

- [ ] **Step 4: Commit**

```bash
git add nautilus/src/nautilus_trading/cli/_strategy_specs.py
git commit -m "feat(c): rewrite _strategy_specs as entry-point discovery module"
```

### Task 1.13 — GREEN: register the 9 entry-points in `nautilus/pyproject.toml`

**Files:**
- Modify: `nautilus/pyproject.toml`

- [ ] **Step 1: Read the current pyproject to find the right insertion point**

```bash
grep -n "^\[" nautilus/pyproject.toml | head -20
```

Locate `[project.scripts]` (the existing `nt` entry). The new `[project.entry-points."nautilus_trading.strategies"]` block goes right after.

- [ ] **Step 2: Add the entry-points block**

Append to `nautilus/pyproject.toml` in the `[project]` section (after `[project.scripts]`):

```toml
[project.entry-points."nautilus_trading.strategies"]
ema_cross = "strategies.forex.ema_cross:STRATEGY_SPEC"
grid_bot = "strategies.crypto.grid_bot:STRATEGY_SPEC"
dca_bot = "strategies.crypto.dca_bot:STRATEGY_SPEC"
timesfm_swing = "strategies.crypto.timesfm_swing:STRATEGY_SPEC"
hybrid_sma_r10 = "strategies.crypto.hybrid_sma_r10:STRATEGY_SPEC"
timesfm_grid = "strategies.crypto.timesfm_grid:STRATEGY_SPEC"
rvs_swing = "strategies.crypto.rvs_swing:STRATEGY_SPEC"
shock_guard = "strategies.crypto.shock_guard:STRATEGY_SPEC"
kronos = "strategies.crypto.kronos:STRATEGY_SPEC"
```

- [ ] **Step 3: Re-install the package so entry-points register**

```bash
cd nautilus && uv sync
```

Editable installs need a re-sync to pick up new entry-points. Confirm by:

```bash
cd nautilus && uv run python -c "import importlib.metadata; print(sorted(ep.name for ep in importlib.metadata.entry_points(group='nautilus_trading.strategies')))"
```

Expected: `['dca_bot', 'ema_cross', 'grid_bot', 'hybrid_sma_r10', 'kronos', 'rvs_swing', 'shock_guard', 'timesfm_grid', 'timesfm_swing']`.

- [ ] **Step 4: Run the discovery tests**

```bash
cd nautilus && uv run python -m pytest tests/cli/test_strategy_discovery.py -v
```

Expected: all PASS (parametric 9, plus the 2 discovery tests).

- [ ] **Step 5: Run the full suite to verify no regressions**

```bash
cd nautilus && uv run python -m pytest
```

Expected: all PASS, same count as before this PR (any change in count signals a regression).

- [ ] **Step 6: Lint**

```bash
make lint
```

Expected: clean.

- [ ] **Step 7: Smoke the CLI**

```bash
cd nautilus && uv run nt paper-trade --config ../configs/paper/grid_bot.yaml --help
cd nautilus && uv run nt paper-trade --config ../configs/paper/kronos.yaml --help
cd nautilus && uv run nt backtest --config ../configs/backtest/ema_cross.yaml --help
```

Expected: all render. CLI dispatch works through the discovered registry.

- [ ] **Step 8: Commit**

```bash
git add nautilus/pyproject.toml
git commit -m "feat(c): register 9 in-repo strategies as nautilus_trading.strategies entry-points"
```

### Task 1.14 — Push branch and open PR 1

- [ ] **Step 1: Push branch**

```bash
git push -u origin subproject-c/pr1-entry-point-discovery
```

(You're already in the PR 1 worktree at `.claude/worktrees/c-pr1`; no `cd` needed.)

- [ ] **Step 2: Open PR 1**

```bash
gh pr create \
  --title "feat(c/pr1): entry-point discovery for strategies + 9 in-repo strategies migrated" \
  --body "$(cat <<'EOF'
## Summary

Sub-project C PR 1 — replaces the hardcoded `STRATEGY_SPECS` dict in `cli/_strategy_specs.py` with discovery via the standard Python `importlib.metadata` entry-points mechanism (group: `nautilus_trading.strategies`). Each of the 9 in-repo strategies gains a top-level `STRATEGY_SPEC` constant and is registered via an entry-point in `nautilus/pyproject.toml`. External strategies can now register themselves the same way without editing this repo.

- New: `nautilus/tests/cli/test_strategy_discovery.py` covers per-strategy spec export, dict-from-entry-points contract, and duplicate-name detection.
- Modified: `cli/_strategy_specs.py` (hardcoded dict → discovery), `nautilus/pyproject.toml` (entry-points block), each of 9 strategy modules (export `STRATEGY_SPEC`).
- No CLI behavior changes; all existing YAMLs work unchanged.

## Plan
`/Users/rc/Projects/workspace/nautilus-trading/docs/superpowers/plans/2026-05-04-subproject-c-implementation.md`

## Spec
`/Users/rc/Projects/workspace/nautilus-trading/docs/superpowers/specs/2026-05-04-subproject-c-design.md`

## Test plan

- [x] `make lint` clean
- [x] `cd nautilus && uv run python -m pytest` — full suite green
- [x] `nt {paper-trade, backtest} --config configs/{paper, backtest}/<strategy>.yaml --help` for grid_bot + kronos + ema_cross renders cleanly

## What's NOT in this PR

- `nt strategies` listing CLI — PR 2.
- External-strategy smoke test fixture — PR 2.
- Migration of the 9 in-repo strategies to a sibling repo — future cleanup.
EOF
)"
```

- [ ] **Step 3: Report PR URL**

Send PR URL to team-lead.

- [ ] **Step 4: Mark Task 1.14 complete**

---

## PR 2 — `nt strategies` CLI + external-strategy smoke test

Branch: `subproject-c/pr2-strategies-cli-and-smoke`. Base: `main` after PR 1 merges. Goal: provide a debugging surface (`nt strategies`) and prove the entry-point contract works for a third-party package via a synthetic smoke fixture.

### Task 2.1 — RED: `nt strategies` listing test

**Files:**
- Create: `nautilus/tests/cli/test_strategies_command.py`

- [ ] **Step 1: Write the failing test**

```python
# nautilus/tests/cli/test_strategies_command.py
"""Tests for the `nt strategies` listing CLI."""

from __future__ import annotations

from typer.testing import CliRunner

from nautilus_trading.cli import app

runner = CliRunner()


def test_strategies_command_lists_all_in_repo_strategies() -> None:
    """`nt strategies` lists each of the 9 in-repo strategies exactly once."""
    result = runner.invoke(app, ["strategies"])
    assert result.exit_code == 0, result.output

    expected = [
        "ema_cross",
        "grid_bot",
        "dca_bot",
        "timesfm_swing",
        "hybrid_sma_r10",
        "timesfm_grid",
        "rvs_swing",
        "shock_guard",
        "kronos",
    ]
    for name in expected:
        assert name in result.output, f"`nt strategies` output missing '{name}'"


def test_strategies_command_includes_source_package_for_each_entry() -> None:
    """Each listed strategy includes its source package name."""
    result = runner.invoke(app, ["strategies"])
    assert result.exit_code == 0
    # The source package for in-repo strategies is `nautilus-trading`.
    assert "nautilus-trading" in result.output


def test_strategies_command_includes_strategy_path_for_each_entry() -> None:
    """Each listed strategy includes its strategy_path."""
    result = runner.invoke(app, ["strategies"])
    assert result.exit_code == 0
    assert "strategies.forex.ema_cross:EMACrossStrategy" in result.output
    assert "strategies.crypto.kronos.strategy:KronosStrategy" in result.output
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd nautilus && uv run python -m pytest tests/cli/test_strategies_command.py -v
```

Expected: 3 failures with `Error: No such command 'strategies'` or similar (the subcommand doesn't exist yet).

- [ ] **Step 3: Commit RED**

```bash
git add nautilus/tests/cli/test_strategies_command.py
git commit -m "test(c/pr2): RED — nt strategies listing CLI contract"
```

### Task 2.2 — GREEN: implement `nt strategies` listing

**Files:**
- Create: `nautilus/src/nautilus_trading/cli/strategies.py`
- Modify: `nautilus/src/nautilus_trading/cli/__init__.py`

- [ ] **Step 1: Write the listing implementation**

Create `nautilus/src/nautilus_trading/cli/strategies.py`:

```python
"""`nt strategies` — list discovered strategy specs and their source packages.

Diagnostic surface for the entry-point discovery introduced in sub-project C.
Helps debug "why isn't my external strategy loading" by showing exactly which
specs are visible to ``nt`` and which package each one comes from.
"""

from __future__ import annotations

import importlib.metadata

import typer

from nautilus_trading.cli._strategy_specs import STRATEGY_SPECS

app = typer.Typer()


@app.callback(invoke_without_command=True)
def strategies() -> None:
    """List all discovered strategy specs with their source packages."""
    # Build the source-package map from entry-points.
    sources: dict[str, str] = {
        ep.name: ep.dist.name
        for ep in importlib.metadata.entry_points(group="nautilus_trading.strategies")
    }

    # Pretty-print, aligned columns.
    name_width = max(len(name) for name in STRATEGY_SPECS) if STRATEGY_SPECS else 8
    package_width = max(len(pkg) for pkg in sources.values()) if sources else 16

    if not STRATEGY_SPECS:
        typer.echo("No strategies discovered.")
        return

    for name in sorted(STRATEGY_SPECS):
        spec = STRATEGY_SPECS[name]
        pkg = sources.get(name, "?")
        typer.echo(
            f"{name:<{name_width}}  ({pkg:<{package_width}})  → {spec.strategy_path}"
        )
```

- [ ] **Step 2: Register the subcommand in `cli/__init__.py`**

Open `nautilus/src/nautilus_trading/cli/__init__.py` and add:

```python
# Add the import alongside existing subcommand imports.
from nautilus_trading.cli import strategies as strategies_cmd

# Add the registration alongside existing app.command() calls.
app.add_typer(strategies_cmd.app, name="strategies", help="List discovered strategies.")
```

The exact placement depends on the existing structure of `cli/__init__.py`. Read it first to match the style of `paper_trade`, `backtest`, `live` registrations.

- [ ] **Step 3: Run the tests**

```bash
cd nautilus && uv run python -m pytest tests/cli/test_strategies_command.py -v
```

Expected: all 3 PASS.

- [ ] **Step 4: Run the full suite**

```bash
cd nautilus && uv run python -m pytest
```

Expected: all PASS, no regressions.

- [ ] **Step 5: Smoke the new command**

```bash
cd nautilus && uv run nt strategies
```

Expected output: 9 strategies, sorted alphabetically, each with `(nautilus-trading)` source package and `strategies.<...>:<Class>` path.

- [ ] **Step 6: Lint**

```bash
make lint
```

Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add nautilus/src/nautilus_trading/cli/strategies.py nautilus/src/nautilus_trading/cli/__init__.py
git commit -m "feat(c/pr2): nt strategies — list discovered strategy specs"
```

### Task 2.3 — RED: external-strategy smoke test fixture + test

**Files:**
- Create: `nautilus/tests/cli/_external_strategy_fixture/pyproject.toml`
- Create: `nautilus/tests/cli/_external_strategy_fixture/external_strat/__init__.py`
- Create: `nautilus/tests/cli/_external_strategy_fixture/external_strat/strategy.py`
- Create: `nautilus/tests/cli/test_external_strategy_smoke.py`

- [ ] **Step 1: Write the synthetic external strategy package**

Create `nautilus/tests/cli/_external_strategy_fixture/pyproject.toml`:

```toml
[project]
name = "external-strat-fixture"
version = "0.0.1"
description = "Synthetic external strategy used by the smoke test only."

[project.entry-points."nautilus_trading.strategies"]
external_strat = "external_strat:STRATEGY_SPEC"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["external_strat"]
```

Create `nautilus/tests/cli/_external_strategy_fixture/external_strat/__init__.py`:

```python
"""Synthetic external strategy fixture for the smoke test.

Mirrors the shape of an in-repo strategy module's STRATEGY_SPEC export so the
discovery + dispatch round-trip can be exercised without depending on any real
external package.
"""

from __future__ import annotations

from typing import Any

from nautilus_trading.cli._strategy_specs import StrategyConfigBuilder, StrategySpec


class ExternalStratConfigBuilder:
    """Pass-through builder for the synthetic external strategy."""

    def build(self, args: dict[str, Any]) -> dict[str, Any]:
        if not args.get("instrument_id") or not args.get("bar_type"):
            raise ValueError("external_strat requires instrument_id and bar_type")
        return {
            "instrument_id": args["instrument_id"],
            "bar_type": args["bar_type"],
        }


STRATEGY_SPEC = StrategySpec(
    name="external_strat",
    builder=ExternalStratConfigBuilder(),
    strategy_path="external_strat.strategy:ExternalStratStrategy",
    config_path="external_strat.strategy:ExternalStratConfig",
)
```

Create `nautilus/tests/cli/_external_strategy_fixture/external_strat/strategy.py`:

```python
"""Minimal Strategy + Config classes for the synthetic external fixture.

These aren't intended to actually trade — only to satisfy import resolution
during the smoke test."""

from __future__ import annotations

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.data import BarType
from nautilus_trader.trading.strategy import Strategy


class ExternalStratConfig(StrategyConfig, frozen=True):
    instrument_id: InstrumentId
    bar_type: BarType


class ExternalStratStrategy(Strategy):
    def __init__(self, config: ExternalStratConfig) -> None:
        super().__init__(config)
```

- [ ] **Step 2: Write the smoke test**

Create `nautilus/tests/cli/test_external_strategy_smoke.py`:

```python
"""Smoke test — a third-party package can register an external strategy via entry-point.

The fixture under `_external_strategy_fixture/` is a minimal pip-installable
package. We `pip install -e` it inside a temporary subprocess venv (or via
`uv pip install --editable`) and verify that the spec is discovered and
dispatchable through the same CLI surface as in-repo strategies.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

FIXTURE_DIR = Path(__file__).parent / "_external_strategy_fixture"


@pytest.fixture(scope="module")
def installed_external_strategy() -> None:
    """Install the synthetic external strategy in editable mode for the test session."""
    # Install editable in the same venv pytest is running in.
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "--editable", str(FIXTURE_DIR), "--quiet"],
        check=True,
    )
    yield
    # Uninstall on teardown.
    subprocess.run(
        [sys.executable, "-m", "pip", "uninstall", "external-strat-fixture", "-y", "--quiet"],
        check=True,
    )


def test_external_strategy_is_discovered(installed_external_strategy: None) -> None:
    """After install, `external_strat` shows up in STRATEGY_SPECS."""
    # Re-import to pick up the new entry-point registration.
    import importlib

    import nautilus_trading.cli._strategy_specs as specs_module

    importlib.reload(specs_module)

    from nautilus_trading.cli._strategy_specs import STRATEGY_SPECS

    assert "external_strat" in STRATEGY_SPECS, (
        f"external_strat not discovered; available: {sorted(STRATEGY_SPECS)}"
    )
    assert STRATEGY_SPECS["external_strat"].strategy_path == "external_strat.strategy:ExternalStratStrategy"


def test_external_strategy_listed_by_strategies_command(installed_external_strategy: None) -> None:
    """`nt strategies` lists external_strat alongside the 9 in-repo strategies."""
    from typer.testing import CliRunner
    import importlib

    import nautilus_trading.cli._strategy_specs as specs_module
    import nautilus_trading.cli as cli_module

    importlib.reload(specs_module)
    importlib.reload(cli_module)

    runner = CliRunner()
    result = runner.invoke(cli_module.app, ["strategies"])
    assert result.exit_code == 0
    assert "external_strat" in result.output
    assert "external-strat-fixture" in result.output  # source package name
```

- [ ] **Step 3: Run the smoke tests to verify they fail**

```bash
cd nautilus && uv run python -m pytest tests/cli/test_external_strategy_smoke.py -v
```

Expected: tests run; the first install via `pip install --editable` succeeds (the fixture is well-formed). After install, the discovery test PASSES if the entry-point machinery from PR 1 works correctly. If the test FAILS at the assertion stage, the bug is in PR 1's discovery — investigate before continuing.

If the install itself fails (e.g., `external-strat-fixture` can't build), check that hatchling is in the test-time uv environment. If not, switch the fixture's `[build-system]` to `setuptools` to keep the dependency footprint minimal.

- [ ] **Step 4: Commit RED**

```bash
git add nautilus/tests/cli/_external_strategy_fixture/ nautilus/tests/cli/test_external_strategy_smoke.py
git commit -m "test(c/pr2): smoke — synthetic external strategy registers via entry-point"
```

### Task 2.4 — Write the external-strategies runbook

**Files:**
- Create: `docs/runbooks/external-strategies.md`

- [ ] **Step 1: Write the runbook**

Create `docs/runbooks/external-strategies.md`:

```markdown
# External strategies — registering with `nt`

This repo discovers strategies via Python entry-points. To plug an external strategy into `nt {backtest, paper-trade, live} --config ...` without modifying this repo:

## Contract

Your package must:

1. Expose a top-level `STRATEGY_SPEC = StrategySpec(...)` constant in some module.
2. Register that constant under the `nautilus_trading.strategies` entry-point group in your `pyproject.toml`.
3. Provide importable `Strategy` and `StrategyConfig` classes at the paths the spec references.
4. (Optional) For actor-bearing strategies, populate `STRATEGY_SPEC.actor_specs` with one or more `ActorSpec(...)` entries.

## Example

```toml
# my-strategies/pyproject.toml
[project.entry-points."nautilus_trading.strategies"]
my_swing = "my_strategies.swing:STRATEGY_SPEC"
```

```python
# my-strategies/my_strategies/swing.py
from nautilus_trading.cli._strategy_specs import StrategyConfigBuilder, StrategySpec
from nautilus_trader.config import StrategyConfig
from nautilus_trader.trading.strategy import Strategy


class MySwingConfigBuilder:
    def build(self, args: dict) -> dict:
        # Validate + transform args -> StrategyConfig kwargs.
        return {"instrument_id": args["instrument_id"], "bar_type": args["bar_type"]}


class MySwingConfig(StrategyConfig, frozen=True):
    instrument_id: str
    bar_type: str


class MySwingStrategy(Strategy):
    def __init__(self, config: MySwingConfig) -> None:
        super().__init__(config)
    # ... your trading logic ...


STRATEGY_SPEC = StrategySpec(
    name="my_swing",
    builder=MySwingConfigBuilder(),
    strategy_path="my_strategies.swing:MySwingStrategy",
    config_path="my_strategies.swing:MySwingConfig",
)
```

## Installing

```bash
# Inside the nautilus-trading worktree's venv:
cd /path/to/my-strategies
uv pip install --editable .
```

Then verify:

```bash
cd /path/to/nautilus-trading/nautilus
uv run nt strategies
# Expected: my_swing listed alongside the 9 in-repo strategies.
```

Run it:

```bash
uv run nt backtest --config /path/to/configs/backtest/my_swing.yaml
```

## Failure modes

| Symptom | Cause | Fix |
|---|---|---|
| `nt strategies` doesn't show your strategy | Editable install missed the entry-point group | Re-run `uv pip install --editable .`; verify with `python -c "import importlib.metadata; print([ep.name for ep in importlib.metadata.entry_points(group='nautilus_trading.strategies')])"` |
| `RuntimeError: Duplicate strategy registration` | Two installed packages register the same name | Uninstall one or rename the duplicate's entry-point key |
| `RuntimeError: Strategy '<name>' not found` at YAML load | YAML's `strategy:` field doesn't match any registered entry-point name | Check `nt strategies` for the expected name |
| Import error during discovery | Your module raises at import time | Fix the import-time error; entry-point loading is fail-fast |
```

- [ ] **Step 2: Commit**

```bash
git add docs/runbooks/external-strategies.md
git commit -m "docs(c/pr2): runbook for external strategy authors"
```

### Task 2.5 — Push branch and open PR 2

- [ ] **Step 1: Push**

```bash
git push -u origin subproject-c/pr2-strategies-cli-and-smoke
```

(You're already in the PR 2 worktree at `.claude/worktrees/c-pr2`; no `cd` needed.)

- [ ] **Step 2: Open PR 2**

```bash
gh pr create \
  --title "feat(c/pr2): nt strategies CLI + external-strategy smoke + runbook" \
  --body "$(cat <<'EOF'
## Summary

Sub-project C PR 2 — adds the diagnostic + onboarding surface for the entry-point discovery introduced in PR 1.

- **`nt strategies`** — lists all discovered strategy specs with their source package and import path. Helps debug \"why isn't my external strategy loading\".
- **External-strategy smoke test** — synthetic third-party package fixture under `tests/cli/_external_strategy_fixture/`; the smoke test installs it editable, verifies discovery, and verifies dispatch through `nt strategies`.
- **Runbook** — `docs/runbooks/external-strategies.md` walks an external author through the contract.

This PR completes sub-project C as scoped: the repo is now a runtime tool with a stable plugin surface for external strategies. The 9 in-repo strategies continue to ship as the reference implementation.

## Plan
`/Users/rc/Projects/workspace/nautilus-trading/docs/superpowers/plans/2026-05-04-subproject-c-implementation.md`

## Spec
`/Users/rc/Projects/workspace/nautilus-trading/docs/superpowers/specs/2026-05-04-subproject-c-design.md`

## Test plan

- [x] `make lint` clean
- [x] `cd nautilus && uv run python -m pytest` — full suite green (incl. external-strategy smoke install + dispatch)
- [x] `nt strategies` lists 9 in-repo strategies with source package
- [x] Smoke test installs synthetic external fixture, verifies discovery, uninstalls cleanly

## What's NOT in this PR

- Migration of the 9 in-repo strategies to a sibling repo — future cleanup once contract is proven in production use.
- Real-money execution — out of scope per 2026-04-21 directive.

EOF
)"
```

- [ ] **Step 3: Report PR URL**

Send PR URL to team-lead. Sub-project C complete with PR 2's merge.

- [ ] **Step 4: Mark Task 2.5 complete**

---

## Verification (end of sub-project C)

After both PRs merge:

- `make lint` clean.
- `cd nautilus && uv run python -m pytest` — all green; the external-strategy smoke test install + uninstall round-trips cleanly.
- `nt strategies` lists 9 in-repo strategies sourced from `nautilus-trading`.
- An external user following `docs/runbooks/external-strategies.md` can register a strategy and dispatch via `nt {backtest, paper-trade, live} --config ...`.
- All 9 existing YAMLs in `configs/{paper, backtest, live}/` still work.
- `STRATEGY_SPECS` in `cli/_strategy_specs.py` is now derived from entry-points (not hardcoded); existing imports of `STRATEGY_SPECS` keep working transparently.

## Notes for the executor

- TDD throughout: every implementation task starts with a RED commit (failing test), then a GREEN commit (minimal code to pass).
- Don't bundle multiple per-strategy `STRATEGY_SPEC` additions into one commit — separate commits keep the git log scannable. The 9 are mechanical but each is its own ~6-line edit.
- After Task 1.13 (entry-points registered), `cd nautilus && uv sync` is REQUIRED before the discovery tests pass — entry-points only register when the package metadata is rebuilt.
- The kronos package is the only entry that exports `STRATEGY_SPEC` from a package `__init__.py` rather than a module. The strategy_path/config_path strings still point at `strategies.crypto.kronos.strategy:KronosStrategy` (the strategy class lives in the submodule, not in `__init__.py`).
- If the smoke test in PR 2 (Task 2.3) fails with hatchling-build errors, swap the fixture's `[build-system]` to setuptools — the test only needs editable install to work, not any specific backend.
