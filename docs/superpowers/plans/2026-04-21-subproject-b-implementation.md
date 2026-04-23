# Sub-project B — Paper-trade Testbed — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `nt paper-trade` so every registered crypto `Strategy` subclass runs against Binance Spot Testnet with identical config builders as `nt backtest`. Zero real-money code paths remain.

**Architecture:** Extract the working Binance-testnet wiring out of the existing `nautilus/src/nautilus_trading/live/runner.py` (built during sub-project A) into a new `paper_trade/` package with a `PaperTradeRunner` ABC that mirrors `BacktestRunner`. Retire `nt live` (real money is out of scope for this sub-project). Add one new blocker fix — `round_to_tick()` — and promote the two already-fixed blockers (Ed25519, InstrumentProvider) from "latent code" to "explicitly regression-tested code". Ship 7 small PRs, each green on `make lint && make test-unit`.

**Tech Stack:** Python 3.13, uv, pytest, Nautilus Trader 1.224.0, Typer CLI, `python-dotenv` (new dep), `BinanceKeyType.ED25519`, `BinanceInstrumentProviderConfig(load_ids=...)`, `BinanceAccountType.SPOT`, `BinanceEnvironment.TESTNET`.

**Spec:** `docs/superpowers/specs/2026-04-21-subproject-b-design.md` (APPROVED 2026-04-21, 7 PRs).

**Branch convention.** One branch per PR, named `subproject-b/pr<N>-<slug>` (e.g. `subproject-b/pr1-foundation`). Each PR rebases on the previous merged PR (or `main` for PR 1). Every PR ends with the tree green: `make lint && make test-unit`.

**Global commands.** Unless noted, all shell commands run from the repo root. Pytest runs from `nautilus/`: `cd nautilus && uv run pytest ...`. Testnet-gated tests (`@pytest.mark.binance_testnet`) are opt-in and are NOT run as part of `make test-unit`.

**PR submission convention (applies to every "Open PR N" task).** Before `git push` and `gh pr create`, dispatch the reviewer subagent to run `/ultrareview` and `/simplify` against the PR branch and address any issues it surfaces. The reviewer subagent (not the human, not the main thread) owns those two commands — it invokes them via its Skill tool, triages findings, and either (a) fixes them directly and commits, or (b) reports back "needs decision" for items that require a judgment call. Only after the reviewer reports clean does the PR get pushed.

Concrete dispatch shape:

```
Agent(
  subagent_type: "pr-review-toolkit:code-reviewer",
  description: "Pre-submit review for PR N",
  prompt: "Run /ultrareview and /simplify against branch <branch>.
           Address the issues you raise: fix straightforward ones
           with commits on this branch; escalate judgment calls
           back to main. Confirm `make lint && cd nautilus && uv
           run python -m pytest ../tests/` still pass after your
           changes. Then report DONE or NEEDS_DECISION with the
           list of items requiring human input."
)
```

**Worker briefing — zero-context primer.** You are implementing against a Nautilus Trader v1.224.0 codebase. Strategies live in `strategies/crypto/*.py` and are **venue-agnostic** — the same `Strategy` subclass runs under a backtest `BacktestEngine` or a live `TradingNode` with no code change. Sub-project A shipped the `BacktestRunner` ABC at `nautilus/src/nautilus_trading/backtest/runner_base.py` and the `STRATEGY_BUILDERS` Protocol registry at `nautilus/src/nautilus_trading/cli/_strategy_configs.py`. Your job is to add the paper-trade counterpart: a `PaperTradeRunner` ABC and an `nt paper-trade` command that reuses `STRATEGY_BUILDERS` unchanged. Read the spec first.

---

## Existing code you must read before starting

Read these files once before PR 1. They define the patterns you will mirror and the code you will refactor.

1. `docs/superpowers/specs/2026-04-21-subproject-b-design.md` — the spec.
2. `nautilus/src/nautilus_trading/backtest/runner_base.py` — the ABC your new `PaperTradeRunner` mirrors.
3. `nautilus/src/nautilus_trading/live/runner.py` — the working Binance-testnet wiring you are extracting. PR 1 deletes this file.
4. `nautilus/src/nautilus_trading/cli/_strategy_configs.py` — the `STRATEGY_BUILDERS` registry you will reuse unchanged.
5. `nautilus/src/nautilus_trading/cli/_common.py` — `_STRATEGY_CLASSES` dict and `_ensure_project_root_on_path()` / `_resolve_strategy_paths()` helpers.
6. `nautilus/src/nautilus_trading/cli/backtest.py` — Typer-command pattern your `cli/paper_trade.py` mirrors (lazy imports, arg surface).
7. `nautilus/src/nautilus_trading/cli/live.py` — existing `nt live` command. PR 1 deletes this file.
8. `strategies/crypto/kronos/paper_trade.py` — the quarantined Kronos script PR 7 replaces and deletes.
9. `.env.example` — the template. PR 1 extends it.

---

## PR 1 — Foundation: `paper_trade/` package + retire `nt live`

**Depends on:** none. First PR, lands on `main`.

**Scope:** Create the `paper_trade/` package skeleton by extracting the working Binance-testnet wiring out of `live/runner.py` into three purpose-built modules. Wire `python-dotenv`-based secrets loading. Remove `cli/live.py`, `live/runner.py`, and the `live` sub-command registration — real-money live trading is out of scope for sub-project B and beyond, and keeping a second path invites drift. Ship this PR with no new runtime behavior beyond "`nt paper-trade` stub that says 'not yet implemented'"; the blocker-fix tests follow in PR 2.

**Why this PR is safe despite deleting `nt live`:** the only existing consumer of `nt live` is the quarantined `strategies/crypto/kronos/paper_trade.py` script, which imports directly from `nautilus_trader` — not from `nt live`. No test in `tests/` exercises `nt live` end-to-end; the sub-project A plan (Task 1.1) only uses it as a pytest-collect hermeticity probe, not as a live-order target. Confirm with `grep -r "nt live\|cli.live\|from nautilus_trading.live" nautilus/ strategies/ tests/ docs/ Makefile` before deleting — if any hit exists outside the files this PR itself deletes, stop and ask.

### Task 1.1 — Create `paper_trade/` package skeleton

**Files:**
- Create: `nautilus/src/nautilus_trading/paper_trade/__init__.py`
- Create: `nautilus/src/nautilus_trading/paper_trade/runner_base.py`
- Create: `tests/paper_trade/__init__.py`
- Create: `tests/paper_trade/test_runner_base.py`

- [ ] **Step 1: Create empty package init**

Write to `nautilus/src/nautilus_trading/paper_trade/__init__.py`:

```python
"""Binance Spot Testnet paper-trade runners.

Mirrors nautilus_trading.backtest — same STRATEGY_BUILDERS registry, different
execution path (TradingNode + Binance Spot Testnet vs. BacktestEngine + SimulatedVenue).
"""
```

- [ ] **Step 2: Write the failing ABC test**

Write to `tests/paper_trade/test_runner_base.py`:

```python
"""ABC behavior: subclasses must override main()."""

from __future__ import annotations

import pytest

from nautilus_trading.paper_trade.runner_base import PaperTradeRunner


def test_paper_trade_runner_is_abstract():
    """Instantiating the ABC directly must raise TypeError."""
    with pytest.raises(TypeError, match="abstract"):
        PaperTradeRunner()  # type: ignore[abstract]


def test_paper_trade_runner_subclass_without_main_is_abstract():
    """A subclass that forgets to override main() is still abstract."""

    class Incomplete(PaperTradeRunner):
        pass

    with pytest.raises(TypeError, match="abstract"):
        Incomplete()  # type: ignore[abstract]


def test_paper_trade_runner_subclass_with_main_instantiates():
    """A subclass that overrides main() instantiates cleanly."""

    class Concrete(PaperTradeRunner):
        def main(self) -> None:
            return None

    assert Concrete().main() is None
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `cd nautilus && uv run pytest ../tests/paper_trade/test_runner_base.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'nautilus_trading.paper_trade.runner_base'`.

- [ ] **Step 4: Write the minimal ABC**

Write to `nautilus/src/nautilus_trading/paper_trade/runner_base.py`:

```python
"""PaperTradeRunner ABC — parallel to BacktestRunner."""

from __future__ import annotations

from abc import ABC, abstractmethod


