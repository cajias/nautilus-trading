# PR 6 — YAML Run Configs · Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the 16-option, 8-branch `nt paper-trade` flag ladder with a **single required** `--config configs/paper/<name>.yaml` option, backed by 8 committed example configs — one per strategy. Delete the flag path entirely. No deprecation window, no fallback.

**Architecture:** New `PaperRunConfig` (msgspec Struct, `forbid_unknown_fields=True`) decodes YAML into a strict top-level shape with a loose `params: dict` bucket for strategy-specific values. The existing `_RUNNERS` registry and `StrategyConfigBuilder.build()` validation path are reused unchanged.

**Tech Stack:** Python 3.13, `uv`, `msgspec` (already a dep), `msgspec.yaml` (requires PyYAML — new dep), `typer` (existing), `pytest` (existing). Runtime entry unchanged: `nt paper-trade …` dispatched through `nautilus_trading.cli:app`.

---

## Before you start

**Worker context:** Repo is a NautilusTrader crypto-trading project. Primary package lives in `nautilus/src/nautilus_trading/`. Strategies live at repo root under `strategies/` (outside the package — imports there need `# type: ignore[import-not-found]`). All commands run via `make` from repo root or via `uv run python -m pytest ../tests -q` from `nautilus/`.

**Environment gotchas you MUST respect:**

1. **pytest invocation.** From `nautilus/` use `uv run python -m pytest` — NEVER bare `uv run pytest` (resolves Homebrew Py 3.9 instead of venv). Memory: `project_pytest_invocation.md`.
2. **Formatter-hook vs ruff import feud.** The PostToolUse `post-format-lint.sh` hook strips the blank line between `nautilus_trader.*` and `nautilus_trading.*` imports, but `ruff` requires it. If Write/Edit triggers the hook and lint fails, either (a) write via `python3 -c "open(p,'w').write(…)"` heredoc to bypass the hook, or (b) do one final Edit to re-add the blank line. Do NOT run `make lint-fix` — it mangles paren-style imports to backslash continuation. Memory: `project_lint_hook_vs_ruff.md`.
3. **Commit message style.** Conventional commits: `feat(scope): …`, `test(scope): …`, `refactor(scope): …`, `docs(scope): …`. No scope wider than one subsystem per commit.
4. **Branch.** Before Task 1, verify PR 5 is merged into `main` and cut a fresh branch:
   ```bash
   git fetch origin && git checkout main && git pull --ff-only
   git checkout -b subproject-b/pr6-yaml-run-configs
   ```
5. **Spec.** Source of truth for this PR: `docs/superpowers/specs/2026-04-22-pr6-yaml-run-configs-design.md`. Read it before starting.

**Verify test count baseline.** From `nautilus/`:
```bash
uv run python -m pytest ../tests -q
# Expected: 286 passed, 19 skipped
```
Every task below states the expected new count so regressions are caught immediately.

---

## Task 1 — Add PyYAML + create `PaperRunConfig` schema and loader

**Goal:** Introduce the msgspec Struct and `load_run_config()` function. End state: a valid YAML round-trips into `PaperRunConfig`.

**Files:**
- Modify: `nautilus/pyproject.toml` (add PyYAML dep)
- Create: `nautilus/src/nautilus_trading/paper_trade/run_config.py`
- Create: `tests/paper_trade/test_run_config.py`

- [ ] **Step 1: Write the failing round-trip test**

Create `tests/paper_trade/test_run_config.py`:

```python
"""Schema + loader tests for PaperRunConfig."""

from __future__ import annotations

from pathlib import Path

import msgspec
import pytest

from nautilus_trading.paper_trade.run_config import PaperRunConfig, load_run_config


def test_load_run_config_round_trips_minimal(tmp_path: Path):
    """Minimal valid YAML → PaperRunConfig with defaults filled."""
    yaml_text = """\
strategy: ema_cross
instrument_id: BTCUSDT.BINANCE
bar_type: BTCUSDT.BINANCE-1-MINUTE-LAST-EXTERNAL
trade_size: "0.001"
params:
  fast_ema: 12
  slow_ema: 26
"""
    path = tmp_path / "run.yaml"
    path.write_text(yaml_text)

    cfg = load_run_config(path)

    assert isinstance(cfg, PaperRunConfig)
    assert cfg.strategy == "ema_cross"
    assert cfg.instrument_id == "BTCUSDT.BINANCE"
    assert cfg.bar_type == "BTCUSDT.BINANCE-1-MINUTE-LAST-EXTERNAL"
    assert cfg.trade_size == "0.001"
    assert cfg.log_level == "INFO"  # default
    assert cfg.duration is None      # default
    assert cfg.params == {"fast_ema": 12, "slow_ema": 26}
```

- [ ] **Step 2: Run the test — expect FAIL (module missing)**

From `nautilus/`:
```bash
uv run python -m pytest ../tests/paper_trade/test_run_config.py -v
```
Expected: `ModuleNotFoundError: No module named 'nautilus_trading.paper_trade.run_config'`.

- [ ] **Step 3: Add PyYAML dependency**

From `nautilus/`:
```bash
uv add pyyaml
```
This updates `pyproject.toml` (adds `"pyyaml>=6.0"` under `[project] dependencies`) and `uv.lock`. Confirm with:
```bash
grep pyyaml pyproject.toml
```

- [ ] **Step 4: Implement `run_config.py`**

Create `nautilus/src/nautilus_trading/paper_trade/run_config.py`:

```python
"""PaperRunConfig — strict YAML schema for `nt paper-trade --config`.

Top-level fields are validated by `msgspec` (unknown keys rejected). The
`params` bucket holds strategy-specific values and is handed as **kwargs to
the StrategyConfigBuilder at dispatch time; that builder already raises
ValueError for missing/bad fields, which the CLI remaps to BadParameter.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import msgspec
import msgspec.yaml


class PaperRunConfig(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    """One paper-trade run, declared in a YAML file."""

    strategy: str
    instrument_id: str
    bar_type: str
    trade_size: str | None = None
    log_level: str = "INFO"
    duration: str | None = None
    params: dict[str, Any] = msgspec.field(default_factory=dict)


def load_run_config(path: Path) -> PaperRunConfig:
    """Read YAML at `path` and decode into PaperRunConfig.

    Raises:
        FileNotFoundError: path does not exist.
        msgspec.ValidationError: unknown field, wrong type, or missing required field.
    """
    data = path.read_bytes()
    return msgspec.yaml.decode(data, type=PaperRunConfig)
```

- [ ] **Step 5: Run the test — expect PASS**

From `nautilus/`:
```bash
uv run python -m pytest ../tests/paper_trade/test_run_config.py -v
```
Expected: 1 passed.

- [ ] **Step 6: Commit**

From repo root:
```bash
git add nautilus/pyproject.toml nautilus/uv.lock \
        nautilus/src/nautilus_trading/paper_trade/run_config.py \
        tests/paper_trade/test_run_config.py
git commit -m "feat(paper-trade): add PaperRunConfig YAML schema + loader"
```

**Expected test count after Task 1: 287 passed, 19 skipped.**

---

## Task 2 — Schema guards (unknown field, missing field, trade_size=null)

**Goal:** Lock down schema strictness with explicit tests. These should ALL pass on first run because `msgspec.Struct(forbid_unknown_fields=True)` and `str | None` already give us this behavior — we're documenting/guarding the contract.

**Files:**
- Modify: `tests/paper_trade/test_run_config.py`

- [ ] **Step 1: Add three guard tests**

Append to `tests/paper_trade/test_run_config.py`:

```python
def test_load_run_config_rejects_unknown_top_level_field(tmp_path: Path):
    """Unknown top-level key → msgspec.ValidationError."""
    path = tmp_path / "run.yaml"
    path.write_text(
        "strategy: ema_cross\n"
        "instrument_id: X\n"
        "bar_type: Y\n"
        "trade_size: \"0.001\"\n"
        "bogus_field: 1\n"
    )
    with pytest.raises(msgspec.ValidationError) as excinfo:
        load_run_config(path)
    assert "bogus_field" in str(excinfo.value)


def test_load_run_config_rejects_missing_required_field(tmp_path: Path):
    """Missing required top-level field → msgspec.ValidationError."""
    path = tmp_path / "run.yaml"
    path.write_text(
        "strategy: ema_cross\n"
        "instrument_id: X\n"
        # bar_type missing
        "trade_size: \"0.001\"\n"
    )
    with pytest.raises(msgspec.ValidationError) as excinfo:
        load_run_config(path)
    assert "bar_type" in str(excinfo.value)


def test_load_run_config_accepts_null_trade_size(tmp_path: Path):
    """trade_size is optional — null decodes to None (hybrid_sma_r10 case)."""
    path = tmp_path / "run.yaml"
    path.write_text(
        "strategy: hybrid_sma_r10\n"
        "instrument_id: BTCUSDT.BINANCE\n"
        "bar_type: BTCUSDT.BINANCE-1-MINUTE-LAST-EXTERNAL\n"
        "trade_size: null\n"
        "params:\n"
        "  sma_fast: 10\n"
        "  sma_slow: 30\n"
    )
    cfg = load_run_config(path)
    assert cfg.trade_size is None
```

- [ ] **Step 2: Run the tests — expect PASS on first run**

From `nautilus/`:
```bash
uv run python -m pytest ../tests/paper_trade/test_run_config.py -v
```
Expected: 4 passed. If any fail, the schema defaults drifted — do NOT work around; fix `run_config.py` so the contract holds.

- [ ] **Step 3: Run full suite + lint**

```bash
cd nautilus && uv run python -m pytest ../tests -q && cd -
make lint
```
Expected: 290 passed, 19 skipped. Lint green.

- [ ] **Step 4: Commit**

```bash
git add tests/paper_trade/test_run_config.py
git commit -m "test(paper-trade): guard PaperRunConfig schema strictness"
```

**Expected test count after Task 2: 290 passed, 19 skipped.**

---

## Task 3 — Rewrite `paper_trade()` CLI as `--config`-only; delete flag path and obsolete tests

**Goal:** Replace the entire `paper_trade()` body + signature with a single `--config`-driven dispatch. Delete the 16 Typer options, the 8-branch `if/elif` ladder, and every flag-based test. Add ONE sanity dispatch test to prove the new path wires.

This is a large, single-shot rewrite by design — the flag path and the YAML path cannot coexist cleanly without the conditional scaffolding the user explicitly rejected.

**Files:**
- Modify: `nautilus/src/nautilus_trading/cli/paper_trade.py` (rewrite)
- Modify: `tests/cli/test_paper_trade_cli.py` (delete flag-based tests, add one YAML dispatch test)

### Step 1: Inventory obsolete tests

From repo root:
```bash
grep -n "^def test_" tests/cli/test_paper_trade_cli.py
```