class PaperTradeRunner(ABC):
    """Base class for Binance Spot Testnet paper-trade runners.

    Each concrete subclass composes its own TradingNode, subscribes to data,
    attaches one strategy (plus optional actor), and runs to completion.
    No default main() body — see sub-project A PR #16 for the rationale.
    """

    @abstractmethod
    def main(self) -> None:
        """Compose TradingNode, subscribe data, add strategy, run."""
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd nautilus && uv run pytest ../tests/paper_trade/test_runner_base.py -v`
Expected: PASS, 3 tests.

- [ ] **Step 6: Commit**

```bash
git add nautilus/src/nautilus_trading/paper_trade/__init__.py \
        nautilus/src/nautilus_trading/paper_trade/runner_base.py \
        tests/paper_trade/__init__.py \
        tests/paper_trade/test_runner_base.py
git commit -m "feat(paper_trade): add PaperTradeRunner ABC"
```

### Task 1.2 — Add `python-dotenv` dependency and secrets loader

**Files:**
- Modify: `nautilus/pyproject.toml` (add `python-dotenv` to main deps)
- Create: `nautilus/src/nautilus_trading/paper_trade/secrets.py`
- Create: `tests/paper_trade/test_secrets.py`

- [ ] **Step 1: Add `python-dotenv` to project dependencies**

In `nautilus/pyproject.toml`, find the `[project]` → `dependencies` array and add `"python-dotenv>=1.0.0"` (keep alphabetical order if the existing list is sorted). Then run:

```bash
cd nautilus && uv sync
```

Expected: `Resolved N packages ... Installed python-dotenv ...`.

- [ ] **Step 2: Write the failing tests**

Write to `tests/paper_trade/test_secrets.py`:

```python
"""`.env.local` loader tests."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from nautilus_trading.paper_trade.secrets import load_dotenv_local


def test_load_dotenv_local_missing_file_is_no_op(tmp_path, monkeypatch):
    """If .env.local is absent, loader returns False and does not raise."""
    monkeypatch.chdir(tmp_path)
    assert load_dotenv_local() is False


def test_load_dotenv_local_populates_env(tmp_path, monkeypatch):
    """When .env.local exists, its keys appear in os.environ."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env.local").write_text(
        "BINANCE_TESTNET_API_KEY=tk_abc\n"
        "BINANCE_TESTNET_API_SECRET=sk_xyz\n"
    )
    monkeypatch.delenv("BINANCE_TESTNET_API_KEY", raising=False)
    monkeypatch.delenv("BINANCE_TESTNET_API_SECRET", raising=False)

    assert load_dotenv_local() is True
    assert os.environ["BINANCE_TESTNET_API_KEY"] == "tk_abc"
    assert os.environ["BINANCE_TESTNET_API_SECRET"] == "sk_xyz"


def test_load_dotenv_local_does_not_override_existing(tmp_path, monkeypatch):
    """Existing env vars win over .env.local (so CI secrets aren't overwritten)."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env.local").write_text("BINANCE_TESTNET_API_KEY=from_file\n")
    monkeypatch.setenv("BINANCE_TESTNET_API_KEY", "from_env")

    load_dotenv_local()
    assert os.environ["BINANCE_TESTNET_API_KEY"] == "from_env"


def test_load_dotenv_local_custom_path(tmp_path, monkeypatch):
    """An explicit path is honored."""
    envfile = tmp_path / "custom.env"
    envfile.write_text("FOO=bar\n")
    monkeypatch.delenv("FOO", raising=False)

    assert load_dotenv_local(path=envfile) is True
    assert os.environ["FOO"] == "bar"
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd nautilus && uv run pytest ../tests/paper_trade/test_secrets.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'nautilus_trading.paper_trade.secrets'`.

- [ ] **Step 4: Write the secrets loader**

Write to `nautilus/src/nautilus_trading/paper_trade/secrets.py`:

```python
"""`.env.local` loader for Binance Testnet credentials.

Design: existing environment variables win. We load from `.env.local` only
to populate what is *missing*, so a CI run with secrets in the environment
is never overwritten by a stale committed-adjacent file.
"""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv


def load_dotenv_local(path: Path | str | None = None) -> bool:
    """Load `.env.local` into os.environ if present.

    Parameters
    ----------
    path
        Optional explicit path. Defaults to `./.env.local` in the current
        working directory.

    Returns
    -------
    bool
        True if a file was found and loaded; False if absent.
    """
    target = Path(path) if path is not None else Path.cwd() / ".env.local"
    if not target.exists():
        return False
    # override=False preserves pre-set environment variables (e.g. CI secrets).
    return bool(load_dotenv(dotenv_path=target, override=False))
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd nautilus && uv run pytest ../tests/paper_trade/test_secrets.py -v`
Expected: PASS, 4 tests.

- [ ] **Step 6: Commit**

```bash
git add nautilus/pyproject.toml nautilus/uv.lock \
        nautilus/src/nautilus_trading/paper_trade/secrets.py \
        tests/paper_trade/test_secrets.py
git commit -m "feat(paper_trade): add .env.local loader via python-dotenv"
```

### Task 1.3 — Extract `build_paper_trade_node_config()` from `live/runner.py`

**Files:**
- Create: `nautilus/src/nautilus_trading/paper_trade/node_config.py`
- Reference only (will delete in Task 1.5): `nautilus/src/nautilus_trading/live/runner.py`

This task is a **pure extraction**: we copy the working `build_live_config()` / `run_live()` / `_check_api_keys()` bodies into `paper_trade/node_config.py`, renaming and adjusting the API surface. No behavior change, no blocker-fix work (that's PR 2). Tests come in PR 2.

- [ ] **Step 1: Write the extracted node-config module**

Write to `nautilus/src/nautilus_trading/paper_trade/node_config.py`:

```python
"""TradingNodeConfig builder for Binance Spot Testnet paper-trade runs.

Centralizes the three Binance-Testnet blocker fixes so every PaperTradeRunner
subclass inherits them for free:

    1. Ed25519 key type for user-data WebSocket (§7.1 of spec).
    2. InstrumentProviderConfig populated with the run's target instrument (§7.2).
    3. Tick-size rounding helper for LIMIT orders (§7.3; added in PR 2).
"""

from __future__ import annotations

import os
import signal
import sys
from typing import Any

from nautilus_trader.adapters.binance import (
    BINANCE,
    BinanceInstrumentProviderConfig,
    BinanceLiveDataClientFactory,
    BinanceLiveExecClientFactory,
)
from nautilus_trader.adapters.binance.common.enums import BinanceAccountType, BinanceEnvironment
from nautilus_trader.adapters.binance.config import (
    BinanceDataClientConfig,
    BinanceExecClientConfig,
    BinanceKeyType,
)
from nautilus_trader.config import (
    ImportableStrategyConfig,
    LoggingConfig,
    TradingNodeConfig,
)
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.identifiers import InstrumentId


def build_paper_trade_node_config(
    *,
    strategy_path: str,
    config_path: str,
    strategy_config: dict[str, Any],
    instrument_id: str,
    log_level: str = "INFO",
    trader_id: str = "PAPER-TRADER-001",
) -> TradingNodeConfig:
    """Build a TradingNodeConfig for Binance Spot Testnet paper trading.

    Parameters
    ----------
    strategy_path : str
        Full import path for the strategy class
        (e.g. "strategies.crypto.grid_bot:GridBotStrategy").
    config_path : str
        Full import path for the strategy's config class.
    strategy_config : dict
        Strategy configuration parameters — emitted by STRATEGY_BUILDERS[name].build(args).
    instrument_id : str
        Instrument ID to load into the venue cache (e.g. "BTCUSDT.BINANCE").
    log_level : str
        Logging level.
    trader_id : str
        Trader identifier.
    """
    account_type = BinanceAccountType.SPOT
    environment = BinanceEnvironment.TESTNET
    instrument_provider = BinanceInstrumentProviderConfig(
        load_ids=frozenset([InstrumentId.from_str(instrument_id)]),
    )

    return TradingNodeConfig(
        trader_id=trader_id,
        logging=LoggingConfig(log_level=log_level),
        data_clients={
            BINANCE: BinanceDataClientConfig(
                account_type=account_type,
                environment=environment,
                key_type=BinanceKeyType.ED25519,
                instrument_provider=instrument_provider,
            ),
        },
        exec_clients={
            BINANCE: BinanceExecClientConfig(
                account_type=account_type,
                environment=environment,
                key_type=BinanceKeyType.ED25519,
                instrument_provider=instrument_provider,
            ),
        },
        strategies=[
            ImportableStrategyConfig(
                strategy_path=strategy_path,
                config_path=config_path,
                config=strategy_config,
            ),
        ],
    )


def run_paper_trade(config: TradingNodeConfig) -> None:
    """Start a paper-trade node. Blocks until SIGINT/SIGTERM."""
    _check_testnet_api_keys()

    node = TradingNode(config=config)
    node.add_data_client_factory(BINANCE, BinanceLiveDataClientFactory)
    node.add_exec_client_factory(BINANCE, BinanceLiveExecClientFactory)
    node.build()

    def _shutdown(_signum, _frame):
        print("\nShutting down paper-trade node...")
        node.stop()
        node.dispose()
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    node.run()


def _check_testnet_api_keys() -> None:
    """Fail fast with an actionable message if Testnet keys are missing."""
    key = os.environ.get("BINANCE_TESTNET_API_KEY")
    secret = os.environ.get("BINANCE_TESTNET_API_SECRET")
    if not key or not secret:
        print("ERROR: Binance Testnet API keys not found in environment.")
        print("Set BINANCE_TESTNET_API_KEY and BINANCE_TESTNET_API_SECRET,")
        print("or put them in .env.local at the repo root.")
        print("Get testnet keys at: https://testnet.binance.vision/")
        sys.exit(1)
```

- [ ] **Step 2: Sanity-check the import**

Run: `cd nautilus && uv run python -c "from nautilus_trading.paper_trade.node_config import build_paper_trade_node_config, run_paper_trade; print('ok')"`
Expected: `ok`.

- [ ] **Step 3: Commit**

```bash
git add nautilus/src/nautilus_trading/paper_trade/node_config.py
git commit -m "feat(paper_trade): extract TradingNodeConfig builder for Binance Spot Testnet"
```

### Task 1.4 — Stub `nt paper-trade` Typer command

**Files:**
- Create: `nautilus/src/nautilus_trading/cli/paper_trade.py`
- Modify: `nautilus/src/nautilus_trading/cli/__init__.py`
- Create: `tests/cli/test_paper_trade_cli.py`

The CLI is a stub in this PR — it parses args, loads secrets, and exits with "not yet wired" until PR 3 connects runners. This keeps PR 1 small and reviewable while proving the command registration works.

- [ ] **Step 1: Write the failing CLI test**

Write to `tests/cli/test_paper_trade_cli.py`:

```python
"""`nt paper-trade` registration and argument-parsing smoke."""

from __future__ import annotations

from typer.testing import CliRunner

from nautilus_trading.cli import app


def test_paper_trade_command_is_registered():
    """The `paper-trade` subcommand appears in --help."""
    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "paper-trade" in result.stdout


def test_paper_trade_help_shows_required_args():
    """paper-trade --help mentions --strategy and --instrument-id."""
    runner = CliRunner()
    result = runner.invoke(app, ["paper-trade", "--help"])
    assert result.exit_code == 0
    assert "--strategy" in result.stdout
    assert "--instrument-id" in result.stdout


def test_paper_trade_unknown_strategy_exits_nonzero():
    """Unknown strategy name → usage error."""
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "paper-trade",
            "--strategy", "nonexistent_strategy",
            "--instrument-id", "BTCUSDT.BINANCE",
            "--bar-type", "BTCUSDT.BINANCE-1-MINUTE-LAST-EXTERNAL",
            "--trade-size", "0.001",
        ],
    )
    assert result.exit_code != 0
```

- [ ] **Step 2: Run — expect collection failure**

Run: `cd nautilus && uv run pytest ../tests/cli/test_paper_trade_cli.py -v`
Expected: FAIL (command not yet registered).

- [ ] **Step 3: Write the stub CLI module**

Write to `nautilus/src/nautilus_trading/cli/paper_trade.py`:

```python
"""`nt paper-trade` — Binance Spot Testnet paper-trade entry point.