Expect to see (exact names verified against the current file):
- `test_paper_trade_ema_cross_dispatches_to_runner`
- `test_paper_trade_grid_bot_dispatches_to_runner`
- `test_paper_trade_dca_bot_dispatches_to_runner`
- `test_paper_trade_timesfm_swing_dispatches_to_runner`
- `test_paper_trade_hybrid_sma_r10_dispatches_to_runner`
- `test_paper_trade_timesfm_grid_dispatches_to_runner`
- `test_paper_trade_rvs_swing_dispatches_to_runner`
- `test_paper_trade_shock_guard_dispatches_to_runner`
- `test_paper_trade_grid_bot_missing_required_args_is_usage_error`
- `test_paper_trade_hybrid_sma_r10_missing_required_args_is_usage_error`
- `test_paper_trade_unknown_strategy_is_usage_error` (flag form)

All of these exercise the deleted flag surface. Their job is taken over by the parametrized config test in Task 4 and the error-path tests in Task 5.

Record the exact set from the grep output — you will delete each of these functions from the file.

- [ ] **Step 2: Write the failing sanity YAML dispatch test**

Open `tests/cli/test_paper_trade_cli.py`. Delete every flag-based test function found in Step 1 (keep the file header imports — they're shared). Add this single sanity test:

```python
def test_paper_trade_config_file_dispatches_to_runner(tmp_path, monkeypatch):
    """`nt paper-trade --config run.yaml` loads the YAML, instantiates the
    right runner, and calls .main(). Sanity check for the YAML dispatch path;
    per-strategy coverage lives in tests/cli/test_paper_trade_configs.py.
    """
    calls = []

    def _recording_main(self):
        calls.append(("ema_cross", self.instrument_id, self.fast_ema, self.slow_ema))

    from strategies.crypto.ema_cross_paper import EMACrossPaperTradeRunner

    monkeypatch.setattr(EMACrossPaperTradeRunner, "main", _recording_main)

    yaml_path = tmp_path / "run.yaml"
    yaml_path.write_text(
        "strategy: ema_cross\n"
        "instrument_id: BTCUSDT.BINANCE\n"
        "bar_type: BTCUSDT.BINANCE-1-MINUTE-LAST-EXTERNAL\n"
        "trade_size: \"0.001\"\n"
        "params:\n"
        "  fast_ema: 12\n"
        "  slow_ema: 26\n"
    )

    runner = CliRunner()
    result = runner.invoke(app, ["paper-trade", "--config", str(yaml_path)])
    assert result.exit_code == 0, result.stdout
    assert calls == [("ema_cross", "BTCUSDT.BINANCE", 12, 26)]
```

- [ ] **Step 3: Run — expect FAIL (`--config` does not exist yet)**

From `nautilus/`:
```bash
uv run python -m pytest ../tests/cli/test_paper_trade_cli.py -v
```
Expected: `test_paper_trade_config_file_dispatches_to_runner` fails with "No such option: --config". The deleted tests should be absent from collection.

- [ ] **Step 4: Rewrite `paper_trade.py`**

Replace the entire contents of `nautilus/src/nautilus_trading/cli/paper_trade.py` with:

```python
"""`nt paper-trade` — Binance Spot Testnet paper-trade entry point.

Single-option CLI: `--config path/to/run.yaml`. The YAML schema lives in
`nautilus_trading.paper_trade.run_config.PaperRunConfig`; example configs
under `configs/paper/`.
"""

from __future__ import annotations

from pathlib import Path

import msgspec
import typer

from nautilus_trading.cli._common import _ensure_project_root_on_path

# Strategy-name → runner class, populated lazily to keep CLI import cheap.
_RUNNERS: dict[str, type] = {}


def _load_runners() -> None:
    """Populate the strategy-name → runner class registry on first use."""
    if _RUNNERS:
        return
    # Lazy import: strategies/ lives at the project root, not inside the
    # nautilus/ package, so it only resolves after _ensure_project_root_on_path()
    # has run. mypy can't see it — but the import is exercised at runtime by
    # the CLI tests in tests/cli/test_paper_trade_cli.py.
    from strategies.crypto.dca_bot_paper import (  # type: ignore[import-not-found]
        DCABotPaperTradeRunner,
    )
    from strategies.crypto.ema_cross_paper import (  # type: ignore[import-not-found]
        EMACrossPaperTradeRunner,
    )
    from strategies.crypto.grid_bot_paper import (  # type: ignore[import-not-found]
        GridBotPaperTradeRunner,
    )
    from strategies.crypto.hybrid_sma_r10_paper import (  # type: ignore[import-not-found]
        HybridSMAR10PaperTradeRunner,
    )
    from strategies.crypto.rvs_swing_paper import (  # type: ignore[import-not-found]
        RVSSwingPaperTradeRunner,
    )
    from strategies.crypto.shock_guard_paper import (  # type: ignore[import-not-found]
        ShockGuardPaperTradeRunner,
    )
    from strategies.crypto.timesfm_grid_paper import (  # type: ignore[import-not-found]
        TimesFMGridPaperTradeRunner,
    )
    from strategies.crypto.timesfm_swing_paper import (  # type: ignore[import-not-found]
        TimesFMSwingPaperTradeRunner,
    )

    _RUNNERS["ema_cross"] = EMACrossPaperTradeRunner
    _RUNNERS["grid_bot"] = GridBotPaperTradeRunner
    _RUNNERS["dca_bot"] = DCABotPaperTradeRunner
    _RUNNERS["timesfm_swing"] = TimesFMSwingPaperTradeRunner
    _RUNNERS["hybrid_sma_r10"] = HybridSMAR10PaperTradeRunner
    _RUNNERS["timesfm_grid"] = TimesFMGridPaperTradeRunner
    _RUNNERS["rvs_swing"] = RVSSwingPaperTradeRunner
    _RUNNERS["shock_guard"] = ShockGuardPaperTradeRunner


def paper_trade(
    config: Path = typer.Option(
        ...,
        "--config",
        help="Path to a YAML run config (see configs/paper/ for examples).",
        exists=True,
        dir_okay=False,
        readable=True,
    ),
) -> None:
    """Run a strategy on Binance Spot Testnet (paper trading)."""
    # Lazy imports so `import nautilus_trading.cli` stays cheap at collection time.
    from nautilus_trading.paper_trade.run_config import load_run_config
    from nautilus_trading.paper_trade.secrets import load_dotenv_local

    _ensure_project_root_on_path()
    load_dotenv_local()
    _load_runners()

    try:
        run_config = load_run_config(config)
    except msgspec.ValidationError as exc:
        raise typer.BadParameter(
            f"Invalid config {config}: {exc}",
            param_hint="--config",
        ) from exc

    if run_config.strategy not in _RUNNERS:
        valid = ", ".join(sorted(_RUNNERS))
        raise typer.BadParameter(
            f"Unknown strategy '{run_config.strategy}'. Valid: {valid}",
            param_hint="--config",
        )
    runner_cls = _RUNNERS[run_config.strategy]

    runner_kwargs: dict[str, object] = {
        "instrument_id": run_config.instrument_id,
        "bar_type": run_config.bar_type,
        "log_level": run_config.log_level,
        **run_config.params,
    }
    if run_config.trade_size is not None:
        runner_kwargs["trade_size"] = run_config.trade_size

    # Dispatch: each runner accepts only the kwargs its dataclass declares.
    # TypeError → unexpected kwargs (wrong field name or shape);
    # ValueError → StrategyConfigBuilder rejected missing/bad required args.
    # We eagerly call build_config() here so the builder validates *before*
    # main() boots a TradingNode — a raw traceback would be hostile CLI UX.
    try:
        runner = runner_cls(**runner_kwargs)
        runner.build_config()
    except (TypeError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc

    runner.main()
```

Note on the import feud: the two blocks `import msgspec\nimport typer` (third-party) and `from nautilus_trading.cli._common import …` (first-party) MUST be separated by exactly one blank line. If the PostToolUse formatter strips it, the fastest fix is a final Edit that re-inserts the blank line — do not run `make lint-fix`.

- [ ] **Step 5: Run CLI tests — expect PASS**

From `nautilus/`:
```bash
uv run python -m pytest ../tests/cli/test_paper_trade_cli.py -v
```
Expected: the sanity test passes; no other tests collected from this file (the flag-based ones were deleted in Step 2).

- [ ] **Step 6: Run full suite + lint**

```bash
cd nautilus && uv run python -m pytest ../tests -q && cd -
make lint
```
Expected: **280 passed, 19 skipped** (286 baseline − 11 deleted flag tests + 4 schema tests from Tasks 1–2 already in + 1 new sanity test = 280). Lint green.

- [ ] **Step 7: Commit**

```bash
git add nautilus/src/nautilus_trading/cli/paper_trade.py \
        tests/cli/test_paper_trade_cli.py
git commit -m "refactor(cli): replace paper-trade flag ladder with --config YAML"
```

**Expected test count after Task 3: 280 passed, 19 skipped.**

---

## Task 4 — Commit 8 example configs + parametrized dispatch test

**Goal:** One runnable YAML per strategy in `configs/paper/`, plus a parametrized test that loads each and asserts dispatch to the correct runner.

**Files:**
- Create: `configs/paper/ema_cross.yaml`
- Create: `configs/paper/grid_bot.yaml`
- Create: `configs/paper/dca_bot.yaml`
- Create: `configs/paper/timesfm_swing.yaml`
- Create: `configs/paper/hybrid_sma_r10.yaml`
- Create: `configs/paper/timesfm_grid.yaml`
- Create: `configs/paper/rvs_swing.yaml`
- Create: `configs/paper/shock_guard.yaml`
- Create: `tests/cli/test_paper_trade_configs.py`

- [ ] **Step 1: Write the failing parametrized test**

Create `tests/cli/test_paper_trade_configs.py`:

```python
"""Each committed config in configs/paper/ dispatches to its runner.

Avoids Testnet boot by monkeypatching every runner's `.main()` to a
recorder. This locks the YAML schema: if any field name drifts, exactly
one parametrized case fails.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from typer.testing import CliRunner

from nautilus_trading.cli import app


REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIGS_DIR = REPO_ROOT / "configs" / "paper"


CONFIG_CASES = [
    ("ema_cross.yaml", "strategies.crypto.ema_cross_paper", "EMACrossPaperTradeRunner"),
    ("grid_bot.yaml", "strategies.crypto.grid_bot_paper", "GridBotPaperTradeRunner"),
    ("dca_bot.yaml", "strategies.crypto.dca_bot_paper", "DCABotPaperTradeRunner"),
    ("timesfm_swing.yaml", "strategies.crypto.timesfm_swing_paper", "TimesFMSwingPaperTradeRunner"),
    ("hybrid_sma_r10.yaml", "strategies.crypto.hybrid_sma_r10_paper", "HybridSMAR10PaperTradeRunner"),
    ("timesfm_grid.yaml", "strategies.crypto.timesfm_grid_paper", "TimesFMGridPaperTradeRunner"),
    ("rvs_swing.yaml", "strategies.crypto.rvs_swing_paper", "RVSSwingPaperTradeRunner"),
    ("shock_guard.yaml", "strategies.crypto.shock_guard_paper", "ShockGuardPaperTradeRunner"),
]


@pytest.mark.parametrize("filename,module,classname", CONFIG_CASES)
def test_committed_config_dispatches_to_runner(filename, module, classname, monkeypatch):
    """configs/paper/<name>.yaml instantiates <classname> and calls .main()."""
    mod = importlib.import_module(module)
    runner_cls = getattr(mod, classname)

    calls = []
    monkeypatch.setattr(runner_cls, "main", lambda self: calls.append(self))

    path = CONFIGS_DIR / filename
    assert path.exists(), f"Missing committed config: {path}"

    cli_runner = CliRunner()
    result = cli_runner.invoke(app, ["paper-trade", "--config", str(path)])
    assert result.exit_code == 0, f"{filename}: {result.stdout}"
    assert len(calls) == 1, f"{filename}: expected one .main() call, got {len(calls)}"
```

- [ ] **Step 2: Run — expect FAIL (configs don't exist yet)**

From `nautilus/`:
```bash
uv run python -m pytest ../tests/cli/test_paper_trade_configs.py -v
```
Expected: all 8 parametrized cases fail with `AssertionError: Missing committed config`.

- [ ] **Step 3: Create the 8 committed YAML configs**

From repo root:
```bash
mkdir -p configs/paper
```

Create each file with the exact content below.

`configs/paper/ema_cross.yaml`:
```yaml
strategy: ema_cross
instrument_id: BTCUSDT.BINANCE
bar_type: BTCUSDT.BINANCE-1-MINUTE-LAST-EXTERNAL
trade_size: "0.001"
log_level: INFO
params:
  fast_ema: 10
  slow_ema: 20
```

`configs/paper/grid_bot.yaml`:
```yaml
strategy: grid_bot
instrument_id: BTCUSDT.BINANCE
bar_type: BTCUSDT.BINANCE-1-MINUTE-LAST-EXTERNAL
trade_size: "0.001"
log_level: INFO
params:
  upper_price: "72000"
  lower_price: "60000"
  grid_levels: 8
```

`configs/paper/dca_bot.yaml`:
```yaml
strategy: dca_bot
instrument_id: BTCUSDT.BINANCE
bar_type: BTCUSDT.BINANCE-1-MINUTE-LAST-EXTERNAL
trade_size: "0.001"
log_level: INFO
params:
  buy_interval_bars: 60
  buy_amount: "10"
```

`configs/paper/timesfm_swing.yaml`:
```yaml
strategy: timesfm_swing
instrument_id: BTCUSDT.BINANCE
bar_type: BTCUSDT.BINANCE-1-MINUTE-LAST-EXTERNAL
trade_size: "0.001"
log_level: INFO
params:
  fast_ema: 5
  slow_ema: 30
```

`configs/paper/hybrid_sma_r10.yaml`:
```yaml
strategy: hybrid_sma_r10
instrument_id: BTCUSDT.BINANCE
bar_type: BTCUSDT.BINANCE-1-MINUTE-LAST-EXTERNAL
# trade_size intentionally omitted — HybridSMA sizes from equity.
trade_size: null
log_level: INFO
params:
  sma_fast: 10
  sma_slow: 30
  stop_fast: "0.05"
  stop_slow: "0.10"
```

`configs/paper/timesfm_grid.yaml`:
```yaml
strategy: timesfm_grid
instrument_id: BTCUSDT.BINANCE
bar_type: BTCUSDT.BINANCE-1-MINUTE-LAST-EXTERNAL
trade_size: "0.001"
log_level: INFO
params: {}
```

`configs/paper/rvs_swing.yaml`:
```yaml
strategy: rvs_swing
instrument_id: BTCUSDT.BINANCE
bar_type: BTCUSDT.BINANCE-1-MINUTE-LAST-EXTERNAL
trade_size: "0.001"
log_level: INFO
params: {}
```

`configs/paper/shock_guard.yaml`:
```yaml
strategy: shock_guard
instrument_id: BTCUSDT.BINANCE
bar_type: BTCUSDT.BINANCE-1-MINUTE-LAST-EXTERNAL
trade_size: "0.001"
log_level: INFO
params: {}
```

- [ ] **Step 4: Run — expect PASS**

From `nautilus/`:
```bash
uv run python -m pytest ../tests/cli/test_paper_trade_configs.py -v
```
Expected: 8 passed.

- [ ] **Step 5: Run full suite + lint**

```bash
cd nautilus && uv run python -m pytest ../tests -q && cd -
make lint
```
Expected: **288 passed, 19 skipped**. Lint green.

- [ ] **Step 6: Commit**

```bash
git add configs/paper/ tests/cli/test_paper_trade_configs.py
git commit -m "feat(paper-trade): add committed YAML configs for all 8 strategies"
```

**Expected test count after Task 4: 288 passed, 19 skipped.**

---

## Task 5 — Bad YAML paths → `BadParameter` with useful messages

**Goal:** Two error paths surface as Typer usage errors, not tracebacks:

- Unknown strategy in YAML (already handled by the CLI — this task only adds the test guard).
- Unknown top-level key in YAML (already remapped via the `try/except msgspec.ValidationError` block added in Task 3 — again, test-only).

Missing-file is already handled by Typer's `exists=True` on the `--config` option; the test just confirms the UX.

**Files:**
- Modify: `tests/cli/test_paper_trade_cli.py`

- [ ] **Step 1: Write three error-path tests**

Append to `tests/cli/test_paper_trade_cli.py`:

```python
def test_paper_trade_config_missing_file_is_usage_error(tmp_path):
    """Nonexistent config file → Typer exit_code != 0 mentioning the path.

    This is served by typer.Option(exists=True) on --config; the test guards
    that the option definition still carries the `exists=True` flag.
    """
    runner = CliRunner()
    bogus = tmp_path / "does-not-exist.yaml"
    result = runner.invoke(app, ["paper-trade", "--config", str(bogus)])
    assert result.exit_code != 0
    assert "does-not-exist" in result.output or "does not exist" in result.output


def test_paper_trade_config_unknown_strategy_is_usage_error(tmp_path):
    """Unknown strategy in YAML → BadParameter listing valid names."""
    yaml_path = tmp_path / "run.yaml"
    yaml_path.write_text(
        "strategy: nonexistent_bot\n"
        "instrument_id: X\n"
        "bar_type: Y\n"
        "trade_size: \"0.001\"\n"
    )
    runner = CliRunner()
    result = runner.invoke(app, ["paper-trade", "--config", str(yaml_path)])
    assert result.exit_code != 0
    assert "nonexistent_bot" in result.output


def test_paper_trade_config_unknown_yaml_field_is_usage_error(tmp_path):
    """Unknown top-level YAML field → BadParameter (not raw ValidationError)."""
    yaml_path = tmp_path / "run.yaml"
    yaml_path.write_text(
        "strategy: ema_cross\n"
        "instrument_id: X\n"
        "bar_type: Y\n"
        "trade_size: \"0.001\"\n"
        "bogus_field: 1\n"
    )
    runner = CliRunner()
    result = runner.invoke(app, ["paper-trade", "--config", str(yaml_path)])
    assert result.exit_code != 0
    assert "bogus_field" in result.output
```

- [ ] **Step 2: Run — expect PASS on first run**

All three paths are already implemented in the Task 3 rewrite:
- `exists=True` on `--config` handles missing file.
- `typer.BadParameter` remap around `load_run_config()` handles the unknown-field case.
- `typer.BadParameter` at the `_RUNNERS` lookup handles the unknown-strategy case.

From `nautilus/`:
```bash
uv run python -m pytest ../tests/cli/test_paper_trade_cli.py -v
```
Expected: all CLI tests pass. If any fail, the Task 3 rewrite is missing a branch — fix the CLI (do NOT weaken the tests).

- [ ] **Step 3: Run full suite + lint**

```bash
cd nautilus && uv run python -m pytest ../tests -q && cd -
make lint
```
Expected: **291 passed, 19 skipped**. Lint green.

- [ ] **Step 4: Commit**

```bash
git add tests/cli/test_paper_trade_cli.py
git commit -m "test(cli): guard --config error paths (missing file, unknown strategy, unknown field)"
```

**Expected test count after Task 5: 291 passed, 19 skipped.**

---

## Task 6 — Update Makefile + CLAUDE.md references to the flag form

**Goal:** No caller of `nt paper-trade` should reference the deleted flag form after this task.

**Files:**
- Possibly modify: `Makefile` (only if a `paper-trade`/`live-*` target uses the flag form)
- Possibly modify: `CLAUDE.md` (only if it documents the flag form)

- [ ] **Step 1: Grep for flag-form usage**

From repo root:
```bash
grep -n -- "--strategy\|--instrument-id\|--bar-type\|--trade-size\|--fast-ema\|--slow-ema\|--upper-price\|--lower-price\|--grid-levels\|--buy-interval-bars\|--buy-amount\|--sma-fast\|--sma-slow\|--stop-fast\|--stop-slow" Makefile CLAUDE.md 2>/dev/null || true
```

Review each hit. Three possible outcomes per line:

1. **Line references `nt paper-trade` with flags** → rewrite to `nt paper-trade --config configs/paper/<name>.yaml`. If the Make target accepted a strategy name, switch it to accept a config path, e.g.:
   ```make
   # Before
   paper-trade:
   	cd nautilus && uv run nt paper-trade --strategy $(STRATEGY) --instrument-id ... --bar-type ... --trade-size ...

   # After
   paper-trade:
   	cd nautilus && uv run nt paper-trade --config ../configs/paper/$(STRATEGY).yaml
   ```
2. **Line references `nt backtest` with similar-named flags** → leave alone. `nt backtest` is not in scope for this PR.
3. **Unrelated to `nt paper-trade`** → leave alone.

- [ ] **Step 2: If `make paper-trade` or similar was rewritten, smoke it**

```bash
# Dry-run the target's command to confirm it assembles correctly.
make -n paper-trade STRATEGY=ema_cross
```
Expected output should contain `--config ../configs/paper/ema_cross.yaml` (or equivalent). Do NOT actually run the target — it would boot a TradingNode.

- [ ] **Step 3: Lint**

```bash
make lint
```
Expected: green. (Makefile and Markdown changes don't affect Python lint, but re-run to confirm no accidental .py edits.)

- [ ] **Step 4: Commit (skip if no files changed)**

```bash
git status --short Makefile CLAUDE.md
# If either is modified:
git add Makefile CLAUDE.md
git commit -m "docs(paper-trade): update callers to --config form"
```

If the grep in Step 1 produced no `nt paper-trade`-related hits, skip Step 4 entirely and note "no callers to update" in the Task-6 summary when reporting to the controller.

**Expected test count after Task 6: unchanged — 291 passed, 19 skipped.**

---

## Task 7 — Renumber sub-project B roadmap

**Goal:** Update the existing sub-project B implementation plan doc (`docs/superpowers/plans/2026-04-21-subproject-b-implementation.md`) so PR 6/7 are renumbered to PR 7/8 (this YAML work becomes PR 6) and add a pointer to this plan.

**Files:**
- Modify: `docs/superpowers/plans/2026-04-21-subproject-b-implementation.md`

- [ ] **Step 1: Locate the section headers**

From repo root:
```bash
grep -n "^## PR 6\|^## PR 7\|^### Task 6\.\|^### Task 7\." docs/superpowers/plans/2026-04-21-subproject-b-implementation.md
```

Record the line numbers of `## PR 6 — Kronos migration + parity gate`, `## PR 7 — CI opt-in smoke …`, and the four `### Task 6.x` and four `### Task 7.x` headers.

- [ ] **Step 2: Edit section headers — shift up by one**

**Important ordering:** do PR 7 → PR 8 edits FIRST (so the regex `## PR 7` only matches the old PR 7 section), then PR 6 → PR 7. Same for Task 7.x → 8.x, then Task 6.x → 7.x.

Edits to make:

- `## PR 7 — CI opt-in smoke + \`make smoke-paper-order\` + runbook + roadmap` → `## PR 8 — CI opt-in smoke + \`make smoke-paper-order\` + runbook + roadmap`
- `### Task 7.1 — Register \`binance_testnet\` pytest marker` → `### Task 8.1 …`
- `### Task 7.2 — CI node-boot smoke for all 8` → `### Task 8.2 …`
- `### Task 7.3 — Manual \`make smoke-paper-order STRATEGY=<name>\` target` → `### Task 8.3 …`
- `### Task 7.4 — Runbook \`docs/runbooks/paper-trade.md\`` → `### Task 8.4 …`

Then:

- `## PR 6 — Kronos migration + parity gate` → `## PR 7 — Kronos migration + parity gate`
- `### Task 6.1 — Write the parity test first (pre-implementation)` → `### Task 7.1 …`
- `### Task 6.2 — Implement \`KronosPaperTradeRunner\`` → `### Task 7.2 …`
- `### Task 6.3 — Delete the quarantined script` → `### Task 7.3 …`
- `### Task 6.4 — Open PR 6` → `### Task 7.4 — Open PR 7`

Also fix internal cross-references:
```bash
grep -n "PR 6\|PR 7" docs/superpowers/plans/2026-04-21-subproject-b-implementation.md
```
Review each hit and update if it refers to the renumbered PRs (e.g. "Depends on: PR 6 merged" referring to Kronos → "Depends on: PR 7 merged").

- [ ] **Step 3: Insert pointer to this plan**

Immediately before the renamed `## PR 7 — Kronos …` header, insert:

```markdown
## PR 6 — YAML run configs

See standalone plan: `docs/superpowers/plans/2026-04-22-pr6-yaml-run-configs-implementation.md`

Spec: `docs/superpowers/specs/2026-04-22-pr6-yaml-run-configs-design.md`

Replaces the 16-flag CLI with `nt paper-trade --config configs/paper/<name>.yaml`. The flag path is deleted — no deprecation window. Completed before Kronos lands so Kronos can ship a config file instead of ~17 new flags.

---
```

- [ ] **Step 4: Verify with a structural grep**

```bash
grep -n "^## PR " docs/superpowers/plans/2026-04-21-subproject-b-implementation.md
```
Expected: one `## PR N —` line per PR, N = 1..8, in order with no gaps.

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/plans/2026-04-21-subproject-b-implementation.md \
        docs/superpowers/plans/2026-04-22-pr6-yaml-run-configs-implementation.md \
        docs/superpowers/specs/2026-04-22-pr6-yaml-run-configs-design.md
git commit -m "docs(plan): renumber sub-project B PRs; add PR 6 YAML configs plan"
```

Note: this is the commit where the new spec and plan files (currently untracked in the worktree) enter git. They travel together.

**Expected test count after Task 7: unchanged — 291 passed, 19 skipped.**

---

## Task 8 — Pre-submit review, push, open PR

**Goal:** Final local gate, push, and open the PR on GitHub. Trigger the standing "review PR feedback" task per memory rule `feedback_pr_feedback_review.md`.

**Files:** none modified in this task — only tooling and VCS actions.

- [ ] **Step 1: Confirm branch state**

From repo root:
```bash
git log --oneline origin/main..HEAD
git status --short
```
Expected: 6–7 commits ahead of main (Tasks 1, 2, 3, 4, 5, 7, and optionally 6 if the Makefile needed updating). Working tree clean.

- [ ] **Step 2: Full test suite + lint (pre-push gate)**

```bash
cd nautilus && uv run python -m pytest ../tests -q && cd -
make lint
```
Expected: **291 passed, 19 skipped**. Lint green.

- [ ] **Step 3: Dispatch holistic pre-submit reviewer (sub-agent)**

The controller thread dispatches a pre-submit reviewer subagent (use `pr-review-toolkit:code-reviewer`) scoped to "PR 6 — YAML run configs end-to-end". The review prompt must check:

- Flag path is fully deleted: `grep -n "^    strategy:\s*str\|--strategy\|--instrument-id" nautilus/src/nautilus_trading/cli/paper_trade.py` returns zero hits.
- Spec non-goals all respected: no changes to `StrategyConfigBuilder`, `STRATEGY_BUILDERS`, `_RUNNERS` keys, or any runner class under `strategies/crypto/*_paper.py`.
- All 8 committed configs pass through the parametrized dispatch test.
- All YAML load error paths surface as `BadParameter` (no raw tracebacks).
- Renumbering is complete with no dangling cross-references to old PR numbers.

If reviewer reports BLOCKING or IMPORTANT findings, address them in a fix commit before pushing.

- [ ] **Step 4: Push**

```bash
git push -u origin subproject-b/pr6-yaml-run-configs
```

- [ ] **Step 5: Open PR**

```bash
gh pr create --title "feat(paper-trade): YAML run configs for nt paper-trade" --body "$(cat <<'EOF'
## Summary

Replaces the 16-option, 8-branch `nt paper-trade` flag ladder with a single `--config configs/paper/<name>.yaml` option.

- New `PaperRunConfig` msgspec Struct (`forbid_unknown_fields=True`) — strict top-level schema, loose `params: dict` bucket for strategy-specific values.
- 8 committed example configs under `configs/paper/` — one runnable per strategy.
- **Flag path is fully deleted.** No deprecation window. Per-user directive: "no fallbacks."
- Reuses `_RUNNERS` registry and `StrategyConfigBuilder.build()` unchanged. No strategy/runner code touched.

## Scope

Commits (6–7):

- Task 1: `feat(paper-trade): add PaperRunConfig YAML schema + loader`
- Task 2: `test(paper-trade): guard PaperRunConfig schema strictness`
- Task 3: `refactor(cli): replace paper-trade flag ladder with --config YAML`
- Task 4: `feat(paper-trade): add committed YAML configs for all 8 strategies`
- Task 5: `test(cli): guard --config error paths (missing file, unknown strategy, unknown field)`
- Task 6 (conditional): `docs(paper-trade): update callers to --config form`
- Task 7: `docs(plan): renumber sub-project B PRs; add PR 6 YAML configs plan`

## Test plan

- [x] `cd nautilus && uv run python -m pytest ../tests -q` → **291 passed, 19 skipped** (was 286; net +5 after deleting 11 obsolete flag tests and adding 16 YAML-path tests)
- [x] `make lint` → ruff + mypy + vulture green
- [x] Each committed YAML round-trips through `test_committed_config_dispatches_to_runner` (parametrized, 8 cases)
- [x] Schema strictness guarded: unknown field → `ValidationError`, missing required → `ValidationError`, `trade_size: null` → `None`
- [x] CLI error UX: missing file, unknown strategy, unknown YAML field all → `BadParameter` (no raw tracebacks)

## Breaking change

The flag form of `nt paper-trade` (`--strategy`, `--instrument-id`, `--bar-type`, `--trade-size`, `--fast-ema`, `--slow-ema`, `--upper-price`, `--lower-price`, `--grid-levels`, `--buy-interval-bars`, `--buy-amount`, `--sma-fast`, `--sma-slow`, `--stop-fast`, `--stop-slow`, `--duration`, `--log-level`) no longer works. Use `nt paper-trade --config configs/paper/<name>.yaml` instead. Example configs are committed under `configs/paper/`.

## Non-goals (explicit)

- No changes to `StrategyConfigBuilder` or `_RUNNERS` registry
- No changes to `nt backtest`
- No changes to runner classes under `strategies/crypto/*_paper.py`

## Follow-ups (not in this PR)

- PR 7 (was PR 6): Kronos migration + parity gate — ships a Kronos config in `configs/paper/` instead of new flags
- PR 8 (was PR 7): CI smoke + runbook + roadmap
EOF
)"
```

- [ ] **Step 6: Auto-create the PR-feedback-review task**

Per memory rule `feedback_pr_feedback_review.md`, the controller thread calls `TaskCreate` with subject `Review PR #N feedback before merge` (N = the new PR number), marks it `in_progress`, and dispatches a general-purpose agent to audit local + Copilot feedback. Do NOT advance to merge until Copilot has reviewed AND every REAL comment has been addressed.

**Expected state after Task 8:**
- PR open on GitHub
- Task "Review PR #N feedback before merge" exists and is `in_progress`
- 291 passed, 19 skipped. Lint green. Branch clean.

---

## Self-review (plan author's checklist)

**Spec coverage:**

| Spec requirement | Plan task |
|---|---|
| `PaperRunConfig` msgspec Struct | Task 1 |
| Top-level `forbid_unknown_fields=True` | Task 2 (guard) |
| `trade_size: str \| None = None` + null decoding | Task 2 |
| `load_run_config(path)` | Task 1 |
| Single `--config` CLI option | Task 3 |
| Flag path fully deleted | Task 3 |
| YAML dispatch reuses `_RUNNERS` | Task 3 |
| Trade-size None-drop for HybridSMA | Task 3 |
| `FileNotFoundError` handled by Typer `exists=True` | Task 3 + Task 5 (guard) |
| `ValidationError` → `BadParameter` | Task 3 + Task 5 (guard) |
| Unknown strategy in YAML → `BadParameter` | Task 3 + Task 5 (guard) |
| 8 committed configs under `configs/paper/` | Task 4 |
| Schema round-trip + bad-path tests | Tasks 1, 2, 5 |
| Parametrized dispatch test per config | Task 4 |
| Delete obsolete flag-based tests | Task 3 |
| Update Makefile + CLAUDE.md callers | Task 6 |
| Renumber sub-project B roadmap | Task 7 |
| Keep `StrategyConfigBuilder` unchanged | Enforced by task boundaries (no task modifies it) |
| Keep `nt backtest` unchanged | Enforced by task boundaries |

All spec requirements are covered.

**Placeholder scan:** no TBD / TODO / "handle edge cases" in step bodies. Task 6 is explicitly conditional ("skip if no files changed") — that's guided branching, not a placeholder.

**Type consistency:** `PaperRunConfig` field names (`strategy`, `instrument_id`, `bar_type`, `trade_size`, `log_level`, `duration`, `params`) match across Tasks 1, 2, 3, 4, 5. `load_run_config(path: Path) -> PaperRunConfig` signature stable. `_RUNNERS` and `StrategyConfigBuilder` contracts untouched. No drift.