This module is intentionally slim: it parses the args shared with `nt backtest`,
loads secrets, resolves the strategy builder, and delegates to a concrete
PaperTradeRunner implementation (wired in PR 3 and onward).
"""

from __future__ import annotations

import typer

from nautilus_trading.cli._common import (
    _ensure_project_root_on_path,
    _resolve_strategy_paths,
)


def paper_trade(
    strategy: str = typer.Option(
        ...,
        "--strategy",
        help="Strategy module name (e.g. 'ema_cross', 'grid_bot').",
    ),
    instrument_id: str = typer.Option(
        ...,
        "--instrument-id",
        help="Binance instrument, e.g. 'BTCUSDT.BINANCE'.",
    ),
    bar_type: str = typer.Option(
        ...,
        "--bar-type",
        help="Bar type, e.g. 'BTCUSDT.BINANCE-1-MINUTE-LAST-EXTERNAL'.",
    ),
    trade_size: float = typer.Option(..., "--trade-size"),
    duration: str | None = typer.Option(
        None,
        "--duration",
        help="Optional time-box like '30m' or '2h'. Omit for continuous run.",
    ),
    log_level: str = typer.Option("INFO", "--log-level"),
) -> None:
    """Run a strategy on Binance Spot Testnet (paper trading)."""
    # Lazy imports so `import nautilus_trading.cli` stays cheap at collection time.
    from nautilus_trading.cli._strategy_configs import STRATEGY_BUILDERS
    from nautilus_trading.paper_trade.secrets import load_dotenv_local

    _ensure_project_root_on_path()
    load_dotenv_local()

    if strategy not in STRATEGY_BUILDERS:
        valid = ", ".join(sorted(STRATEGY_BUILDERS))
        raise typer.BadParameter(
            f"Unknown strategy '{strategy}'. Valid: {valid}",
            param_hint="--strategy",
        )

    _strategy_path, _config_path = _resolve_strategy_paths(strategy)
    # PR 3 wires this to a concrete PaperTradeRunner. Until then, fail loudly.
    raise typer.Exit(
        code=1,
    )
```

- [ ] **Step 4: Register the command**

Read `nautilus/src/nautilus_trading/cli/__init__.py`, then add the registration. Final content should look like:

```python
"""Typer CLI entry point — the `nt` command."""

from __future__ import annotations

import typer

from nautilus_trading.cli.backtest import backtest
from nautilus_trading.cli.paper_trade import paper_trade
from nautilus_trading.cli.strategies import strategies

app = typer.Typer(help="NautilusTrader project CLI.")
app.command(name="backtest")(backtest)
app.command(name="paper-trade")(paper_trade)
app.command(name="strategies")(strategies)
```

(Note: if the existing file already imports `live`, remove that import — Task 1.5 deletes it.)

- [ ] **Step 5: Run CLI tests**

Run: `cd nautilus && uv run pytest ../tests/cli/test_paper_trade_cli.py -v`
Expected: PASS, 3 tests.

- [ ] **Step 6: Commit**

```bash
git add nautilus/src/nautilus_trading/cli/paper_trade.py \
        nautilus/src/nautilus_trading/cli/__init__.py \
        tests/cli/test_paper_trade_cli.py
git commit -m "feat(cli): register nt paper-trade stub command"
```

### Task 1.5 — Retire `nt live` and `live/runner.py`

**Files:**
- Delete: `nautilus/src/nautilus_trading/cli/live.py`
- Delete: `nautilus/src/nautilus_trading/live/runner.py`
- Delete: `nautilus/src/nautilus_trading/live/__init__.py` (if only file left)
- Modify: any test or Makefile target referencing `nt live`

- [ ] **Step 1: Audit references**

Run: `grep -rn "nt live\|cli\.live\|from nautilus_trading\.live\|nautilus_trading\.cli\.live" nautilus/ strategies/ tests/ docs/ Makefile 2>/dev/null`

Expected: only hits are in the files we are about to delete (`cli/live.py`, `live/runner.py`, `cli/__init__.py` if it still imports `live`). Anything else — stop and surface to the orchestrator.

- [ ] **Step 2: Delete the files**

```bash
rm nautilus/src/nautilus_trading/cli/live.py
rm nautilus/src/nautilus_trading/live/runner.py
# Check if live/__init__.py is now empty or only has runner.py import:
cat nautilus/src/nautilus_trading/live/__init__.py
# If it's empty or only contained `from .runner import ...`, delete it:
rm nautilus/src/nautilus_trading/live/__init__.py
# Then remove the directory if it's now empty:
rmdir nautilus/src/nautilus_trading/live 2>/dev/null || true
```

- [ ] **Step 3: Remove `live` import from CLI init**

If `nautilus/src/nautilus_trading/cli/__init__.py` still imports `live`, remove that line and the `app.command(name="live")(live)` call.

- [ ] **Step 4: Run the full unit-test suite**

Run: `cd nautilus && uv run pytest -x --ignore=../tests/paper_trade/test_smoke_paper.py ../tests/ -v`
Expected: all PASS. If any test references `cli.live` or `live.runner`, it was a sub-project-A test that verifies the live-command registration — update or delete it in this same step.

- [ ] **Step 5: Lint**

Run: `make lint`
Expected: clean. If ruff flags imports of `nautilus_trading.live`, fix them.

- [ ] **Step 6: Commit**

```bash
git add -u
git commit -m "refactor: retire nt live command (real money out of scope for sub-project B)"
```

### Task 1.6 — Update `.env.example` and `.gitignore` comment

**Files:**
- Modify: `.env.example`
- Modify: `.gitignore`

- [ ] **Step 1: Extend `.env.example`**

Read `.env.example` first. Append the Testnet section if not already present:

```
# --- Binance Spot Testnet (paper trade) ---
# Get keys at: https://testnet.binance.vision/
BINANCE_TESTNET_API_KEY=
BINANCE_TESTNET_API_SECRET=
# Path to Ed25519 private key PEM (required for user-data WebSocket)
BINANCE_TESTNET_ED25519_KEY_PATH=/absolute/path/to/ed25519_private.pem
```

- [ ] **Step 2: Confirm `.gitignore` protects `.env.local`**

Read `.gitignore` and confirm `.env.*` catches `.env.local`. The existing entry `.env.*` with `!.env.example` already covers this. If you must add anything, only add a clarifying comment:

Near the environment variables section, confirm these lines are present (they already are per the read at the top of this session):

```
# Environment variables
.env
.env.*
.envrc
*.pem
!.env.example
```

No changes required. If the lines exist, skip the edit.

- [ ] **Step 3: Commit**

```bash
git add .env.example
git commit -m "docs: document Binance Testnet env vars in .env.example"
```

### Task 1.7 — Open the PR 1 pull request

- [ ] **Step 1: Push branch**

```bash
git push -u origin subproject-b/pr1-foundation
```

- [ ] **Step 2: Open PR**

```bash
gh pr create --title "sub-project B PR 1: paper_trade/ foundation + retire nt live" --body "$(cat <<'EOF'
## Summary
- Adds `nautilus/src/nautilus_trading/paper_trade/` package: `runner_base.py` (ABC), `secrets.py` (`.env.local` loader), `node_config.py` (extracted from `live/runner.py`).
- Registers `nt paper-trade` Typer stub; real runner wiring ships in PR 3.
- Retires `nt live` / `live/runner.py` — real-money live trading is out of scope for sub-project B.

## Test plan
- [ ] `make lint` green
- [ ] `make test-unit` green (new: `tests/paper_trade/test_runner_base.py`, `test_secrets.py`, `tests/cli/test_paper_trade_cli.py`)
- [ ] `cd nautilus && uv run nt --help` shows `paper-trade` and no `live`

Spec: `docs/superpowers/specs/2026-04-21-subproject-b-design.md`
EOF
)"
```

---

## PR 2 — Blocker-fix regression tests + `round_to_tick()`

**Depends on:** PR 1 merged.

**Scope:** PR 1 extracted the Ed25519 and InstrumentProvider wiring — but there is no test proving it stays wired as the code evolves. PR 2 adds those regression tests, then adds the *only* brand-new blocker fix from the spec: `round_to_tick(price, instrument) -> Price` to prevent Binance from rejecting off-grid LIMIT orders (§7.3 incident). We audit the 8 strategies for arithmetic LIMIT-price construction and route those through `round_to_tick()` defensively.

### Task 2.1 — Regression test: Ed25519 key type

**Files:**
- Create: `tests/paper_trade/test_node_config.py`

- [ ] **Step 1: Write the test**

Write to `tests/paper_trade/test_node_config.py`:

```python
"""Regression tests for the three Binance Spot Testnet blocker fixes."""

from __future__ import annotations

import pytest

from nautilus_trader.adapters.binance import BINANCE
from nautilus_trader.adapters.binance.common.enums import BinanceAccountType, BinanceEnvironment
from nautilus_trader.adapters.binance.config import BinanceKeyType

from nautilus_trading.paper_trade.node_config import build_paper_trade_node_config


@pytest.fixture
def sample_config():
    return build_paper_trade_node_config(
        strategy_path="strategies.crypto.ema_cross:EMACrossStrategy",
        config_path="strategies.crypto.ema_cross:EMACrossConfig",
        strategy_config={
            "instrument_id": "BTCUSDT.BINANCE",
            "bar_type": "BTCUSDT.BINANCE-1-MINUTE-LAST-EXTERNAL",
            "trade_size": "0.001",
        },
        instrument_id="BTCUSDT.BINANCE",
    )


def test_key_type_is_ed25519_on_data_client(sample_config):
    """Regression: Ed25519 signing required for user-data WebSocket (2026-04-08)."""
    assert sample_config.data_clients[BINANCE].key_type == BinanceKeyType.ED25519


def test_key_type_is_ed25519_on_exec_client(sample_config):
    """Regression: exec client must also use Ed25519 for consistency."""
    assert sample_config.exec_clients[BINANCE].key_type == BinanceKeyType.ED25519
```

- [ ] **Step 2: Run and verify pass**

Run: `cd nautilus && uv run pytest ../tests/paper_trade/test_node_config.py -v`
Expected: PASS, 2 tests. (They should pass immediately because PR 1 already wired Ed25519.)

- [ ] **Step 3: Commit**

```bash
git add tests/paper_trade/test_node_config.py
git commit -m "test(paper_trade): regression for Ed25519 key type on data+exec clients"
```

### Task 2.2 — Regression test: InstrumentProvider loads target symbol

**Files:**
- Modify: `tests/paper_trade/test_node_config.py`

- [ ] **Step 1: Extend the test file**

Append to `tests/paper_trade/test_node_config.py`:

```python
def test_instrument_provider_loads_target_symbol(sample_config):
    """Regression: default InstrumentProviderConfig() is empty → unknown-instrument errors.

    The config must explicitly declare the run's instrument for loading (2026-04-08 incident).
    """
    from nautilus_trader.model.identifiers import InstrumentId

    provider_cfg = sample_config.data_clients[BINANCE].instrument_provider
    # API contract: BinanceInstrumentProviderConfig exposes `load_ids` as an
    # iterable of InstrumentId. This assertion stays robust if load_ids is a
    # frozenset, list, or tuple.
    loaded = set(provider_cfg.load_ids)
    assert InstrumentId.from_str("BTCUSDT.BINANCE") in loaded


def test_instrument_provider_populated_on_exec_client(sample_config):
    """Exec client must load the same instrument (parallel cache in Nautilus)."""
    from nautilus_trader.model.identifiers import InstrumentId

    provider_cfg = sample_config.exec_clients[BINANCE].instrument_provider
    assert InstrumentId.from_str("BTCUSDT.BINANCE") in set(provider_cfg.load_ids)


def test_account_type_is_spot(sample_config):
    assert sample_config.data_clients[BINANCE].account_type == BinanceAccountType.SPOT
    assert sample_config.exec_clients[BINANCE].account_type == BinanceAccountType.SPOT


def test_environment_is_testnet(sample_config):
    assert sample_config.data_clients[BINANCE].environment == BinanceEnvironment.TESTNET
    assert sample_config.exec_clients[BINANCE].environment == BinanceEnvironment.TESTNET
```

- [ ] **Step 2: Run**

Run: `cd nautilus && uv run pytest ../tests/paper_trade/test_node_config.py -v`
Expected: PASS, 6 tests total.

- [ ] **Step 3: Commit**

```bash
git add tests/paper_trade/test_node_config.py
git commit -m "test(paper_trade): regression for InstrumentProvider + account/env"
```

### Task 2.3 — New helper: `round_to_tick()`

**Files:**
- Modify: `nautilus/src/nautilus_trading/paper_trade/node_config.py`
- Modify: `tests/paper_trade/test_node_config.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/paper_trade/test_node_config.py`:

```python
from decimal import Decimal

from nautilus_trader.model.objects import Price


class _FakeInstrument:
    """Minimal Instrument shim exposing just what round_to_tick() needs."""

    def __init__(self, tick_size: str, price_precision: int):
        self.price_increment = Price.from_str(tick_size)
        self.price_precision = price_precision


@pytest.mark.parametrize(
    "tick, precision, raw, expected",
    [
        ("0.01", 2, Decimal("100.237"), "100.23"),
        ("0.01", 2, Decimal("100.000"), "100.00"),
        ("0.001", 3, Decimal("0.123456"), "0.123"),
        ("0.00001", 5, Decimal("65432.123456"), "65432.12345"),
        # Crosses a tick exactly — should land on the tick, not above:
        ("0.01", 2, Decimal("100.020"), "100.02"),
    ],
)
def test_round_to_tick_grid(tick, precision, raw, expected):
    from nautilus_trading.paper_trade.node_config import round_to_tick

    inst = _FakeInstrument(tick_size=tick, price_precision=precision)
    price = round_to_tick(raw, inst)
    assert isinstance(price, Price)
    assert str(price) == expected


def test_round_to_tick_floors_positive_prices():
    """We floor to avoid overshooting on BUY LIMITs and to match Binance's
    conservative validation."""
    from nautilus_trading.paper_trade.node_config import round_to_tick

    inst = _FakeInstrument(tick_size="0.01", price_precision=2)
    # 100.019 should floor to 100.01 (not round-half-even to 100.02).
    assert str(round_to_tick(Decimal("100.019"), inst)) == "100.01"


def test_round_to_tick_preserves_exact_grid():
    from nautilus_trading.paper_trade.node_config import round_to_tick

    inst = _FakeInstrument(tick_size="0.01", price_precision=2)
    assert str(round_to_tick(Decimal("100.02"), inst)) == "100.02"
```

- [ ] **Step 2: Run — expect fail**

Run: `cd nautilus && uv run pytest ../tests/paper_trade/test_node_config.py -v`
Expected: the new tests FAIL with `ImportError: cannot import name 'round_to_tick'`.

- [ ] **Step 3: Implement `round_to_tick()`**

Append to `nautilus/src/nautilus_trading/paper_trade/node_config.py`:

```python
from decimal import Decimal, ROUND_FLOOR

from nautilus_trader.model.objects import Price


def round_to_tick(price: Decimal, instrument: Any) -> Price:
    """Floor `price` to the instrument's tick grid.

    Binance rejects LIMIT orders whose price is not on the tick grid (2026-04-08
    incident with grid_bot). Strategies that construct LIMIT prices arithmetically
    must call this helper before submit_order().

    Floor (not round-half-even) is chosen for two reasons:
      1. Symmetry with Binance's own validator, which truncates.
      2. A floored BUY-limit price can never overshoot the user's ceiling;
         the SELL side is handled by callers mirroring the offset.
    """
    tick = Decimal(str(instrument.price_increment))
    floored = (price / tick).quantize(Decimal("1"), rounding=ROUND_FLOOR) * tick
    return Price(floored, precision=instrument.price_precision)
```

Note: the `from decimal import ...` and `from nautilus_trader.model.objects import Price` lines go near the top of the file alongside the other imports (Python groups them — do NOT leave them at the bottom in a finished module).

- [ ] **Step 4: Run — expect pass**

Run: `cd nautilus && uv run pytest ../tests/paper_trade/test_node_config.py -v`
Expected: PASS, all ~13 tests.

- [ ] **Step 5: Commit**

```bash
git add nautilus/src/nautilus_trading/paper_trade/node_config.py \
        tests/paper_trade/test_node_config.py
git commit -m "feat(paper_trade): add round_to_tick() helper for LIMIT order price grid"
```

### Task 2.4 — Audit strategies for arithmetic LIMIT-price construction

**Files:**
- Modify (possibly): `strategies/crypto/grid_bot.py` (known 2026-04-08 offender)
- Possibly modify: `strategies/crypto/timesfm_grid.py`, `strategies/crypto/dca_bot.py`, others

- [ ] **Step 1: Identify LIMIT-order construction sites**

Run: `grep -n "order_factory\.limit\|OrderFactory.*limit" strategies/crypto/*.py strategies/crypto/**/*.py 2>/dev/null`

Record the hits. For each, look at how the `price=` argument is computed. If it's literal or tick-aligned by construction, skip it. If it's arithmetic (e.g. `Price(lower + spread, precision)`, or `mid * (1 + offset)`), it is at risk.

- [ ] **Step 2: Route each at-risk site through `round_to_tick()`**

For each risky call site, import and apply:

```python
from decimal import Decimal

from nautilus_trading.paper_trade.node_config import round_to_tick

# Before:
price = Price(raw_level, precision=instrument.price_precision)

# After:
price = round_to_tick(Decimal(str(raw_level)), instrument)
```

`grid_bot.py` is the confirmed case from 2026-04-08. Fix it for sure. Audit the other 7 — err on the side of routing through the helper if in doubt; the backtest path tolerates it (SimulatedVenue accepts any price on grid).

- [ ] **Step 3: Run each modified strategy's backtest test**

For each strategy you touched:

```bash
cd nautilus && uv run pytest ../tests/strategies/crypto/test_<name>.py -v
```

Expected: all green. `round_to_tick()` must not change backtest behavior for prices already on-grid.

- [ ] **Step 4: Commit**

```bash
git add -u
git commit -m "fix(strategies/crypto): route arithmetic LIMIT prices through round_to_tick()"
```

### Task 2.5 — Open PR 2

- [ ] **Step 1: Push and open PR**

```bash
git push -u origin subproject-b/pr2-blocker-fixes
gh pr create --title "sub-project B PR 2: blocker-fix regression tests + round_to_tick()" --body "$(cat <<'EOF'
## Summary
- Regression tests pin the Ed25519 + InstrumentProvider wiring extracted in PR 1.
- New `round_to_tick()` helper in `paper_trade/node_config.py` with parametrized coverage.
- Strategies that construct LIMIT prices arithmetically now route through the helper (grid_bot confirmed; others audited).

## Test plan
- [ ] `make lint` green
- [ ] `make test-unit` green
- [ ] `cd nautilus && uv run pytest ../tests/paper_trade/ -v` — all 13 blocker-fix tests pass

Closes the 3 blockers from 2026-04-08 by design: Ed25519 (§7.1), InstrumentProvider (§7.2), tick rounding (§7.3).
EOF
)"
```

---

## PR 3 — `EMACrossPaperTradeRunner` + wire CLI to runner

**Depends on:** PR 2 merged.

**Scope:** Ship the first concrete `PaperTradeRunner`. `ema_cross` is the simplest strategy (two indicators, MARKET orders only, no actor) — so it validates the end-to-end pattern with the least risk. Connect the `nt paper-trade` stub to a dispatch dict that maps `strategy` → concrete runner class.

### Task 3.1 — `EMACrossPaperTradeRunner` + its test

**Files:**
- Create: `strategies/crypto/ema_cross_paper.py`
- Create: `tests/strategies/crypto/test_ema_cross_paper.py`

- [ ] **Step 1: Write the failing test**

Write to `tests/strategies/crypto/test_ema_cross_paper.py`:

```python
"""EMACrossPaperTradeRunner composition test.

Does NOT hit Testnet. Verifies the runner builds the right TradingNodeConfig
and attaches the right strategy with the right config dict.
"""

from __future__ import annotations

from nautilus_trader.adapters.binance import BINANCE
from nautilus_trader.adapters.binance.common.enums import BinanceEnvironment

from strategies.crypto.ema_cross_paper import EMACrossPaperTradeRunner


def test_runner_builds_testnet_spot_config():
    runner = EMACrossPaperTradeRunner(
        instrument_id="BTCUSDT.BINANCE",
        bar_type="BTCUSDT.BINANCE-1-MINUTE-LAST-EXTERNAL",
        trade_size="0.001",
        fast_ema=10,
        slow_ema=20,
    )
    config = runner.build_config()

    assert config.data_clients[BINANCE].environment == BinanceEnvironment.TESTNET
    assert len(config.strategies) == 1
    strat_entry = config.strategies[0]
    assert strat_entry.strategy_path.endswith(":EMACrossStrategy")
    assert strat_entry.config["fast_ema_period"] == 10
    assert strat_entry.config["slow_ema_period"] == 20
```

- [ ] **Step 2: Run — fail on import**

Run: `cd nautilus && uv run pytest ../tests/strategies/crypto/test_ema_cross_paper.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'strategies.crypto.ema_cross_paper'`.

- [ ] **Step 3: Implement the runner**

Write to `strategies/crypto/ema_cross_paper.py`:

```python
"""EMACrossPaperTradeRunner — Binance Spot Testnet driver for EMACrossStrategy."""

from __future__ import annotations

from dataclasses import dataclass

from nautilus_trader.config import TradingNodeConfig

from nautilus_trading.cli._strategy_configs import STRATEGY_BUILDERS
from nautilus_trading.paper_trade.node_config import (
    build_paper_trade_node_config,
    run_paper_trade,
)
from nautilus_trading.paper_trade.runner_base import PaperTradeRunner


@dataclass
class EMACrossPaperTradeRunner(PaperTradeRunner):
    instrument_id: str
    bar_type: str
    trade_size: str
    fast_ema: int = 10
    slow_ema: int = 20
    log_level: str = "INFO"

    def build_config(self) -> TradingNodeConfig:
        """Build the TradingNodeConfig. Separated from main() for testability."""
        builder = STRATEGY_BUILDERS["ema_cross"]
        strategy_config = builder.build(
            _EMACrossArgs(
                instrument_id=self.instrument_id,
                bar_type=self.bar_type,
                trade_size=self.trade_size,
                fast_ema=self.fast_ema,
                slow_ema=self.slow_ema,
            )
        )
        return build_paper_trade_node_config(
            strategy_path="strategies.crypto.ema_cross:EMACrossStrategy",
            config_path="strategies.crypto.ema_cross:EMACrossConfig",
            strategy_config=strategy_config,
            instrument_id=self.instrument_id,
            log_level=self.log_level,
        )

    def main(self) -> None:
        run_paper_trade(self.build_config())


@dataclass
class _EMACrossArgs:
    instrument_id: str
    bar_type: str
    trade_size: str
    fast_ema: int
    slow_ema: int
```

**Note about `_EMACrossArgs`:** `STRATEGY_BUILDERS[name].build(args)` takes the same CLI-args object that `nt backtest` passes. `_EMACrossArgs` is a minimal dataclass that exposes just the fields `EMAConfigBuilder.build()` reads. Before writing this, read `nautilus/src/nautilus_trading/cli/_strategy_configs.py` to confirm the exact attribute names each builder reads; mirror them precisely. If a builder reads `args.trade_size` as a string, pass a string; if `int`, pass an int.

- [ ] **Step 4: Run — pass**

Run: `cd nautilus && uv run pytest ../tests/strategies/crypto/test_ema_cross_paper.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add strategies/crypto/ema_cross_paper.py \
        tests/strategies/crypto/test_ema_cross_paper.py
git commit -m "feat(strategies/crypto): add EMACrossPaperTradeRunner"
```

### Task 3.2 — Dispatch table in `cli/paper_trade.py`

**Files:**
- Modify: `nautilus/src/nautilus_trading/cli/paper_trade.py`
- Modify: `tests/cli/test_paper_trade_cli.py`

- [ ] **Step 1: Extend CLI test to exercise dispatch**

Append to `tests/cli/test_paper_trade_cli.py`:

```python
def test_paper_trade_ema_cross_dispatches_to_runner(monkeypatch):
    """Invoking `nt paper-trade --strategy ema_cross ...` builds an EMACross runner
    and calls .main(). We swap .main() for a recorder double so we don't hit Testnet."""
    calls = []

    def _recording_main(self):
        calls.append(("ema_cross", self.instrument_id, self.fast_ema, self.slow_ema))

    from strategies.crypto.ema_cross_paper import EMACrossPaperTradeRunner
    monkeypatch.setattr(EMACrossPaperTradeRunner, "main", _recording_main)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "paper-trade",
            "--strategy", "ema_cross",
            "--instrument-id", "BTCUSDT.BINANCE",
            "--bar-type", "BTCUSDT.BINANCE-1-MINUTE-LAST-EXTERNAL",
            "--trade-size", "0.001",
            "--fast-ema", "12",
            "--slow-ema", "26",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert calls == [("ema_cross", "BTCUSDT.BINANCE", 12, 26)]
```

- [ ] **Step 2: Run — fail**

Run: `cd nautilus && uv run pytest ../tests/cli/test_paper_trade_cli.py -v`
Expected: the new test FAILs because `--fast-ema` isn't wired and there's no dispatch.

- [ ] **Step 3: Wire dispatch**

Rewrite `nautilus/src/nautilus_trading/cli/paper_trade.py`:

```python
"""`nt paper-trade` — Binance Spot Testnet paper-trade entry point."""

from __future__ import annotations

import typer

from nautilus_trading.cli._common import _ensure_project_root_on_path

# Strategy-name → runner class, populated lazily to keep CLI import cheap.
_RUNNERS: dict[str, type] = {}


def _load_runners() -> None:
    if _RUNNERS:
        return
    from strategies.crypto.ema_cross_paper import EMACrossPaperTradeRunner

    _RUNNERS["ema_cross"] = EMACrossPaperTradeRunner


def paper_trade(
    strategy: str = typer.Option(..., "--strategy"),
    instrument_id: str = typer.Option(..., "--instrument-id"),
    bar_type: str = typer.Option(..., "--bar-type"),
    trade_size: str = typer.Option(..., "--trade-size"),
    fast_ema: int = typer.Option(10, "--fast-ema"),
    slow_ema: int = typer.Option(20, "--slow-ema"),
    duration: str | None = typer.Option(None, "--duration"),
    log_level: str = typer.Option("INFO", "--log-level"),
) -> None:
    """Run a strategy on Binance Spot Testnet (paper trading)."""
    from nautilus_trading.paper_trade.secrets import load_dotenv_local

    _ensure_project_root_on_path()
    load_dotenv_local()
    _load_runners()

    if strategy not in _RUNNERS:
        valid = ", ".join(sorted(_RUNNERS))
        raise typer.BadParameter(
            f"Unknown strategy '{strategy}'. Valid: {valid}",
            param_hint="--strategy",
        )

    runner_cls = _RUNNERS[strategy]
    # Dispatch: each runner accepts only the kwargs its dataclass declares;
    # Python raises TypeError for unexpected kwargs, which we catch and remap
    # to a friendly usage error.
    try:
        runner = runner_cls(
            instrument_id=instrument_id,
            bar_type=bar_type,
            trade_size=trade_size,
            fast_ema=fast_ema,
            slow_ema=slow_ema,
        )
    except TypeError as exc:
        raise typer.BadParameter(str(exc)) from exc

    runner.main()
```

As more strategies ship in PRs 4–6, each adds `_RUNNERS[name] = <Class>` to `_load_runners()` and — if its args differ from `ema_cross` — extends the Typer signature and the dispatch kwargs. See Task 4.1 for the pattern.

- [ ] **Step 4: Run — pass**

Run: `cd nautilus && uv run pytest ../tests/cli/test_paper_trade_cli.py ../tests/strategies/crypto/test_ema_cross_paper.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add nautilus/src/nautilus_trading/cli/paper_trade.py tests/cli/test_paper_trade_cli.py
git commit -m "feat(cli): dispatch nt paper-trade ema_cross to EMACrossPaperTradeRunner"
```

### Task 3.3 — Open PR 3

- [ ] **Step 1: Dispatch pre-submit reviewer** — per the PR submission convention at the top of this plan, dispatch the `pr-review-toolkit:code-reviewer` subagent to run `/ultrareview` and `/simplify` against `subproject-b/pr3-cli-ema-runner`, address findings, and confirm tests stay green. Wait for DONE.

- [ ] **Step 2: Push and open PR**

```bash
git push -u origin subproject-b/pr3-cli-ema-runner
gh pr create --title "sub-project B PR 3: first concrete runner (ema_cross) + CLI dispatch" ...
```

---

## PR 4 — Simple directional runners: grid_bot, dca_bot, timesfm_swing

**Depends on:** PR 3 merged.

**Scope:** Replicate the PR 3 pattern for three more strategies. They share the shape "subscribe bars → strategy emits orders" but each has its own config fields. grid_bot is the 2026-04-08 tick-grid offender — PR 2's `round_to_tick()` is already in place; here we just confirm the paper runner picks that up.

### Task 4.1 — `GridBotPaperTradeRunner` + test

**Files:**
- Create: `strategies/crypto/grid_bot_paper.py`
- Create: `tests/strategies/crypto/test_grid_bot_paper.py`

- [ ] **Step 1: Write the failing test**

Mirror `test_ema_cross_paper.py`. Read the existing `strategies/crypto/grid_bot.py` to identify which config fields the `GridBotConfigBuilder` reads. At minimum: `upper_price`, `lower_price`, `num_grids`, `trade_size`, `instrument_id`, `bar_type`.

```python
"""GridBotPaperTradeRunner composition test."""

from __future__ import annotations

from nautilus_trader.adapters.binance import BINANCE
from nautilus_trader.adapters.binance.common.enums import BinanceEnvironment

from strategies.crypto.grid_bot_paper import GridBotPaperTradeRunner


def test_runner_builds_testnet_spot_config():
    runner = GridBotPaperTradeRunner(
        instrument_id="BTCUSDT.BINANCE",
        bar_type="BTCUSDT.BINANCE-1-MINUTE-LAST-EXTERNAL",
        trade_size="0.001",
        upper_price="72000",
        lower_price="60000",
        num_grids=8,
    )
    config = runner.build_config()
    assert config.data_clients[BINANCE].environment == BinanceEnvironment.TESTNET
    assert config.strategies[0].strategy_path.endswith(":GridBotStrategy")
    assert config.strategies[0].config["num_grids"] == 8
```

- [ ] **Step 2–5: Implement runner, register in `_load_runners()`, extend Typer signature, run tests, commit**

Same shape as EMA. Add `--upper-price`, `--lower-price`, `--num-grids` Typer options. Keep EMA options with `None` defaults; dispatch filters them so each runner receives only what its dataclass accepts.

(Keep defaults for each new option as `None` so they don't leak across strategies. In the dispatch, build kwargs conditionally: `kwargs = {"instrument_id": instrument_id, "bar_type": bar_type, "trade_size": trade_size}; if strategy == "grid_bot": kwargs.update(...)`.)

### Task 4.2 — `DCABotPaperTradeRunner`

Mirror Task 4.1 using `DCABotConfigBuilder` arg names.

### Task 4.3 — `TimesFMSwingPaperTradeRunner`

Mirror Task 4.1 using `TimesFMConfigBuilder` arg names. Note: TimesFM requires the ML checkpoint to be present; the composition test must stub the model load, or use a tiny fixture.

### Task 4.4 — Open PR 4

- [ ] **Step 1: Dispatch pre-submit reviewer** — per the PR submission convention at the top of this plan, dispatch the `pr-review-toolkit:code-reviewer` subagent to run `/ultrareview` and `/simplify` against `subproject-b/pr4-simple-runners`, address findings, and confirm tests stay green. Wait for DONE.

- [ ] **Step 2: Push and open PR** — `git push -u origin subproject-b/pr4-simple-runners && gh pr create --title "sub-project B PR 4: simple directional runners (grid_bot, dca_bot, timesfm_swing)" ...`

---

## PR 5 — Composite/ML runners: hybrid_sma_r10, timesfm_grid, rvs_swing, shock_guard

**Depends on:** PR 4 merged.

**Scope:** The trickier four. `hybrid_sma_r10` and `timesfm_grid` attach an Actor alongside the Strategy (see `KronosActor` pattern); `rvs_swing` and `shock_guard` may need larger indicator warmup windows. Each composition test must assert the Actor is wired in `config.actors` (or equivalent) where applicable.

### Task 5.1 — `HybridSMAR10PaperTradeRunner` — includes actor wiring

Read `strategies/crypto/hybrid_sma_r10.py` and `strategies/crypto/kronos/backtest.py` for the Actor-attach pattern. The runner's `build_config()` must add both a strategy and an actor to `TradingNodeConfig.actors` (check exact keyword — `actors=[ImportableActorConfig(...)]`).

Composition test asserts `len(config.actors) == 1` and the actor path ends with `:HybridSMAActor` (or whatever the actual class name is).

### Task 5.2–5.4 — timesfm_grid, rvs_swing, shock_guard

Same pattern. Per-strategy args extracted from each `*ConfigBuilder`.

### Task 5.5 — Open PR 5

- [ ] **Step 1: Dispatch pre-submit reviewer** — per the PR submission convention at the top of this plan, dispatch the `pr-review-toolkit:code-reviewer` subagent to run `/ultrareview` and `/simplify` against `subproject-b/pr5-composite-runners`, address findings, and confirm tests stay green. Wait for DONE.

- [ ] **Step 2: Push and open PR** — `git push -u origin subproject-b/pr5-composite-runners && gh pr create --title "sub-project B PR 5: composite/ML runners (hybrid_sma_r10, timesfm_grid, rvs_swing, shock_guard)" ...`

---

## PR 6 — YAML run configs

See standalone plan: `docs/superpowers/plans/2026-04-22-pr6-yaml-run-configs-implementation.md`

Spec: `docs/superpowers/specs/2026-04-22-pr6-yaml-run-configs-design.md`

Replaces the 16-flag CLI with `nt paper-trade --config configs/paper/<name>.yaml`. The flag path is deleted — no deprecation window. Completed before Kronos lands so Kronos can ship a config file instead of ~17 new flags.

---

## PR 7 — Kronos migration + parity gate

**Depends on:** PR 6 (YAML run configs) merged.

**Scope:** Replace `strategies/crypto/kronos/paper_trade.py` (quarantined script) with `strategies/crypto/kronos/paper_runner.py` implementing `KronosPaperTradeRunner(PaperTradeRunner)`. Add a parity test asserting the new runner's config matches the quarantined script's on the 5 fields from spec §10. Delete the old script only after the parity test passes in the same PR.

### Task 7.1 — Write the parity test first (pre-implementation)

**Files:**
- Create: `tests/strategies/crypto/kronos/test_paper_runner_parity.py`

- [ ] **Step 1: Write a test that compares the **extracted** config from the old script against the new runner's config**

```python
"""Parity gate: KronosPaperTradeRunner's config must match the quarantined script's
on the 5 fields from spec §10."""

from __future__ import annotations

from nautilus_trader.adapters.binance import BINANCE
from nautilus_trader.adapters.binance.common.enums import BinanceAccountType, BinanceEnvironment


def test_kronos_paper_runner_matches_quarantined_script():
    # The quarantined script constructs config at module top level behind an env check.
    # We mirror its env inputs and import it defensively (it exits on missing keys).
    import os
    os.environ.setdefault("BINANCE_TESTNET_API_KEY", "stub_key_for_config_only")
    os.environ.setdefault("BINANCE_TESTNET_API_SECRET", "stub_secret_for_config_only")
    os.environ.setdefault("KRONOS_SYMBOL", "BTCUSDT.BINANCE")
    os.environ.setdefault("KRONOS_INTERVAL", "1-MINUTE-LAST-EXTERNAL")

    # Import the OLD script and capture its config before deletion in Task 7.3.
    # Because the old script calls node.run() at import-end, we must guard against
    # actually booting the node. Strategy: extract just the config construction
    # via a module-level function we temporarily expose, or parse the module AST.
    # Simpler: copy the old config construction into a fixture helper file in
    # tests/ so this test stays runnable after Task 7.3 deletes the script.
    from tests.strategies.crypto.kronos._quarantined_config_snapshot import (
        build_quarantined_config,
    )
    from strategies.crypto.kronos.paper_runner import KronosPaperTradeRunner

    old = build_quarantined_config()
    new = KronosPaperTradeRunner(
        instrument_id="BTCUSDT.BINANCE",
        bar_type="BTCUSDT.BINANCE-1-MINUTE-LAST-EXTERNAL",
        trade_size="0.001",
    ).build_config()

    # 1. account_type
    assert new.data_clients[BINANCE].account_type == old.data_clients[BINANCE].account_type == BinanceAccountType.SPOT
    # 2. environment
    assert new.data_clients[BINANCE].environment == old.data_clients[BINANCE].environment == BinanceEnvironment.TESTNET
    # 3. venue name (BINANCE constant)
    assert BINANCE in new.exec_clients and BINANCE in old.exec_clients
    # 4. strategy + actor classes
    assert new.strategies[0].strategy_path == old.strategies[0].strategy_path
    assert new.actors and new.actors[0].actor_path.endswith(":KronosActor")
    # 5. configured symbol
    assert new.strategies[0].config["instrument_id"] == old.strategies[0].config["instrument_id"]
```

- [ ] **Step 2: Create the config snapshot fixture**

Because Task 7.3 deletes the old script, copy its **config-construction portion only** (not `node.run()`) into `tests/strategies/crypto/kronos/_quarantined_config_snapshot.py`. This is a one-time frozen copy — it exists to prove parity at migration time and then never changes.

### Task 7.2 — Implement `KronosPaperTradeRunner`

Mirror the PR 5 actor pattern. Reuses `KronosStrategy` and `KronosActor` unchanged.

### Task 7.3 — Delete the quarantined script

Only after Task 7.1's parity test passes:

```bash
rm strategies/crypto/kronos/paper_trade.py
```

Remove the `strategies/crypto/kronos/paper_trade.py` entry from `nautilus/pyproject.toml`'s `tool.ruff.per-file-ignores` table.

### Task 7.4 — Open PR 7

- [ ] **Step 1: Dispatch pre-submit reviewer** — per the PR submission convention at the top of this plan, dispatch the `pr-review-toolkit:code-reviewer` subagent to run `/ultrareview` and `/simplify` against `subproject-b/pr7-kronos-parity`, address findings, and confirm both unit tests and the parity gate stay green. Wait for DONE.

- [ ] **Step 2: Push and open PR** — `git push -u origin subproject-b/pr7-kronos-parity && gh pr create --title "sub-project B PR 7: Kronos migration + parity gate" ...`

---

## PR 8 — CI opt-in smoke + `make smoke-paper-order` + runbook + roadmap

**Depends on:** PR 7 (Kronos migration) merged.

### Task 8.1 — Register `binance_testnet` pytest marker

**Files:**
- Modify: `nautilus/pyproject.toml` (`[tool.pytest.ini_options]` → `markers`)

- [ ] Add `"binance_testnet: opt-in smoke against live Binance Spot Testnet"` to the markers list.

### Task 8.2 — CI node-boot smoke for all 8

**Files:**
- Create: `tests/paper_trade/test_smoke_paper.py`

This test is gated by `@pytest.mark.binance_testnet` and runs each runner with a 30-second event-loop timeout. Asserts: (a) `node.build()` returns without exception, (b) at least one `Bar` or `QuoteTick` event arrives, (c) `node.stop()` runs cleanly. No order path (that's §9.2, next task).

Pseudocode structure (expand to a real test per strategy — parametrize if 8 runners share shape):

```python
import pytest
import os
from strategies.crypto.ema_cross_paper import EMACrossPaperTradeRunner
# ... import all 8 runners

pytestmark = pytest.mark.binance_testnet

@pytest.fixture(autouse=True)
def require_testnet_keys():
    if not os.environ.get("BINANCE_TESTNET_API_KEY"):
        pytest.skip("BINANCE_TESTNET_API_KEY not set")

@pytest.mark.parametrize("runner_factory, instrument, bar_type", [
    (lambda: EMACrossPaperTradeRunner(...), "BTCUSDT.BINANCE", "..."),
    # ... 7 more
])
def test_node_boots_and_receives_data(runner_factory, instrument, bar_type):
    runner = runner_factory()
    config = runner.build_config()
    # Run with a 30s timeout; assert no exception and at least one market event.
    # Use a message-bus subscriber or a debug flag to count events.
    ...
```

### Task 8.3 — Manual `make smoke-paper-order STRATEGY=<name>` target

**Files:**
- Modify: `Makefile`
- Create: `scripts/smoke_paper_order.py`

The Makefile target invokes the script with the strategy name. The script boots the runner's node, grabs the exec client, submits one off-market LIMIT order via the exec client directly (strategy-bypass), asserts the ACK, cancels the order, shuts down.

### Task 8.4 — Runbook `docs/runbooks/paper-trade.md`

Write the runbook with: Testnet account creation link, Ed25519 key-gen commands, `.env.local` template, how to start / stop / panic-close, common errors (401, unknown instrument, tick rejection) + their fixes.

### Task 8.5 — Update `docs/superpowers/roadmap.md` and `CLAUDE.md`

Replace the sub-project B roadmap paragraph with the paper-trade-only scope. Add a short "Paper trading" section to `CLAUDE.md` mirroring the existing "Backtesting" section.

### Task 8.6 — Open PR 8

- [ ] **Step 1: Dispatch pre-submit reviewer** — per the PR submission convention at the top of this plan, dispatch the `pr-review-toolkit:code-reviewer` subagent to run `/ultrareview` and `/simplify` against `subproject-b/pr8-smoke-runbook`, address findings, and confirm tests stay green. Wait for DONE.

- [ ] **Step 2: Push and open PR** — `git push -u origin subproject-b/pr8-smoke-runbook && gh pr create --title "sub-project B PR 8: opt-in smoke + runbook + roadmap" ...`

---

## Self-review (executed 2026-04-21 by plan author)

**Spec coverage:**
- §1 Goal → PR 3–5 ship runners; PR 6 swaps flags for YAML configs; PR 7 migrates Kronos; PR 8 ships CI + runbook. ✅
- §2 Non-goals → plan explicitly retires `nt live` in PR 1 (aligned with "no real money"). ✅
- §3 Architecture → PR 1 creates `paper_trade/` package parallel to `backtest/`. ✅
- §4 Module layout → every file listed in spec has a task. ✅
- §5 CLI surface → PR 3 registers base args; PRs 4–6 extend. ✅
- §6 Secrets flow → PR 1 Task 1.2. ✅
- §7 Three blocker fixes → PR 2 covers all three (7.1/7.2 as regression tests, 7.3 as new helper + strategy audit). ✅
- §8 Runner ABC → PR 1 Task 1.1. ✅
- §9 Testing → PR 8 Task 8.2 (CI smoke) + 8.3 (forced-order). Unit tests distributed across PRs. ✅
- §10 Kronos parity → PR 7 Task 7.1 with 5-field assertion. ✅
- §11 Documentation → PR 8 Tasks 8.4/8.5. ✅
- §12 PR slicing → 7 PRs, 1:1 match with spec. ✅
- §13 Open follow-ons → out of plan by design. ✅

**Scope refinement vs. spec:**
The plan makes one scope decision not in the spec: *retire `nt live` entirely in PR 1*. Rationale: the spec's §1–§2 state "no real money, ever" and §14 decision #1 aligns. Keeping an `nt live` command — even as a stub — invites drift and reuse of real-money code paths. The audit in Task 1.5 Step 1 catches any consumer before deletion.

**Placeholder scan:** No "TBD", no "handle edge cases" — every step has concrete code or exact commands. PR 4 / PR 5 individual tasks compress into a pattern directive ("mirror Task 4.1 using X builder arg names") — this is intentional because after 3 concrete examples the repetition would be noise, not clarity. The worker has the EMA example (PR 3) as the canonical template.

**Type consistency:** `build_paper_trade_node_config()` signature is stable across all tasks. `PaperTradeRunner.main()` signature is stable. `_RUNNERS` dispatch dict is the single source of CLI-to-class mapping. `round_to_tick(price: Decimal, instrument) -> Price` signature is stable from PR 2 through the strategy audit.

**Open risk:** Task 3.1's `_EMACrossArgs` relies on `STRATEGY_BUILDERS[name].build(args)` accepting a duck-typed object with the right attributes. If any existing builder reads attributes the dataclass doesn't have (e.g. `args.catalog_dir` for a paper-trade context that has no catalog), the builder either needs a nullable branch or the runner needs to fake the field. Mitigation: the first task of PR 3 Step 3 includes "read `cli/_strategy_configs.py` to confirm exact attribute names". If a builder requires backtest-only fields, surface as a blocker to the orchestrator rather than hacking around it.

---

## Execution handoff

Plan complete and saved to `docs/superpowers/plans/2026-04-21-subproject-b-implementation.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, two-stage review (spec compliance → code quality) between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using `superpowers:executing-plans`, batch execution with checkpoints for review.

Which approach?
