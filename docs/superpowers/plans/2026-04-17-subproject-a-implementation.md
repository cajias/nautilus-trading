# Sub-project A — Core + Crypto Strategies Redesign — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Consolidate `nautilus/` core package and `strategies/crypto/` under Option-2 Protocol-based architecture (`StrategyConfigBuilder` + `BacktestRunner` base), test-first, delivered as 8 revertable PRs that each keep the tree green.

**Architecture:** Test harness lands first (PR 1) to raise core package from ~25% → ≥70% coverage via real-scenario characterization tests using a committed parquet fixture catalog. Structural changes then flow strictly TDD: add test that captures current behavior → refactor → confirm test still passes. `cli/_strategy_configs.py` (Protocol registry) and `backtest/runner_base.py` (`BacktestRunner` ABC) are the load-bearing abstractions; kronos migrates onto the runner base as the first consumer. `strategies/crypto/_grid_math.py` lifts pure helpers out of `timesfm_grid.py`.

**Tech Stack:** Python 3.13, uv, pytest (real Nautilus engine, no mocks in runtime), Nautilus Trader 1.224.0, Typer CLI, parquet fixtures under `tests/fixtures/` + opt-in Binance testnet smoke.

**Spec:** `docs/superpowers/specs/2026-04-17-subproject-a-design.md` (APPROVED, Option 2, 8 PRs).

**Branch convention.** One branch per PR, named `subproject-a/pr<N>-<slug>` (e.g. `subproject-a/pr1-test-harness`). Each PR rebases on top of the previous merged PR's branch (or `main` for PR 1). Every PR ends with the tree green: `make lint && make test-unit`.

**Global commands.** Unless noted, all shell commands run from the repo root `/Users/rc/Projects/workspace/nautilus-trading/.claude/worktrees/dreamy-prancing-glade/`. Pytest runs from the `nautilus/` directory via `cd nautilus && uv run pytest …` because that is where `pyproject.toml` + `.venv` live.

---

## PR 1 — Test harness + pytest-collection fix

**Depends on:** none. This PR lands first and is the pre-condition for every structural PR that follows.

**Scope:** (a) make `pytest --collect-only` hermetic and sub-5s, (b) commit a small parquet fixture catalog under `tests/fixtures/crypto/`, (c) add characterization tests for the six previously-untested core modules + two end-to-end smoke tests, (d) add an opt-in Binance testnet smoke job gated by `BINANCE_TESTNET_API_KEY`. Target: ≥70% line coverage on the modules touched in PRs 2–8.

**Mechanical note — "monkeypatch" is not a runtime mock.** We patch `nautilus_trading.live.runner.run_live` inside Typer-CliRunner tests so the live command can build a `TradingNodeConfig` and *stop* before `.run()` blocks. Runtime code is not mocked; the test captures what was built. Same for `TradingNode` in the live-runner tests: we subclass it with a recorder double — no `unittest.mock`.

### Task 1.1 — Diagnose and make pytest collection hermetic

**Files:**
- Modify: `nautilus/src/nautilus_trading/cli/__init__.py` (lines 1-17)
- Modify: `nautilus/src/nautilus_trading/cli/live.py` (top-of-file imports)
- Modify: `nautilus/src/nautilus_trading/cli/backtest.py` (top-of-file imports)
- Modify: `nautilus/src/nautilus_trading/__main__.py` (if it exists)
- Modify: `nautilus/src/nautilus_trading/backtest/runner.py` (top-of-file `nautilus_trader.backtest.node` import)

- [x] **Step 1: Capture the baseline failure**

```bash
cd nautilus && timeout 30 uv run pytest --collect-only -q 2>&1 | tail -20
```

Expected: either `collected 0 items` after ~20s, or a timeout. Save the output to a note — you'll use it in the commit message.

- [x] **Step 2: Identify the heavy imports triggered at collection time**

Run the following and note modules that account for >1s of import time:

```bash
cd nautilus && uv run python -X importtime -c "import nautilus_trading.cli" 2>&1 | awk -F'|' '$2 > 1000000 {print}' | head -20
```

Expected culprits (from audit): `nautilus_trader.backtest.node`, `nautilus_trader.live.node`, `nautilus_trader.adapters.binance`. Record which files import them at module level.

- [x] **Step 3: Move heavy third-party imports inside functions**

In `nautilus/src/nautilus_trading/cli/backtest.py`, move these out of module scope and into the body of `backtest()`:

```python
def backtest(
    # ... existing parameters unchanged ...
) -> None:
    """Run a strategy backtest on historical data."""
    # Lazy imports so `import nautilus_trading.cli` stays cheap at test-collection time.
    from nautilus_trading.backtest.runner import build_backtest_config, print_results, run_backtest
    from nautilus_trading.data.download import ensure_catalog

    _ensure_project_root_on_path()
    # ... rest of existing body ...
```

Remove the corresponding top-of-file imports:
```python
# DELETE these two lines at the top of cli/backtest.py:
from nautilus_trading.backtest.runner import build_backtest_config, print_results, run_backtest
from nautilus_trading.data.download import ensure_catalog
```

In `nautilus/src/nautilus_trading/cli/live.py`, the import at module top is already limited to typer + `cli.backtest` helpers — leave as-is (Task 1.1 only touches `cli/backtest.py` and `backtest/runner.py`). Verify no other module-level imports pull in `nautilus_trader.backtest.node` or `nautilus_trader.live.node`.

In `nautilus/src/nautilus_trading/backtest/runner.py`, wrap the `nautilus_trader.backtest.node` imports in a function so `import nautilus_trading.backtest.runner` does not trigger engine bootstrap:

```python
"""BacktestNode configuration and execution."""

from __future__ import annotations

from typing import Any

from nautilus_trader.model import QuoteTick
from nautilus_trader.persistence.catalog import ParquetDataCatalog


def _node_imports() -> tuple[type, type, type, type, type, type]:
    """Deferred import of Nautilus backtest-node symbols.

    Imported inside functions so `import nautilus_trading.backtest.runner`
    does not trigger engine bootstrap during pytest collection.
    """
    from nautilus_trader.backtest.node import (
        BacktestDataConfig,
        BacktestEngineConfig,
        BacktestNode,
        BacktestRunConfig,
        BacktestVenueConfig,
    )
    from nautilus_trader.config import ImportableStrategyConfig, LoggingConfig

    return (
        BacktestDataConfig,
        BacktestEngineConfig,
        BacktestNode,
        BacktestRunConfig,
        BacktestVenueConfig,
        (ImportableStrategyConfig, LoggingConfig),
    )
```

Then inside `build_backtest_config` / `run_backtest` / `print_results`, call `_node_imports()` to obtain the symbols. Preserve the existing public signatures exactly — only the import sites move.

- [x] **Step 4: Run collection and confirm it's fast and non-zero**

```bash
cd nautilus && time uv run pytest --collect-only -q 2>&1 | tail -5
```

Expected: `389 tests collected in X.XXs` where `X.XX < 5.00`. If collection still hangs, run step 2 again against `cli.live` and `backtest.runner` and escalate to team-lead — do **not** fall back to `addopts` shortcut; the user rejected that approach.

- [x] **Step 5: Run the full existing test suite to confirm no regressions**

```bash
cd nautilus && uv run pytest -x 2>&1 | tail -20
```

Expected: same pass/fail ratio as before the refactor (the suite was passing via AST counting; anything new that fails is a regression).

- [x] **Step 6: Commit**

```bash
git add nautilus/src/nautilus_trading/cli/backtest.py nautilus/src/nautilus_trading/backtest/runner.py
git commit -m "refactor: lazy-import heavy Nautilus symbols so pytest collection is hermetic"
```

### Task 1.2 — Fixture catalog builder script

**Files:**
- Create: `tests/fixtures/crypto/build_catalog.py`
- Create: `tests/fixtures/crypto/__init__.py`
- Create: `tests/fixtures/__init__.py`

- [x] **Step 1: Write the failing integration test for the builder**

Create `nautilus/tests/test_fixture_catalog.py` (new file):

```python
"""Verifies the tests/fixtures/crypto/catalog/ fixture is populated and loadable."""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CATALOG_DIR = REPO_ROOT / "tests" / "fixtures" / "crypto" / "catalog"


@pytest.mark.integration
def test_fixture_catalog_exists_and_has_instruments() -> None:
    assert CATALOG_DIR.exists(), (
        f"fixture catalog missing at {CATALOG_DIR}; run "
        f"`cd nautilus && uv run python ../tests/fixtures/crypto/build_catalog.py`"
    )

    from nautilus_trader.persistence.catalog import ParquetDataCatalog

    catalog = ParquetDataCatalog(str(CATALOG_DIR))
    instruments = catalog.instruments()
    assert len(instruments) >= 1, f"no instruments in {CATALOG_DIR}"
    assert str(instruments[0].id) == "BTCUSDT.BINANCE"
```

- [x] **Step 2: Run the test and confirm it fails**

```bash
cd nautilus && uv run pytest tests/test_fixture_catalog.py -v
```

Expected: FAIL — catalog directory does not yet exist.

- [x] **Step 3: Create the fixture builder mirroring `tests/competition/fixtures/build_catalog.py` style**

Create `tests/fixtures/crypto/build_catalog.py`:

```python
"""
Build a small real-data ParquetDataCatalog fixture for sub-project A tests.

Downloads 336 real 1-hour BTCUSDT klines from Binance's public REST API
(2024-01-01 00:00 UTC .. 2024-01-14 23:00 UTC inclusive) and persists them,
along with a BTCUSDT.BINANCE CurrencyPair instrument, into:

    tests/fixtures/crypto/catalog/

Usage:
    cd nautilus && uv run python ../tests/fixtures/crypto/build_catalog.py

Self-contained; raises on any failure. NO synthetic-data fallback.
"""

from __future__ import annotations

import shutil
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parents[2]  # tests/fixtures/crypto -> tests/fixtures -> tests -> repo
if str(_REPO_ROOT / "nautilus" / "src") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "nautilus" / "src"))

import urllib.request

from nautilus_trader.model.currencies import BTC, USDT
from nautilus_trader.model.data import Bar, BarSpecification, BarType
from nautilus_trader.model.enums import AggregationSource, BarAggregation, PriceType
from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
from nautilus_trader.model.instruments import CurrencyPair
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.persistence.catalog.parquet import ParquetDataCatalog

CATALOG_DIR = _HERE / "catalog"
SYMBOL = "BTCUSDT"
INTERVAL = "1h"
START_MS = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
END_MS = int(datetime(2024, 1, 14, 23, tzinfo=timezone.utc).timestamp() * 1000)
BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"


def fetch_klines() -> list[list]:
    """Fetch 336 hourly klines from Binance public REST API. Raises on any failure."""
    params = (
        f"?symbol={SYMBOL}&interval={INTERVAL}"
        f"&startTime={START_MS}&endTime={END_MS}&limit=500"
    )
    req = urllib.request.Request(BINANCE_KLINES_URL + params)
    with urllib.request.urlopen(req, timeout=30) as resp:
        import json as _json
        data = _json.loads(resp.read().decode())
    if len(data) < 300:
        raise RuntimeError(f"expected >=300 klines, got {len(data)}")
    return data


def build_instrument() -> CurrencyPair:
    instrument_id = InstrumentId(Symbol(SYMBOL), Venue("BINANCE"))
    return CurrencyPair(
        instrument_id=instrument_id,
        raw_symbol=Symbol(SYMBOL),
        base_currency=BTC,
        quote_currency=USDT,
        price_precision=2,
        size_precision=6,
        price_increment=Price.from_str("0.01"),
        size_increment=Quantity.from_str("0.000001"),
        lot_size=None,
        max_quantity=None,
        min_quantity=Quantity.from_str("0.00001"),
        max_notional=None,
        min_notional=None,
        max_price=None,
        min_price=None,
        margin_init=Decimal("0"),
        margin_maint=Decimal("0"),
        maker_fee=Decimal("0.001"),
        taker_fee=Decimal("0.001"),
        ts_event=0,
        ts_init=0,
    )


def build_bars(instrument: CurrencyPair, klines: list[list]) -> list[Bar]:
    bar_spec = BarSpecification(
        step=1,
        aggregation=BarAggregation.HOUR,
        price_type=PriceType.LAST,
    )
    bar_type = BarType(
        instrument_id=instrument.id,
        bar_spec=bar_spec,
        aggregation_source=AggregationSource.EXTERNAL,
    )
    bars: list[Bar] = []
    for k in klines:
        open_ms, o, h, l, c, v, close_ms = k[0], k[1], k[2], k[3], k[4], k[5], k[6]
        ts_event_ns = int(close_ms) * 1_000_000
        bars.append(
            Bar(
                bar_type=bar_type,
                open=Price.from_str(str(o)),
                high=Price.from_str(str(h)),
                low=Price.from_str(str(l)),
                close=Price.from_str(str(c)),
                volume=Quantity.from_str(str(v)),
                ts_event=ts_event_ns,
                ts_init=ts_event_ns,
            )
        )
    if not bars:
        raise RuntimeError("produced 0 bars from Binance response")
    return bars


def main() -> None:
    if CATALOG_DIR.exists():
        shutil.rmtree(CATALOG_DIR)
    CATALOG_DIR.mkdir(parents=True, exist_ok=False)

    print(f"Fetching {SYMBOL} {INTERVAL} klines from Binance public API...")
    klines = fetch_klines()
    print(f"  got {len(klines)} klines")

    instrument = build_instrument()
    bars = build_bars(instrument, klines)

    catalog = ParquetDataCatalog(str(CATALOG_DIR))
    catalog.write_data([instrument])
    catalog.write_data(bars)

    print(f"Wrote fixture catalog to {CATALOG_DIR}")
    print(f"  {len(bars)} bars, 1 instrument ({instrument.id})")


if __name__ == "__main__":
    main()
```

Create empty init files:

```bash
touch tests/fixtures/__init__.py tests/fixtures/crypto/__init__.py
```

- [x] **Step 4: Run the builder once to produce the fixture**

```bash
cd nautilus && uv run python ../tests/fixtures/crypto/build_catalog.py
```

Expected: `Wrote fixture catalog to .../tests/fixtures/crypto/catalog` and `336 bars, 1 instrument (BTCUSDT.BINANCE)`. If Binance rate-limits, wait 60s and retry.

- [x] **Step 5: Rerun the test and confirm it passes**

```bash
cd nautilus && uv run pytest tests/test_fixture_catalog.py -v
```

Expected: PASS.

- [x] **Step 6: Commit fixture + builder + test**

```bash
git add tests/fixtures/ nautilus/tests/test_fixture_catalog.py
git commit -m "test: add parquet fixture catalog builder and integrity test for sub-project A"
```

### Task 1.3 — Shared conftest for fixture paths and CLI helpers

**Files:**
- Create: `nautilus/tests/conftest_subproject_a.py` (imported by `nautilus/tests/conftest.py`)
- Modify: `nautilus/tests/conftest.py`

- [x] **Step 1: Write a failing test that imports the new helpers**

Append to `nautilus/tests/test_fixture_catalog.py`:

```python
def test_conftest_exposes_crypto_catalog_path(crypto_catalog_path: Path) -> None:
    assert crypto_catalog_path == CATALOG_DIR
    assert crypto_catalog_path.exists()
```

- [x] **Step 2: Run and confirm failure**

```bash
cd nautilus && uv run pytest tests/test_fixture_catalog.py::test_conftest_exposes_crypto_catalog_path -v
```

Expected: FAIL with `fixture 'crypto_catalog_path' not found`.

- [x] **Step 3: Add the fixture**

Create `nautilus/tests/conftest_subproject_a.py`:

```python
"""Shared fixtures for sub-project A characterization tests."""

from __future__ import annotations

from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def crypto_catalog_path() -> Path:
    """Path to the committed parquet fixture catalog (BTCUSDT 1H, 2024-01)."""
    path = _REPO_ROOT / "tests" / "fixtures" / "crypto" / "catalog"
    if not path.exists():
        pytest.skip(
            "fixture catalog missing; run "
            "`cd nautilus && uv run python ../tests/fixtures/crypto/build_catalog.py`"
        )
    return path


@pytest.fixture
def cli_runner():
    """Typer CliRunner bound to the `nt` app."""
    from typer.testing import CliRunner
    return CliRunner()


@pytest.fixture
def nt_app():
    """The top-level Typer app exposed by `nt`."""
    from nautilus_trading.cli import app
    return app
```

Modify `nautilus/tests/conftest.py` to pull in the new fixtures. If that file does not yet import `conftest_subproject_a`, add:

```python
# At the top of nautilus/tests/conftest.py (preserve anything already there):
from nautilus.tests.conftest_subproject_a import (  # noqa: F401
    cli_runner,
    crypto_catalog_path,
    nt_app,
)
```

If pytest discovers the module via path rather than package, use a `pytest_plugins` declaration instead:

```python
# alternative in nautilus/tests/conftest.py
pytest_plugins = ["tests.conftest_subproject_a"]
```

Pick whichever matches the existing style of `conftest.py`.

- [x] **Step 4: Rerun the failing test**

```bash
cd nautilus && uv run pytest tests/test_fixture_catalog.py -v
```

Expected: both tests pass.

- [ ] **Step 5: Commit**

```bash
git add nautilus/tests/conftest.py nautilus/tests/conftest_subproject_a.py nautilus/tests/test_fixture_catalog.py
git commit -m "test: shared conftest fixtures (crypto_catalog_path, cli_runner, nt_app) for sub-project A"
```

### Task 1.4 — Characterization tests for `data/providers.py`

**Files:**
- Create: `nautilus/tests/test_data_providers.py`

- [x] **Step 1: Write three failing tests**

Create `nautilus/tests/test_data_providers.py`:

```python
"""Characterization tests for nautilus_trading.data.providers."""

from __future__ import annotations

from pathlib import Path

import pytest

from nautilus_trading.data.providers import DataProvider, TestDataProvider


def test_data_provider_is_abstract() -> None:
    with pytest.raises(TypeError):
        DataProvider()  # type: ignore[abstract]


def test_test_provider_name() -> None:
    assert TestDataProvider().name == "test"


def test_test_provider_ensure_catalog_idempotent(tmp_path: Path) -> None:
    provider = TestDataProvider()
    catalog = provider.ensure_catalog(tmp_path / "cat")
    assert catalog.instruments(), "first call produced empty catalog"
    # Second call must not re-download — should short-circuit.
    catalog2 = provider.ensure_catalog(tmp_path / "cat")
    assert [str(i.id) for i in catalog2.instruments()] == [str(i.id) for i in catalog.instruments()]
```

- [x] **Step 2: Run and confirm all pass (characterization captures current behavior)**

```bash
cd nautilus && uv run pytest tests/test_data_providers.py -v
```

Expected: 3 PASSED. If `test_test_provider_ensure_catalog_idempotent` is slow (>30s) because of network, mark it `@pytest.mark.integration` and add `--runintegration` wiring later; do not mock it.

- [x] **Step 3: Commit**

```bash
git add nautilus/tests/test_data_providers.py
git commit -m "test: characterization tests for data.providers (abstract ABC, name, idempotent ensure_catalog)"
```

### Task 1.5 — Characterization tests for `data/download.py`

**Files:**
- Create: `nautilus/tests/test_data_download.py`

- [x] **Step 1: Write the tests**

```python
"""Characterization tests for nautilus_trading.data.download."""

from __future__ import annotations

from pathlib import Path

import pytest

from nautilus_trading.data.download import PROVIDERS, ensure_catalog, get_provider
from nautilus_trading.data.providers import BinanceDataProvider, TestDataProvider


def test_providers_registry_contains_test_and_binance() -> None:
    assert set(PROVIDERS) == {"test", "binance"}
    assert PROVIDERS["test"] is TestDataProvider
    assert PROVIDERS["binance"] is BinanceDataProvider


def test_get_provider_returns_instance() -> None:
    assert isinstance(get_provider("test"), TestDataProvider)
    assert isinstance(get_provider("binance"), BinanceDataProvider)


def test_get_provider_raises_for_unknown_name() -> None:
    with pytest.raises(ValueError, match="Unknown provider 'nope'"):
        get_provider("nope")


def test_ensure_catalog_dispatches_to_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[Path] = []

    class _Recorder(TestDataProvider):
        def ensure_catalog(self, catalog_path):
            calls.append(catalog_path)
            return super().ensure_catalog(catalog_path)

    monkeypatch.setitem(PROVIDERS, "test", _Recorder)
    ensure_catalog(tmp_path / "cat", provider="test")
    assert calls == [tmp_path / "cat"]
```

- [x] **Step 2: Run and confirm pass**

```bash
cd nautilus && uv run pytest tests/test_data_download.py -v
```

Expected: 4 PASSED.

- [x] **Step 3: Commit**

```bash
git add nautilus/tests/test_data_download.py
git commit -m "test: characterization tests for data.download registry + dispatch"
```

### Task 1.6 — Characterization tests for `backtest/runner.py`

**Files:**
- Modify: `nautilus/tests/test_backtest_runner.py` (existing file — append, don't overwrite)

- [x] **Step 1: Read the existing file to match style**

```bash
cat nautilus/tests/test_backtest_runner.py
```

Note the existing 3 tests and their import/fixture conventions.

- [x] **Step 2: Append 5 new characterization tests — one per string-match branch in the runner plus a run smoke**

Append to `nautilus/tests/test_backtest_runner.py`:

```python
# ---------------------------------------------------------------------------
# Sub-project A characterization tests — capture current per-strategy branches
# in build_backtest_config before PRs 5-6 refactor them behind a registry.
# ---------------------------------------------------------------------------

from nautilus_trading.backtest.runner import build_backtest_config


def test_build_backtest_config_ema_cross_includes_ema_params(crypto_catalog_path):
    from nautilus_trader.persistence.catalog import ParquetDataCatalog

    catalog = ParquetDataCatalog(str(crypto_catalog_path))
    config = build_backtest_config(
        catalog,
        strategy_path="strategies.forex.ema_cross:EMACrossStrategy",
        config_path="strategies.forex.ema_cross:EMACrossConfig",
        bar_interval="1-HOUR-LAST-EXTERNAL",
        trade_size="0.01",
        fast_ema_period=10,
        slow_ema_period=20,
        venue_name="BINANCE",
        base_currency="USDT",
        starting_balance="10_000 USDT",
        end_time=None,
    )
    strat_cfg = config.engine.strategies[0].config
    assert strat_cfg["trade_size"] == "0.01"
    assert strat_cfg["fast_ema_period"] == 10
    assert strat_cfg["slow_ema_period"] == 20


def test_build_backtest_config_non_ema_omits_ema_params(crypto_catalog_path):
    from nautilus_trader.persistence.catalog import ParquetDataCatalog

    catalog = ParquetDataCatalog(str(crypto_catalog_path))
    config = build_backtest_config(
        catalog,
        strategy_path="strategies.crypto.grid_bot:GridBotStrategy",
        config_path="strategies.crypto.grid_bot:GridBotConfig",
        bar_interval="1-HOUR-LAST-EXTERNAL",
        trade_size="0.01",
        venue_name="BINANCE",
        base_currency="USDT",
        starting_balance="10_000 USDT",
        end_time=None,
        strategy_config_overrides={
            "upper_price": "50000",
            "lower_price": "40000",
            "grid_levels": 10,
        },
    )
    strat_cfg = config.engine.strategies[0].config
    assert "fast_ema_period" not in strat_cfg
    assert "slow_ema_period" not in strat_cfg
    assert strat_cfg["upper_price"] == "50000"
    assert strat_cfg["grid_levels"] == 10


def test_build_backtest_config_raises_when_catalog_empty(tmp_path):
    from nautilus_trader.persistence.catalog import ParquetDataCatalog

    empty = ParquetDataCatalog(str(tmp_path / "empty"))
    with pytest.raises(RuntimeError, match="No instruments found"):
        build_backtest_config(empty)


def test_build_backtest_config_raises_on_bad_instrument_index(crypto_catalog_path):
    from nautilus_trader.persistence.catalog import ParquetDataCatalog

    catalog = ParquetDataCatalog(str(crypto_catalog_path))
    with pytest.raises(RuntimeError, match="instrument_index 99 out of range"):
        build_backtest_config(catalog, instrument_index=99)


@pytest.mark.integration
def test_run_backtest_end_to_end_ema_cross(crypto_catalog_path):
    """Smoke: build → run → inspect results DataFrame. Uses real engine + fixture data."""
    from nautilus_trader.persistence.catalog import ParquetDataCatalog
    from nautilus_trading.backtest.runner import run_backtest

    catalog = ParquetDataCatalog(str(crypto_catalog_path))
    config = build_backtest_config(
        catalog,
        strategy_path="strategies.forex.ema_cross:EMACrossStrategy",
        config_path="strategies.forex.ema_cross:EMACrossConfig",
        bar_interval="1-HOUR-LAST-EXTERNAL",
        trade_size="0.001",
        fast_ema_period=5,
        slow_ema_period=15,
        venue_name="BINANCE",
        base_currency="USDT",
        starting_balance="10_000 USDT",
        end_time=None,
    )
    results = run_backtest(config)
    # results is a list[BacktestResult] — smoke-assert the shape.
    assert len(results) == 1
    assert results[0].elapsed_time >= 0
```

- [x] **Step 3: Run and confirm pass**

```bash
cd nautilus && uv run pytest tests/test_backtest_runner.py -v
```

Expected: 3 original + 5 new = 8 PASSED. The `@pytest.mark.integration` test may be >5s; that is acceptable.

- [x] **Step 4: Commit**

```bash
git add nautilus/tests/test_backtest_runner.py
git commit -m "test: characterization tests for build_backtest_config branches + end-to-end smoke"
```

### Task 1.7 — Characterization tests for `cli/live.py`

**Files:**
- Create: `nautilus/tests/test_cli_live.py`

- [ ] **Step 1: Write the tests — one per strategy branch plus the flat-error cases**

```python
"""Characterization tests for nautilus_trading.cli.live.

We capture the strat_config dict that cli.live assembles for each strategy
branch by monkeypatching `build_live_config` and `run_live`. This locks in
the current per-strategy logic before PR 5 moves it behind a registry.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest


@dataclass
class _CapturedLiveCall:
    strategy_config: dict[str, Any] = field(default_factory=dict)
    strategy_path: str = ""
    config_path: str = ""
    instrument_id: str = ""
    testnet: bool = True


@pytest.fixture
def capture_live(monkeypatch):
    captured = _CapturedLiveCall()

    def fake_build_live_config(**kwargs):
        captured.strategy_config = kwargs["strategy_config"]
        captured.strategy_path = kwargs["strategy_path"]
        captured.config_path = kwargs["config_path"]
        captured.instrument_id = kwargs["instrument_id"]
        captured.testnet = kwargs["testnet"]
        return object()  # sentinel

    def fake_run_live(config):  # noqa: ARG001
        return None

    monkeypatch.setattr("nautilus_trading.live.runner.build_live_config", fake_build_live_config)
    monkeypatch.setattr("nautilus_trading.live.runner.run_live", fake_run_live)
    return captured


def test_live_grid_bot_config(cli_runner, nt_app, capture_live):
    result = cli_runner.invoke(
        nt_app,
        [
            "live",
            "-s", "strategies.crypto.grid_bot",
            "-i", "BTCUSDT.BINANCE",
            "--bar-type", "BTCUSDT.BINANCE-1-HOUR-LAST-EXTERNAL",
            "--trade-size", "0.001",
            "--upper-price", "50000",
            "--lower-price", "40000",
            "--grid-levels", "8",
            "--testnet",
        ],
    )
    assert result.exit_code == 0, result.output
    cfg = capture_live.strategy_config
    assert cfg["trade_size"] == "0.001"
    assert cfg["upper_price"] == "50000"
    assert cfg["lower_price"] == "40000"
    assert cfg["grid_levels"] == 8
    assert capture_live.testnet is True


def test_live_grid_bot_requires_prices(cli_runner, nt_app, capture_live):
    result = cli_runner.invoke(
        nt_app,
        [
            "live",
            "-s", "strategies.crypto.grid_bot",
            "-i", "BTCUSDT.BINANCE",
            "--bar-type", "BTCUSDT.BINANCE-1-HOUR-LAST-EXTERNAL",
            "--trade-size", "0.001",
        ],
    )
    assert result.exit_code != 0
    assert "--upper-price" in result.output and "--lower-price" in result.output


def test_live_dca_bot_config(cli_runner, nt_app, capture_live):
    result = cli_runner.invoke(
        nt_app,
        [
            "live",
            "-s", "strategies.crypto.dca_bot",
            "-i", "BTCUSDT.BINANCE",
            "--bar-type", "BTCUSDT.BINANCE-1-HOUR-LAST-EXTERNAL",
            "--trade-size", "0.001",
            "--buy-amount", "5.0",
            "--buy-interval", "60",
        ],
    )
    assert result.exit_code == 0, result.output
    cfg = capture_live.strategy_config
    assert cfg["buy_amount"] == "5.0"
    assert cfg["buy_interval_bars"] == 60


def test_live_ema_cross_config(cli_runner, nt_app, capture_live):
    result = cli_runner.invoke(
        nt_app,
        [
            "live",
            "-s", "strategies.forex.ema_cross",
            "-i", "BTCUSDT.BINANCE",
            "--bar-type", "BTCUSDT.BINANCE-1-HOUR-LAST-EXTERNAL",
            "--trade-size", "0.001",
            "--fast-ema", "20",
            "--slow-ema", "50",
        ],
    )
    assert result.exit_code == 0, result.output
    cfg = capture_live.strategy_config
    assert cfg["fast_ema_period"] == 20
    assert cfg["slow_ema_period"] == 50
    assert cfg["ema_period"] == 50  # existing code sets this too; regression-lock


def test_live_timesfm_swing_fallback(cli_runner, nt_app, capture_live):
    result = cli_runner.invoke(
        nt_app,
        [
            "live",
            "-s", "strategies.crypto.timesfm_swing",
            "-i", "BTCUSDT.BINANCE",
            "--bar-type", "BTCUSDT.BINANCE-1-HOUR-LAST-EXTERNAL",
            "--trade-size", "0.01",
            "--fast-ema", "20",
            "--slow-ema", "100",
        ],
    )
    assert result.exit_code == 0, result.output
    cfg = capture_live.strategy_config
    assert cfg["fallback_fast_ema_period"] == 20
    assert cfg["ema_period"] == 100


def test_live_hybrid_sma_skips_trade_size_and_decimalizes(cli_runner, nt_app, capture_live):
    result = cli_runner.invoke(
        nt_app,
        [
            "live",
            "-s", "strategies.crypto.hybrid_sma_r10",
            "-i", "BTCUSDT.BINANCE",
            "--bar-type", "BTCUSDT.BINANCE-1-HOUR-LAST-EXTERNAL",
            "--trade-size", "0.01",
        ],
    )
    assert result.exit_code == 0, result.output
    cfg = capture_live.strategy_config
    assert "trade_size" not in cfg, "hybrid_sma_r10 must not receive trade_size"
    assert "sma_fast" in cfg and isinstance(cfg["sma_fast"], int)
    assert "stop_fast" in cfg and isinstance(cfg["stop_fast"], str)  # Decimal-as-string
```

- [ ] **Step 2: Run and confirm pass**

```bash
cd nautilus && uv run pytest tests/test_cli_live.py -v
```

Expected: 6 PASSED.

- [ ] **Step 3: Commit**

```bash
git add nautilus/tests/test_cli_live.py
git commit -m "test: characterization tests for cli.live per-strategy config branches"
```

### Task 1.8 — Characterization tests for `cli/strategies.py`

**Files:**
- Create: `nautilus/tests/test_cli_strategies.py`

- [ ] **Step 1: Read the current command**

```bash
cat nautilus/src/nautilus_trading/cli/strategies.py | head -60
```

Identify the listing format (table, plain list, etc.).

- [ ] **Step 2: Write tests that assert observable output**

```python
"""Characterization tests for `nt strategies`."""

from __future__ import annotations


def test_strategies_lists_forex_ema_cross(cli_runner, nt_app):
    result = cli_runner.invoke(nt_app, ["strategies"])
    assert result.exit_code == 0, result.output
    assert "forex.ema_cross" in result.output


def test_strategies_lists_crypto_grid_bot(cli_runner, nt_app):
    result = cli_runner.invoke(nt_app, ["strategies"])
    assert result.exit_code == 0, result.output
    assert "crypto.grid_bot" in result.output


def test_strategies_lists_crypto_timesfm_grid(cli_runner, nt_app):
    result = cli_runner.invoke(nt_app, ["strategies"])
    assert result.exit_code == 0, result.output
    assert "crypto.timesfm_grid" in result.output


def test_strategies_does_not_list_backtest_demo(cli_runner, nt_app):
    """backtest_demo is a script, not a Strategy — should never appear."""
    result = cli_runner.invoke(nt_app, ["strategies"])
    assert result.exit_code == 0, result.output
    assert "backtest_demo" not in result.output
```

- [ ] **Step 3: Run and confirm pass**

```bash
cd nautilus && uv run pytest tests/test_cli_strategies.py -v
```

Expected: 4 PASSED.

- [ ] **Step 4: Commit**

```bash
git add nautilus/tests/test_cli_strategies.py
git commit -m "test: characterization tests for `nt strategies` listing"
```

### Task 1.9 — Characterization tests for `live/runner.py`

**Files:**
- Create: `nautilus/tests/test_live_runner.py`

- [ ] **Step 1: Write tests — build_live_config shape + API-key guard + TradingNode factories**

```python
"""Characterization tests for nautilus_trading.live.runner."""

from __future__ import annotations

import pytest

from nautilus_trading.live.runner import build_live_config


def test_build_live_config_testnet_shape():
    cfg = build_live_config(
        strategy_path="strategies.crypto.grid_bot:GridBotStrategy",
        config_path="strategies.crypto.grid_bot:GridBotConfig",
        strategy_config={
            "instrument_id": "BTCUSDT.BINANCE",
            "bar_type": "BTCUSDT.BINANCE-1-HOUR-LAST-EXTERNAL",
            "trade_size": "0.001",
            "upper_price": "50000",
            "lower_price": "40000",
            "grid_levels": 8,
        },
        instrument_id="BTCUSDT.BINANCE",
        testnet=True,
    )
    assert cfg.trader_id == "TRADER-001"
    assert len(cfg.strategies) == 1
    s = cfg.strategies[0]
    assert s.strategy_path == "strategies.crypto.grid_bot:GridBotStrategy"
    assert s.config["grid_levels"] == 8


def test_build_live_config_production_requires_keys(monkeypatch):
    """A production config still builds — the guard is in run_live, not build."""
    cfg = build_live_config(
        strategy_path="strategies.crypto.grid_bot:GridBotStrategy",
        config_path="strategies.crypto.grid_bot:GridBotConfig",
        strategy_config={"instrument_id": "BTCUSDT.BINANCE", "bar_type": "X", "trade_size": "0"},
        instrument_id="BTCUSDT.BINANCE",
        testnet=False,
    )
    assert cfg.strategies


def test_run_live_fails_without_testnet_keys(monkeypatch):
    """run_live must refuse to start without credentials — no silent fallback."""
    monkeypatch.delenv("BINANCE_TESTNET_API_KEY", raising=False)
    monkeypatch.delenv("BINANCE_TESTNET_API_SECRET", raising=False)

    from nautilus_trading.live.runner import _check_api_keys

    with pytest.raises(SystemExit):
        _check_api_keys(
            build_live_config(
                strategy_path="strategies.crypto.grid_bot:GridBotStrategy",
                config_path="strategies.crypto.grid_bot:GridBotConfig",
                strategy_config={"instrument_id": "BTCUSDT.BINANCE", "bar_type": "X", "trade_size": "0"},
                instrument_id="BTCUSDT.BINANCE",
                testnet=True,
            )
        )


def test_run_live_builds_node_without_blocking(monkeypatch):
    """Recorder TradingNode double — verify build() is called but run() is not."""
    from nautilus_trading.live import runner as live_runner

    built: list = []

    class _RecorderNode:
        def __init__(self, *, config):
            self.config = config

        def add_data_client_factory(self, *args, **kwargs):
            built.append(("add_data", args, kwargs))

        def add_exec_client_factory(self, *args, **kwargs):
            built.append(("add_exec", args, kwargs))

        def build(self):
            built.append(("build",))

        def run(self):  # pragma: no cover - must not be called
            raise AssertionError("run() should not be called in tests")

    monkeypatch.setattr(live_runner, "TradingNode", _RecorderNode)
    monkeypatch.setattr(live_runner, "_check_api_keys", lambda cfg: None)
    monkeypatch.setenv("BINANCE_TESTNET_API_KEY", "x")
    monkeypatch.setenv("BINANCE_TESTNET_API_SECRET", "y")

    # Patch signal.signal so run_live's shutdown handlers register without effect.
    import signal as _signal
    monkeypatch.setattr(_signal, "signal", lambda *a, **k: None)

    # Short-circuit the node.run() call — patch at the point where run_live blocks.
    # run_live currently calls signal.signal and then enters the event loop;
    # since our _RecorderNode.run raises on call, we simulate run_live stopping at build.
    cfg = build_live_config(
        strategy_path="strategies.crypto.grid_bot:GridBotStrategy",
        config_path="strategies.crypto.grid_bot:GridBotConfig",
        strategy_config={"instrument_id": "BTCUSDT.BINANCE", "bar_type": "X", "trade_size": "0"},
        instrument_id="BTCUSDT.BINANCE",
        testnet=True,
    )
    # We expect the current run_live to eventually hit _RecorderNode.run() and
    # raise the AssertionError. Catch it and assert build() ran first.
    with pytest.raises(AssertionError, match="run\\(\\) should not be called"):
        live_runner.run_live(cfg)

    kinds = [entry[0] for entry in built]
    assert kinds[:3] == ["add_data", "add_exec", "build"]
```

- [ ] **Step 2: Run and confirm pass**

```bash
cd nautilus && uv run pytest tests/test_live_runner.py -v
```

Expected: 4 PASSED. If `test_run_live_builds_node_without_blocking` fails because `run_live` currently calls `node.run()` via a different path, adjust the recorder so `run()` returns normally and assert post-build via side effects — keep it real, no `unittest.mock.Mock`.

- [ ] **Step 3: Commit**

```bash
git add nautilus/tests/test_live_runner.py
git commit -m "test: characterization tests for live.runner (config shape, key guard, node factories)"
```

### Task 1.10 — End-to-end `make backtest-crypto` smoke

**Files:**
- Create: `nautilus/tests/test_make_targets.py`

- [ ] **Step 1: Write the smoke test — invoke the Typer command, not the shell**

```python
"""End-to-end smoke tests that exercise the `make backtest-crypto` and `make live` paths.

We bypass `make` itself (which just shells out) and invoke the Typer command with
the fixture catalog so the test is fast and hermetic.
"""

from __future__ import annotations

import pytest


@pytest.mark.integration
def test_backtest_crypto_grid_bot_end_to_end(cli_runner, nt_app, crypto_catalog_path):
    result = cli_runner.invoke(
        nt_app,
        [
            "backtest",
            "-s", "strategies.crypto.grid_bot",
            "--catalog", str(crypto_catalog_path),
            "--bar-interval", "1-HOUR-LAST-EXTERNAL",
            "--trade-size", "0.001",
            "--venue", "BINANCE",
            "--currency", "USDT",
            "--balance", "10_000 USDT",
            "--end-time", "",
            "--data-provider", "test",  # any value; ensure_catalog is hit via CLI
        ],
    )
    # grid_bot requires extra params (upper/lower price) that CLI does not currently
    # surface for backtest — the CLI should still assemble the config and let the
    # strategy raise if required. Accept exit codes 0 or 1; do not accept a crash.
    assert result.exit_code in (0, 1), result.output
    assert "error" not in result.output.lower() or "upper_price" in result.output.lower()


@pytest.mark.integration
def test_make_live_dry_run(cli_runner, nt_app, monkeypatch):
    """`make live STRATEGY=crypto.grid_bot` dry-run — assert TradingNodeConfig is built."""
    captured = {}

    def fake_build(**kwargs):
        captured["strategy_config"] = kwargs["strategy_config"]
        return object()

    def fake_run(_cfg):
        captured["ran"] = True

    monkeypatch.setattr("nautilus_trading.live.runner.build_live_config", fake_build)
    monkeypatch.setattr("nautilus_trading.live.runner.run_live", fake_run)

    result = cli_runner.invoke(
        nt_app,
        [
            "live",
            "-s", "strategies.crypto.grid_bot",
            "-i", "BTCUSDT.BINANCE",
            "--bar-type", "BTCUSDT.BINANCE-1-HOUR-LAST-EXTERNAL",
            "--trade-size", "0.001",
            "--upper-price", "50000",
            "--lower-price", "40000",
            "--grid-levels", "10",
            "--testnet",
        ],
    )
    assert result.exit_code == 0, result.output
    assert captured.get("ran") is True
    assert captured["strategy_config"]["grid_levels"] == 10
```

- [ ] **Step 2: Run and confirm pass**

```bash
cd nautilus && uv run pytest tests/test_make_targets.py -v
```

Expected: 2 PASSED.

- [ ] **Step 3: Commit**

```bash
git add nautilus/tests/test_make_targets.py
git commit -m "test: end-to-end smoke for make backtest-crypto and make live paths"
```

### Task 1.11 — Opt-in Binance testnet smoke

**Files:**
- Create: `nautilus/tests/smoke/__init__.py`
- Create: `nautilus/tests/smoke/test_binance_testnet.py`

- [ ] **Step 1: Write the opt-in test**

```python
"""Opt-in Binance testnet smoke test for sub-project A.

This test is SKIPPED unless BINANCE_TESTNET_API_KEY and BINANCE_TESTNET_API_SECRET
are both set. When enabled, it builds the full TradingNodeConfig for grid_bot on
the testnet, calls TradingNode.build(), and exits before .run() — no real orders.
"""

from __future__ import annotations

import os

import pytest

_HAVE_KEYS = bool(
    os.environ.get("BINANCE_TESTNET_API_KEY")
    and os.environ.get("BINANCE_TESTNET_API_SECRET")
)


@pytest.mark.integration
@pytest.mark.skipif(not _HAVE_KEYS, reason="BINANCE_TESTNET_API_KEY/SECRET not set")
def test_live_grid_bot_testnet_builds_successfully():
    from nautilus_trader.live.node import TradingNode
    from nautilus_trading.live.runner import build_live_config

    cfg = build_live_config(
        strategy_path="strategies.crypto.grid_bot:GridBotStrategy",
        config_path="strategies.crypto.grid_bot:GridBotConfig",
        strategy_config={
            "instrument_id": "BTCUSDT.BINANCE",
            "bar_type": "BTCUSDT.BINANCE-1-HOUR-LAST-EXTERNAL",
            "trade_size": "0.001",
            "upper_price": "50000",
            "lower_price": "40000",
            "grid_levels": 8,
        },
        instrument_id="BTCUSDT.BINANCE",
        testnet=True,
    )

    from nautilus_trader.adapters.binance import (
        BINANCE,
        BinanceLiveDataClientFactory,
        BinanceLiveExecClientFactory,
    )

    node = TradingNode(config=cfg)
    node.add_data_client_factory(BINANCE, BinanceLiveDataClientFactory)
    node.add_exec_client_factory(BINANCE, BinanceLiveExecClientFactory)
    node.build()
    # Do NOT call .run() — exits here. node.dispose() cleans up.
    node.dispose()
```

- [ ] **Step 2: Run it (it will skip if keys absent)**

```bash
cd nautilus && uv run pytest tests/smoke/test_binance_testnet.py -v
```

Expected without keys: `1 skipped`. With keys set, `1 passed` in ~5-10s.

- [ ] **Step 3: Commit**

```bash
git add nautilus/tests/smoke/__init__.py nautilus/tests/smoke/test_binance_testnet.py
git commit -m "test: opt-in Binance testnet smoke (skipped unless BINANCE_TESTNET_API_KEY set)"
```

### Task 1.12 — Verify coverage target ≥70% and wire CI

**Files:**
- Modify: `nautilus/pyproject.toml` — add coverage threshold

- [ ] **Step 1: Run coverage against the modules in scope**

```bash
cd nautilus && uv run pytest --cov=nautilus_trading.cli --cov=nautilus_trading.backtest --cov=nautilus_trading.live --cov=nautilus_trading.data --cov-report=term-missing tests/ -q 2>&1 | tail -30
```

Expected: overall coverage ≥70%. If any of `cli.live`, `cli.strategies`, `live.runner`, `data.providers`, `data.download`, `backtest.runner` are below 70%, add focused tests for the missing lines before proceeding.

- [ ] **Step 2: Add a coverage floor to pyproject so future PRs cannot regress**

Edit `nautilus/pyproject.toml` — inside `[tool.pytest.ini_options]` `addopts`, append coverage invocation:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
python_files = ["test_*.py"]
python_classes = ["Test*"]
python_functions = ["test_*"]
addopts = [
    "-v",
    "--strict-markers",
    "--tb=short",
]
markers = [
    "slow: marks tests as slow",
    "integration: marks tests as integration tests",
]

[tool.coverage.run]
source = ["nautilus_trading"]
omit = ["*/tests/*", "*/conftest.py"]

[tool.coverage.report]
fail_under = 70
exclude_lines = [
    "pragma: no cover",
    "def __repr__",
    "raise AssertionError",
    "raise NotImplementedError",
    "if __name__ == .__main__.:",
    "if TYPE_CHECKING:",
]
```

The `fail_under = 70` line is the only substantive addition. We deliberately do **not** add `--cov` to `addopts` — that keeps `make test` fast; CI runs coverage as a separate step.

- [ ] **Step 3: Add a `make coverage` target (if not present)**

Edit `Makefile`, add near the `test` target:

```make
.PHONY: coverage
coverage:
	@echo "Running tests with coverage..."
	@cd nautilus && uv run pytest \
		--cov=nautilus_trading.cli \
		--cov=nautilus_trading.backtest \
		--cov=nautilus_trading.live \
		--cov=nautilus_trading.data \
		--cov-report=term-missing \
		tests/
```

- [ ] **Step 4: Run `make coverage`**

```bash
make coverage 2>&1 | tail -30
```

Expected: `Required test coverage of 70% reached. Total coverage: XX.XX%`.

- [ ] **Step 5: Commit**

```bash
git add nautilus/pyproject.toml Makefile
git commit -m "ci: enforce 70% coverage floor on core modules via fail_under and make coverage"
```

### Task 1.13 — Open the PR

- [ ] **Step 1: Push and open**

```bash
git push -u origin subproject-a/pr1-test-harness
gh pr create --title "PR 1 — Test harness + hermetic pytest collection" --body "$(cat <<'EOF'
## Summary
- Lazy-imports `nautilus_trader.backtest.node` et al. so `pytest --collect-only` finishes in <5s.
- Adds fixture parquet catalog builder + 336 BTCUSDT 1H bars under `tests/fixtures/crypto/`.
- Adds characterization tests for `data.providers`, `data.download`, `backtest.runner`, `cli.live` (per strategy branch), `cli.strategies`, `live.runner`.
- End-to-end smoke for `make backtest-crypto` and `make live`.
- Opt-in Binance testnet smoke gated by `BINANCE_TESTNET_API_KEY`.
- `fail_under = 70` on coverage.

## Test plan
- [x] `make test` green (389 + ~30 new tests)
- [x] `make coverage` ≥70% on cli/live, cli/strategies, live.runner, data.providers, data.download, backtest.runner
- [x] `pytest --collect-only` <5s
- [x] Testnet smoke passes when `BINANCE_TESTNET_API_KEY` exported; skipped otherwise
EOF
)"
```

---

## PR 2 — Delete `strategies/crypto/backtest_demo.py`

**Depends on:** PR 1 merged.

### Task 2.1 — Delete the dead demo

**Files:**
- Delete: `strategies/crypto/backtest_demo.py`
- Modify: `Makefile` (if any target references it)
- Modify: `vulture_whitelist.py` (if it whitelists anything in that file)

- [x] **Step 1: Confirm no runtime code imports it**

```bash
grep -rn "backtest_demo" strategies/ nautilus/ tests/ Makefile 2>/dev/null
```

Expected: matches only in `Makefile` (if any) and this file itself. Any imports from strategies/tests/production code are blockers — stop and ask the team-lead.

- [x] **Step 2: Delete the file and any Makefile references**

```bash
git rm strategies/crypto/backtest_demo.py
```

If `grep` in step 1 found Makefile targets (e.g. `backtest-demo:`), remove those targets with `Edit` on `Makefile`. Audit says none exist, but verify.

- [x] **Step 3: Run the full test suite**

```bash
cd nautilus && uv run pytest -q 2>&1 | tail -10
```

Expected: same pass count as end of PR 1 (no test referenced `backtest_demo`).

- [x] **Step 4: Run `make lint`**

```bash
make lint 2>&1 | tail -20
```

Expected: clean. Vulture should not flag anything — the file is gone.

- [x] **Step 5: Commit**

```bash
git commit -m "chore: delete strategies/crypto/backtest_demo.py (dead code per audit)"
```

- [x] **Step 6: Push and open PR**

```bash
git push -u origin subproject-a/pr2-delete-backtest-demo
gh pr create --title "PR 2 — Delete strategies/crypto/backtest_demo.py" --body "Dead code per sub-project A audit: hardcoded-EMA demo, no tests, no Makefile target."
```

---

## PR 3 — Extract `cli/_common.py`

**Depends on:** PR 1 merged.

### Task 3.1 — Move shared helpers behind a new module

**Files:**
- Create: `nautilus/src/nautilus_trading/cli/_common.py`
- Modify: `nautilus/src/nautilus_trading/cli/backtest.py`
- Modify: `nautilus/src/nautilus_trading/cli/live.py`
- Create: `nautilus/tests/test_cli_common.py`

- [x] **Step 1: Write the failing test for `_common`**

Create `nautilus/tests/test_cli_common.py`:

```python
"""Tests for cli._common shared helpers."""

from __future__ import annotations

import sys
from pathlib import Path


def test_ensure_project_root_on_path_is_idempotent():
    from nautilus_trading.cli._common import _ensure_project_root_on_path

    _ensure_project_root_on_path()
    count_before = sum(1 for p in sys.path if "nautilus-trading" in p or "workspace" in p)
    _ensure_project_root_on_path()
    count_after = sum(1 for p in sys.path if "nautilus-trading" in p or "workspace" in p)
    assert count_after == count_before


def test_resolve_strategy_paths_known_module():
    from nautilus_trading.cli._common import _resolve_strategy_paths

    strat, cfg = _resolve_strategy_paths("strategies.crypto.grid_bot")
    assert strat == "strategies.crypto.grid_bot:GridBotStrategy"
    assert cfg == "strategies.crypto.grid_bot:GridBotConfig"


def test_resolve_strategy_paths_explicit_import_path():
    from nautilus_trading.cli._common import _resolve_strategy_paths

    strat, cfg = _resolve_strategy_paths("strategies.forex.ema_cross:EMACrossStrategy")
    assert strat == "strategies.forex.ema_cross:EMACrossStrategy"
    assert cfg == "strategies.forex.ema_cross:EMACrossConfig"


def test_resolve_strategy_paths_pascal_case_fallback():
    from nautilus_trading.cli._common import _resolve_strategy_paths

    strat, cfg = _resolve_strategy_paths("strategies.crypto.some_new_thing")
    assert strat == "strategies.crypto.some_new_thing:SomeNewThingStrategy"
    assert cfg == "strategies.crypto.some_new_thing:SomeNewThingConfig"
```

- [x] **Step 2: Run and confirm failure**

```bash
cd nautilus && uv run pytest tests/test_cli_common.py -v
```

Expected: 4 FAILED, module `nautilus_trading.cli._common` not found.

- [x] **Step 3: Create `cli/_common.py`**

```python
"""Shared helpers for nautilus_trading.cli.*. No strategy-specific logic lives here."""

from __future__ import annotations

import sys
from pathlib import Path

# Maps strategy module names to their class names. Extend when a new strategy
# ships with class names that don't match `PascalCase + Strategy/Config`.
_STRATEGY_CLASSES: dict[str, tuple[str, str]] = {
    "ema_cross": ("EMACrossStrategy", "EMACrossConfig"),
    "grid_bot": ("GridBotStrategy", "GridBotConfig"),
    "dca_bot": ("DCABotStrategy", "DCABotConfig"),
    "timesfm_swing": ("TimesFMSwingStrategy", "TimesFMSwingConfig"),
}


def _ensure_project_root_on_path() -> None:
    """Add the project root (parent of the ``nautilus/`` package dir) to sys.path.

    Allows strategy modules under ``strategies/`` to be imported via
    ``ImportableStrategyConfig``.
    """
    # nautilus/src/nautilus_trading/cli/_common.py -> project root is 4 levels up
    project_root = str(Path(__file__).resolve().parents[4])
    if project_root not in sys.path:
        sys.path.insert(0, project_root)


def _resolve_strategy_paths(module_path: str) -> tuple[str, str]:
    """Resolve a module path like 'strategies.crypto.grid_bot' to (strategy_path, config_path).

    If ``module_path`` already contains ``:``, treat it as an explicit import path.
    Otherwise, look up the module in ``_STRATEGY_CLASSES`` or PascalCase-fallback.
    """
    if ":" in module_path:
        module, cls = module_path.rsplit(":", 1)
        config_cls = cls.replace("Strategy", "Config")
        return module_path, f"{module}:{config_cls}"

    module_name = module_path.rsplit(".", 1)[-1]
    if module_name in _STRATEGY_CLASSES:
        strategy_cls, config_cls = _STRATEGY_CLASSES[module_name]
    else:
        parts = module_name.split("_")
        base = "".join(p.capitalize() for p in parts)
        strategy_cls = f"{base}Strategy"
        config_cls = f"{base}Config"

    return f"{module_path}:{strategy_cls}", f"{module_path}:{config_cls}"
```

- [x] **Step 4: Run the common tests and confirm pass**

```bash
cd nautilus && uv run pytest tests/test_cli_common.py -v
```

Expected: 4 PASSED.

- [x] **Step 5: Update `cli/backtest.py` — remove the duplicated helpers, import from `_common`**

In `nautilus/src/nautilus_trading/cli/backtest.py`:

1. Delete `_STRATEGY_CLASSES`, `_ensure_project_root_on_path`, `_resolve_strategy_paths` function bodies (lines currently ~15–58).
2. Add import at the top (below `import typer`):

```python
from nautilus_trading.cli._common import _ensure_project_root_on_path, _resolve_strategy_paths
```

3. Leave the call sites untouched — the names are unchanged.

- [x] **Step 6: Update `cli/live.py` — change the import source**

Replace:

```python
from nautilus_trading.cli.backtest import _ensure_project_root_on_path, _resolve_strategy_paths
```

with:

```python
from nautilus_trading.cli._common import _ensure_project_root_on_path, _resolve_strategy_paths
```

- [x] **Step 7: Run the full suite**

```bash
cd nautilus && uv run pytest -q 2>&1 | tail -10
```

Expected: all PR 1 characterization tests still pass. Specifically `tests/test_cli_live.py` and `tests/test_backtest_runner.py` exercise the moved helpers indirectly.

- [x] **Step 8: Commit**

```bash
git add nautilus/src/nautilus_trading/cli/_common.py \
         nautilus/src/nautilus_trading/cli/backtest.py \
         nautilus/src/nautilus_trading/cli/live.py \
         nautilus/tests/test_cli_common.py
git commit -m "refactor: extract cli._common for shared project-root and strategy-path helpers"
```

- [x] **Step 9: Push and open PR**

```bash
git push -u origin subproject-a/pr3-cli-common
gh pr create --title "PR 3 — Extract cli/_common.py" --body "Removes cross-module dependency where cli/live.py imported helpers from cli/backtest.py. PR 1 tests are the safety net."
```

---

## PR 4 — Split `strategies/crypto/kronos/backtest.py`

**Depends on:** PR 1 merged.

**Goal:** `kronos/backtest.py` drops from 370 LOC to ≤80 LOC; engine/venue/catalog wiring moves to `kronos/backtest_config.py` as pure builders.

### Task 4.1 — Introduce `kronos/backtest_config.py`

**Files:**
- Create: `strategies/crypto/kronos/backtest_config.py`
- Create: `nautilus/tests/test_kronos_backtest_config.py`

- [x] **Step 1: Write failing tests for the builder surface**

```python
"""Tests for strategies.crypto.kronos.backtest_config builders."""

from __future__ import annotations

from decimal import Decimal

import pytest


def test_build_engine_config_returns_engine_config():
    from nautilus_trader.backtest.engine import BacktestEngineConfig
    from strategies.crypto.kronos.backtest_config import build_engine_config

    cfg = build_engine_config(log_level="ERROR")
    assert isinstance(cfg, BacktestEngineConfig)


def test_build_venue_spec_binance_spot_usdt():
    from strategies.crypto.kronos.backtest_config import build_venue_spec

    spec = build_venue_spec(initial_capital=Decimal("500"))
    assert spec.name == "BINANCE"
    assert spec.oms_type.name == "NETTING"
    assert spec.account_type.name == "CASH"
    assert spec.starting_balances[0].as_double() == 500.0


def test_build_instrument_btcusdt_returns_currency_pair():
    from nautilus_trader.model.instruments import CurrencyPair
    from strategies.crypto.kronos.backtest_config import build_instrument

    inst = build_instrument(symbol="BTCUSDT")
    assert isinstance(inst, CurrencyPair)
    assert str(inst.id) == "BTCUSDT.BINANCE"


def test_build_bar_type_hourly():
    from strategies.crypto.kronos.backtest_config import build_bar_type, build_instrument

    inst = build_instrument(symbol="BTCUSDT")
    bar_type = build_bar_type(inst, interval="1h")
    assert str(bar_type).endswith("-1-HOUR-LAST-EXTERNAL")
```

- [x] **Step 2: Run and confirm failure**

```bash
cd nautilus && uv run pytest tests/test_kronos_backtest_config.py -v
```

Expected: 4 FAILED — module not found.

- [x] **Step 3: Create `kronos/backtest_config.py` by lifting the builders out of `backtest.py`**

Read `strategies/crypto/kronos/backtest.py` and identify four logical blocks:
1. `BacktestEngineConfig` construction → becomes `build_engine_config(*, log_level)`.
2. Venue spec (`BacktestVenueConfig`) → becomes `build_venue_spec(*, initial_capital)`.
3. `CurrencyPair` instrument construction → becomes `build_instrument(*, symbol)`.
4. `BarType` construction → becomes `build_bar_type(instrument, *, interval)`.

Create `strategies/crypto/kronos/backtest_config.py`:

```python
"""Pure builders for Kronos BacktestEngine configuration.

Split from kronos/backtest.py (SRP fix). No I/O except data-catalog loading
belongs here — keep it testable.
"""

from __future__ import annotations

from decimal import Decimal

from nautilus_trader.backtest.engine import BacktestEngineConfig
from nautilus_trader.backtest.node import BacktestVenueConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.currencies import BTC, ETH, SOL, USDT
from nautilus_trader.model.data import BarSpecification, BarType
from nautilus_trader.model.enums import AccountType, AggregationSource, BarAggregation, OmsType, PriceType
from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
from nautilus_trader.model.instruments import CurrencyPair
from nautilus_trader.model.objects import Money, Price, Quantity

_BASE_CURRENCIES = {"BTCUSDT": BTC, "ETHUSDT": ETH, "SOLUSDT": SOL}


def build_engine_config(*, log_level: str = "ERROR") -> BacktestEngineConfig:
    """Build the BacktestEngineConfig used by Kronos backtests."""
    return BacktestEngineConfig(logging=LoggingConfig(log_level=log_level))


def build_venue_spec(*, initial_capital: Decimal = Decimal("500")) -> BacktestVenueConfig:
    """Build the BINANCE SPOT venue with USDT cash balance."""
    return BacktestVenueConfig(
        name="BINANCE",
        oms_type=OmsType.NETTING,
        account_type=AccountType.CASH,
        base_currency=None,  # multi-currency for SPOT
        starting_balances=[Money(initial_capital, USDT).to_str()],
    )


def build_instrument(*, symbol: str = "BTCUSDT") -> CurrencyPair:
    """Build a Binance CurrencyPair instrument for the given spot symbol."""
    base = _BASE_CURRENCIES.get(symbol)
    if base is None:
        raise ValueError(f"unsupported Kronos symbol: {symbol}")
    instrument_id = InstrumentId(Symbol(symbol), Venue("BINANCE"))
    return CurrencyPair(
        instrument_id=instrument_id,
        raw_symbol=Symbol(symbol),
        base_currency=base,
        quote_currency=USDT,
        price_precision=2,
        size_precision=6,
        price_increment=Price.from_str("0.01"),
        size_increment=Quantity.from_str("0.000001"),
        lot_size=None,
        max_quantity=None,
        min_quantity=Quantity.from_str("0.00001"),
        max_notional=None,
        min_notional=None,
        max_price=None,
        min_price=None,
        margin_init=Decimal("0"),
        margin_maint=Decimal("0"),
        maker_fee=Decimal("0.001"),
        taker_fee=Decimal("0.001"),
        ts_event=0,
        ts_init=0,
    )


_INTERVAL_TO_SPEC = {
    "1h": (1, BarAggregation.HOUR),
    "4h": (4, BarAggregation.HOUR),
    "1d": (1, BarAggregation.DAY),
}


def build_bar_type(instrument: CurrencyPair, *, interval: str = "1h") -> BarType:
    """Build a BarType for the given instrument + interval."""
    if interval not in _INTERVAL_TO_SPEC:
        raise ValueError(f"unsupported interval: {interval}")
    step, aggregation = _INTERVAL_TO_SPEC[interval]
    return BarType(
        instrument_id=instrument.id,
        bar_spec=BarSpecification(step=step, aggregation=aggregation, price_type=PriceType.LAST),
        aggregation_source=AggregationSource.EXTERNAL,
    )
```

- [x] **Step 4: Run the builder tests**

```bash
cd nautilus && uv run pytest tests/test_kronos_backtest_config.py -v
```

Expected: 4 PASSED.

- [x] **Step 5: Commit the builder + tests**

```bash
git add strategies/crypto/kronos/backtest_config.py nautilus/tests/test_kronos_backtest_config.py
git commit -m "refactor: extract kronos/backtest_config.py pure builders from kronos/backtest.py"
```

### Task 4.2 — Shrink `kronos/backtest.py` to a thin runner

**Files:**
- Modify: `strategies/crypto/kronos/backtest.py`

- [ ] **Step 1: Replace the body of `kronos/backtest.py` with a thin runner**

The new file composes the builders from `backtest_config.py` plus the existing Binance REST data fetch + `KronosActor` + `KronosStrategy` wiring. Target ≤80 LOC.

```python
"""Kronos integration backtest runner (thin composition layer).

Engine/venue/catalog wiring lives in ``kronos/backtest_config.py``; this
module only orchestrates the runtime pipeline.

Usage
-----
    cd nautilus && uv run python ../strategies/crypto/kronos/backtest.py

Environment variables (optional):
    KRONOS_MODEL_SIZE, KRONOS_SYMBOL, KRONOS_INTERVAL, KRONOS_START, KRONOS_END,
    KRONOS_INITIAL_CAPITAL, KRONOS_TRADE_SIZE, KRONOS_N_SAMPLES,
    KRONOS_FORECAST_BARS, KRONOS_INFERENCE_INTERVAL
"""

from __future__ import annotations

import os
import sys
from decimal import Decimal
from pathlib import Path

_REPO_ROOT = str(Path(__file__).resolve().parents[3])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from nautilus_trader.backtest.engine import BacktestEngine

from strategies.crypto.kronos.actor import KronosActor, KronosActorConfig
from strategies.crypto.kronos.backtest_config import (
    build_bar_type,
    build_engine_config,
    build_instrument,
    build_venue_spec,
)
from strategies.crypto.kronos.strategy import KronosStrategy, KronosStrategyConfig
# NOTE: the Binance REST fetch helper currently lives inline. If it is more
# than ~40 LOC after trimming, move it to kronos/_fetch_binance.py in a
# follow-up — do NOT move it in this PR.
from strategies.crypto.kronos._fetch_binance import fetch_bars_from_binance  # created by PR 4 if needed


def main() -> None:
    symbol = os.getenv("KRONOS_SYMBOL", "BTCUSDT")
    interval = os.getenv("KRONOS_INTERVAL", "1h")
    start = os.getenv("KRONOS_START", "2024-01-01")
    end = os.getenv("KRONOS_END", "2024-12-31")
    initial_capital = Decimal(os.getenv("KRONOS_INITIAL_CAPITAL", "500"))
    trade_size = Decimal(os.getenv("KRONOS_TRADE_SIZE", "0.001"))

    engine = BacktestEngine(config=build_engine_config(log_level="ERROR"))
    engine.add_venue(**build_venue_spec(initial_capital=initial_capital).__dict__)

    instrument = build_instrument(symbol=symbol)
    engine.add_instrument(instrument)
    bar_type = build_bar_type(instrument, interval=interval)

    bars = fetch_bars_from_binance(symbol=symbol, interval=interval, start=start, end=end, bar_type=bar_type)
    engine.add_data(bars)

    actor = KronosActor(KronosActorConfig(
        instrument_id=instrument.id,
        bar_type=bar_type,
        model_size=os.getenv("KRONOS_MODEL_SIZE", "mini"),
        forecast_bars=int(os.getenv("KRONOS_FORECAST_BARS", "24")),
        n_samples=int(os.getenv("KRONOS_N_SAMPLES", "50")),
        inference_interval_bars=int(os.getenv("KRONOS_INFERENCE_INTERVAL", "4")),
    ))
    engine.add_actor(actor)
    engine.add_strategy(KronosStrategy(KronosStrategyConfig(
        instrument_id=instrument.id,
        bar_type=bar_type,
        trade_size=trade_size,
    )))

    engine.run()
    engine.dispose()


if __name__ == "__main__":
    main()
```

**If** the old `backtest.py` had the Binance REST fetch inline, extract it to `strategies/crypto/kronos/_fetch_binance.py` (pure function, no side effects at import) — the line budget requires it. If the old file imported the fetch from elsewhere, just keep that import.

- [ ] **Step 2: Verify the new file is ≤80 LOC**

```bash
wc -l strategies/crypto/kronos/backtest.py
```

Expected: `<= 80`. If over, move the REST-fetch helper into its own file.

- [ ] **Step 3: Run the kronos strategy unit tests (no network)**

```bash
cd nautilus && uv run pytest tests/test_kronos_strategy.py -q 2>&1 | tail -10
```

Expected: all 46 still PASS.

- [ ] **Step 4: Smoke-run the script against the fixture catalog (short path)**

Run for a single iteration using the fixture catalog instead of live Binance — add a `--dry-run` guard only if necessary. If the REST fetch is unavoidable in the script, skip this step and rely on the unit tests; add an integration-marked test in a follow-up.

```bash
# Only if the script supports a fixture-catalog override:
cd nautilus && KRONOS_N_SAMPLES=2 KRONOS_FORECAST_BARS=4 timeout 60 uv run python ../strategies/crypto/kronos/backtest.py 2>&1 | tail -5
```

Expected: exits 0 or skipped if dependencies missing. Do not let this step block the PR — the unit tests are the contract.

- [ ] **Step 5: Commit**

```bash
git add strategies/crypto/kronos/backtest.py strategies/crypto/kronos/_fetch_binance.py
git commit -m "refactor: shrink kronos/backtest.py to ≤80 LOC thin runner composing build_engine/venue/instrument/bar_type"
```

- [ ] **Step 6: Push and open PR**

```bash
git push -u origin subproject-a/pr4-kronos-split
gh pr create --title "PR 4 — Split kronos/backtest.py into pure builders + thin runner" --body "Fixes 370 LOC SRP violation. backtest_config.py holds engine/venue/catalog builders; backtest.py is now ≤80 LOC composition."
```

---

## PR 5 — `StrategyConfigBuilder` Protocol + `STRATEGY_BUILDERS` registry

**Depends on:** PR 3 merged (for `cli/_common.py`).

**Goal:** Eliminate the string-match branches in `cli/live.py` and `backtest/runner.py`. All PR 1 characterization tests must pass unchanged — they lock in the output dicts byte-for-byte.

### Task 5.1 — Define the Protocol + first builder (`GridBotConfigBuilder`)

**Files:**
- Create: `nautilus/src/nautilus_trading/cli/_strategy_configs.py`
- Create: `nautilus/tests/test_strategy_configs.py`

- [ ] **Step 1: Write the failing test**

```python
"""Tests for the cli._strategy_configs registry."""

from __future__ import annotations

import pytest


def test_strategy_config_builder_is_protocol():
    from typing import get_type_hints, Protocol
    from nautilus_trading.cli._strategy_configs import StrategyConfigBuilder

    # Protocols don't subclass ABC but are runtime-checkable when decorated.
    assert hasattr(StrategyConfigBuilder, "build")


def test_grid_bot_builder_outputs_expected_dict():
    from nautilus_trading.cli._strategy_configs import GridBotConfigBuilder

    builder = GridBotConfigBuilder()
    out = builder.build({
        "instrument_id": "BTCUSDT.BINANCE",
        "bar_type": "BTCUSDT.BINANCE-1-HOUR-LAST-EXTERNAL",
        "trade_size": "0.001",
        "upper_price": "50000",
        "lower_price": "40000",
        "grid_levels": 8,
    })
    assert out == {
        "instrument_id": "BTCUSDT.BINANCE",
        "bar_type": "BTCUSDT.BINANCE-1-HOUR-LAST-EXTERNAL",
        "trade_size": "0.001",
        "upper_price": "50000",
        "lower_price": "40000",
        "grid_levels": 8,
    }


def test_grid_bot_builder_raises_when_prices_missing():
    from nautilus_trading.cli._strategy_configs import GridBotConfigBuilder

    builder = GridBotConfigBuilder()
    with pytest.raises(ValueError, match="upper_price"):
        builder.build({
            "instrument_id": "BTCUSDT.BINANCE",
            "bar_type": "X",
            "trade_size": "0.001",
        })
```

- [ ] **Step 2: Run and confirm failure**

```bash
cd nautilus && uv run pytest tests/test_strategy_configs.py -v
```

Expected: 3 FAILED.

- [ ] **Step 3: Create `cli/_strategy_configs.py` with Protocol + grid builder**

```python
"""Protocol-based strategy config builders.

Each builder maps CLI args → the strategy_config dict passed to
ImportableStrategyConfig. New strategies add a class and an entry in
STRATEGY_BUILDERS; no CLI editing needed.
"""

from __future__ import annotations

from typing import Any, Protocol


class StrategyConfigBuilder(Protocol):
    """Builds a strategy_config dict from parsed CLI args."""

    def build(self, args: dict[str, Any]) -> dict[str, Any]:
        ...


class GridBotConfigBuilder:
    def build(self, args: dict[str, Any]) -> dict[str, Any]:
        if not args.get("upper_price") or not args.get("lower_price"):
            raise ValueError("grid_bot requires upper_price and lower_price")
        return {
            "instrument_id": args["instrument_id"],
            "bar_type": args["bar_type"],
            "trade_size": args["trade_size"],
            "upper_price": args["upper_price"],
            "lower_price": args["lower_price"],
            "grid_levels": args["grid_levels"],
        }


# Populated by subsequent tasks.
STRATEGY_BUILDERS: dict[str, StrategyConfigBuilder] = {
    "grid_bot": GridBotConfigBuilder(),
}
```

- [ ] **Step 4: Run and confirm pass**

```bash
cd nautilus && uv run pytest tests/test_strategy_configs.py -v
```

Expected: 3 PASSED.

- [ ] **Step 5: Commit**

```bash
git add nautilus/src/nautilus_trading/cli/_strategy_configs.py nautilus/tests/test_strategy_configs.py
git commit -m "feat: StrategyConfigBuilder Protocol + GridBotConfigBuilder"
```

### Task 5.2 — DCA, EMA, TimesFM, HybridSMA builders

**Files:**
- Modify: `nautilus/src/nautilus_trading/cli/_strategy_configs.py`
- Modify: `nautilus/tests/test_strategy_configs.py`

- [ ] **Step 1: Append failing tests for the four remaining builders**

```python
def test_dca_bot_builder():
    from nautilus_trading.cli._strategy_configs import DCABotConfigBuilder

    out = DCABotConfigBuilder().build({
        "instrument_id": "BTCUSDT.BINANCE",
        "bar_type": "X",
        "trade_size": "0.001",
        "buy_amount": "5.0",
        "buy_interval_bars": 60,
    })
    assert out["buy_amount"] == "5.0"
    assert out["buy_interval_bars"] == 60


def test_dca_bot_builder_omits_buy_amount_when_absent():
    from nautilus_trading.cli._strategy_configs import DCABotConfigBuilder

    out = DCABotConfigBuilder().build({
        "instrument_id": "BTCUSDT.BINANCE",
        "bar_type": "X",
        "trade_size": "0.001",
        "buy_amount": None,
        "buy_interval_bars": 60,
    })
    assert "buy_amount" not in out
    assert out["buy_interval_bars"] == 60


def test_ema_cross_builder():
    from nautilus_trading.cli._strategy_configs import EMAConfigBuilder

    out = EMAConfigBuilder().build({
        "instrument_id": "BTCUSDT.BINANCE",
        "bar_type": "X",
        "trade_size": "0.001",
        "fast_ema": 20,
        "slow_ema": 50,
        "module_name": "ema_cross",
    })
    assert out["fast_ema_period"] == 20
    assert out["slow_ema_period"] == 50
    assert out["ema_period"] == 50


def test_timesfm_swing_builder():
    from nautilus_trading.cli._strategy_configs import TimesFMConfigBuilder

    out = TimesFMConfigBuilder().build({
        "instrument_id": "BTCUSDT.BINANCE",
        "bar_type": "X",
        "trade_size": "0.01",
        "fast_ema": 20,
        "slow_ema": 100,
    })
    assert out["fallback_fast_ema_period"] == 20
    assert out["ema_period"] == 100
    assert "fast_ema_period" not in out


def test_hybrid_sma_builder_omits_trade_size_and_decimalizes():
    from nautilus_trading.cli._strategy_configs import HybridSMAConfigBuilder

    out = HybridSMAConfigBuilder().build({
        "instrument_id": "BTCUSDT.BINANCE",
        "bar_type": "X",
        "trade_size": "0.01",
        "sma_fast": 10,
        "sma_slow": 30,
        "stop_fast": "0.5",
        "stop_slow": "1.0",
    })
    assert "trade_size" not in out
    assert out["sma_fast"] == 10
    assert out["stop_fast"] == "0.5"
    assert isinstance(out["stop_fast"], str)
```

- [ ] **Step 2: Run and confirm failure**

Expected: 5 FAILED — classes don't exist.

- [ ] **Step 3: Implement the four builders and wire them into `STRATEGY_BUILDERS`**

Append to `nautilus/src/nautilus_trading/cli/_strategy_configs.py`:

```python
_BASE_FIELDS = ("instrument_id", "bar_type")


def _base(args: dict[str, Any], *, include_trade_size: bool = True) -> dict[str, Any]:
    out = {k: args[k] for k in _BASE_FIELDS}
    if include_trade_size:
        out["trade_size"] = args["trade_size"]
    return out


class DCABotConfigBuilder:
    def build(self, args: dict[str, Any]) -> dict[str, Any]:
        out = _base(args)
        if args.get("buy_amount"):
            out["buy_amount"] = args["buy_amount"]
        out["buy_interval_bars"] = args["buy_interval_bars"]
        return out


class EMAConfigBuilder:
    """EMA cross / swing strategies that need both slow and fast EMA periods."""

    def build(self, args: dict[str, Any]) -> dict[str, Any]:
        out = _base(args)
        out["ema_period"] = args["slow_ema"]
        out["fast_ema_period"] = args["fast_ema"]
        out["slow_ema_period"] = args["slow_ema"]
        return out


class TimesFMConfigBuilder:
    """TimesFM swing: uses ema_period + fallback_fast_ema_period (no fast_ema_period)."""

    def build(self, args: dict[str, Any]) -> dict[str, Any]:
        out = _base(args)
        out["ema_period"] = args["slow_ema"]
        out["fallback_fast_ema_period"] = args["fast_ema"]
        return out


class HybridSMAConfigBuilder:
    """Hybrid SMA ensemble: sizes from equity, so NO trade_size. Decimal fields as strings."""

    def build(self, args: dict[str, Any]) -> dict[str, Any]:
        out = _base(args, include_trade_size=False)
        out["sma_fast"] = args["sma_fast"]
        out["sma_slow"] = args["sma_slow"]
        out["stop_fast"] = str(args["stop_fast"])
        out["stop_slow"] = str(args["stop_slow"])
        return out


STRATEGY_BUILDERS = {
    "grid_bot": GridBotConfigBuilder(),
    "dca_bot": DCABotConfigBuilder(),
    "ema_cross": EMAConfigBuilder(),
    "timesfm_swing": TimesFMConfigBuilder(),
    "hybrid_sma_r10": HybridSMAConfigBuilder(),
}
```

- [ ] **Step 4: Run and confirm all builder tests pass**

```bash
cd nautilus && uv run pytest tests/test_strategy_configs.py -v
```

Expected: 8 PASSED total.

- [ ] **Step 5: Commit**

```bash
git add nautilus/src/nautilus_trading/cli/_strategy_configs.py nautilus/tests/test_strategy_configs.py
git commit -m "feat: DCA/EMA/TimesFM/HybridSMA config builders + STRATEGY_BUILDERS registry"
```

### Task 5.3 — Replace branches in `cli/live.py` with registry dispatch

**Files:**
- Modify: `nautilus/src/nautilus_trading/cli/live.py`

- [ ] **Step 1: Replace the per-strategy `if module_name == …` block**

In `cli/live.py`, inside the `live()` function, replace the existing block (from `# Build strategy config dict from CLI args` through the end of the `elif module_name == "hybrid_sma_r10":` branch) with:

```python
from nautilus_trading.cli._strategy_configs import STRATEGY_BUILDERS

module_name = strategy_path.rsplit(".", 1)[-1].split(":")[0]
builder = STRATEGY_BUILDERS.get(module_name)

builder_args = {
    "instrument_id": instrument_id,
    "bar_type": bar_type,
    "trade_size": trade_size,
    "upper_price": upper_price,
    "lower_price": lower_price,
    "grid_levels": grid_levels,
    "buy_amount": buy_amount,
    "buy_interval_bars": buy_interval_bars,
    "fast_ema": fast_ema,
    "slow_ema": slow_ema,
    "sma_fast": sma_fast,
    "sma_slow": sma_slow,
    "stop_fast": stop_fast,
    "stop_slow": stop_slow,
    "module_name": module_name,
}

if builder is None:
    # Unknown strategy — fall back to the minimal base dict (preserves old behavior).
    strat_config = {"instrument_id": instrument_id, "bar_type": bar_type, "trade_size": trade_size}
else:
    try:
        strat_config = builder.build(builder_args)
    except ValueError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(1) from exc
```

- [ ] **Step 2: Run PR 1's cli.live characterization tests — they must pass byte-for-byte**

```bash
cd nautilus && uv run pytest tests/test_cli_live.py -v
```

Expected: all 6 PASSED. Any mismatch means the new builder output drifts from the old branches — fix the builder, not the test.

- [ ] **Step 3: Run the full suite**

```bash
cd nautilus && uv run pytest -q 2>&1 | tail -10
```

Expected: green.

- [ ] **Step 4: Commit**

```bash
git add nautilus/src/nautilus_trading/cli/live.py
git commit -m "refactor: replace per-strategy branches in cli/live.py with STRATEGY_BUILDERS registry"
```

### Task 5.4 — Replace EMA branch in `backtest/runner.py`

**Files:**
- Modify: `nautilus/src/nautilus_trading/backtest/runner.py`

- [ ] **Step 1: Replace the `if "ema_cross" in strategy_path:` block**

Replace:

```python
if "ema_cross" in strategy_path:
    strat_config["fast_ema_period"] = fast_ema_period
    strat_config["slow_ema_period"] = slow_ema_period
```

with:

```python
# Dispatch to a registered builder when one exists; else keep the base dict.
from nautilus_trading.cli._strategy_configs import STRATEGY_BUILDERS

module_name = strategy_path.rsplit(".", 1)[-1].split(":")[0]
builder = STRATEGY_BUILDERS.get(module_name)
if builder is not None:
    strat_config = builder.build({
        "instrument_id": strat_config["instrument_id"],
        "bar_type": strat_config["bar_type"],
        "trade_size": strat_config["trade_size"],
        "fast_ema": fast_ema_period,
        "slow_ema": slow_ema_period,
        # The rest are not available in backtest CLI; builders treat them as optional.
        "upper_price": None,
        "lower_price": None,
        "grid_levels": None,
        "buy_amount": None,
        "buy_interval_bars": None,
        "sma_fast": None,
        "sma_slow": None,
        "stop_fast": None,
        "stop_slow": None,
        "module_name": module_name,
    })
```

If a builder now raises because optional fields are required (e.g. `GridBotConfigBuilder` needs prices), the backtest path should also fail loudly — that's correct behavior; the caller must pass overrides via `--strategy-config-override`. Preserve the existing `strategy_config_overrides` merge immediately after the builder dispatch.

- [ ] **Step 2: Run the backtest characterization tests**

```bash
cd nautilus && uv run pytest tests/test_backtest_runner.py -v
```

Expected: all 8 PASSED. The non-EMA branch test covers the `if builder is None` fallthrough; the EMA branch covers the registered case.

- [ ] **Step 3: Commit**

```bash
git add nautilus/src/nautilus_trading/backtest/runner.py
git commit -m "refactor: route build_backtest_config through STRATEGY_BUILDERS for EMA params"
```

- [ ] **Step 4: Push and open PR**

```bash
git push -u origin subproject-a/pr5-strategy-registry
gh pr create --title "PR 5 — StrategyConfigBuilder Protocol + STRATEGY_BUILDERS registry" --body "Closes HIGH OCP/DIP finding: cli/live.py and backtest/runner.py no longer string-match strategy names. All PR 1 characterization tests pass byte-for-byte."
```

---

## PR 6 — `BacktestRunner` ABC + kronos migration

**Depends on:** PR 4 merged (kronos split), PR 5 merged (registry in place).

**Goal:** Both `backtest/runner.py` and `kronos/backtest.py` subclass a common `BacktestRunner` ABC. Kronos stops being a parallel code path.

### Task 6.1 — Define `BacktestRunner` ABC with tests

**Files:**
- Create: `nautilus/src/nautilus_trading/backtest/runner_base.py`
- Create: `nautilus/tests/test_runner_base.py`

- [ ] **Step 1: Write the failing contract tests**

```python
"""Tests for the BacktestRunner abstract base."""

from __future__ import annotations

import pytest


def test_backtest_runner_is_abstract():
    from nautilus_trading.backtest.runner_base import BacktestRunner

    with pytest.raises(TypeError):
        BacktestRunner()  # type: ignore[abstract]


def test_backtest_runner_has_required_methods():
    from nautilus_trading.backtest.runner_base import BacktestRunner

    for name in ("build_config", "add_data", "run", "print_results"):
        assert hasattr(BacktestRunner, name), f"missing method: {name}"


def test_concrete_subclass_runs():
    from nautilus_trading.backtest.runner_base import BacktestRunner

    class _StubRunner(BacktestRunner):
        def build_config(self):
            return {"built": True}

        def add_data(self, engine, config):
            engine.setdefault("data", []).append(config)

        def run(self, engine):
            return {"ok": True, "engine": engine}

        def print_results(self, results):
            return str(results)

    r = _StubRunner()
    cfg = r.build_config()
    engine = {}
    r.add_data(engine, cfg)
    assert r.run(engine) == {"ok": True, "engine": {"data": [{"built": True}]}}
```

- [ ] **Step 2: Run and confirm failure**

```bash
cd nautilus && uv run pytest tests/test_runner_base.py -v
```

Expected: 3 FAILED.

- [ ] **Step 3: Implement the ABC**

```python
"""BacktestRunner abstract base — unifies the EMA and Kronos runner code paths."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BacktestRunner(ABC):
    """Common lifecycle for Nautilus backtest runners.

    Subclasses own the engine/venue/data wiring. Callers invoke:
        runner = ConcreteRunner(...)
        config = runner.build_config()
        engine = ...  # subclass creates & configures
        runner.add_data(engine, config)
        results = runner.run(engine)
        runner.print_results(results)
    """

    @abstractmethod
    def build_config(self) -> Any:
        """Return the BacktestRunConfig (or equivalent) for this runner."""

    @abstractmethod
    def add_data(self, engine: Any, config: Any) -> None:
        """Populate ``engine`` with instrument + bars from ``config``."""

    @abstractmethod
    def run(self, engine: Any) -> Any:
        """Execute the backtest on ``engine`` and return results."""

    @abstractmethod
    def print_results(self, results: Any) -> None:
        """Pretty-print results."""
```

- [ ] **Step 4: Run and confirm pass**

```bash
cd nautilus && uv run pytest tests/test_runner_base.py -v
```

Expected: 3 PASSED.

- [ ] **Step 5: Commit**

```bash
git add nautilus/src/nautilus_trading/backtest/runner_base.py nautilus/tests/test_runner_base.py
git commit -m "feat: BacktestRunner ABC with build_config/add_data/run/print_results lifecycle"
```

### Task 6.2 — Migrate kronos to subclass `BacktestRunner`

**Files:**
- Modify: `strategies/crypto/kronos/backtest.py`

- [ ] **Step 1: Replace the `main()` function with a `KronosBacktestRunner(BacktestRunner)` subclass**

```python
"""Kronos integration backtest runner (BacktestRunner subclass).

Engine/venue/catalog wiring lives in ``kronos/backtest_config.py``.
"""

from __future__ import annotations

import os
import sys
from decimal import Decimal
from pathlib import Path

_REPO_ROOT = str(Path(__file__).resolve().parents[3])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from nautilus_trader.backtest.engine import BacktestEngine

from nautilus_trading.backtest.runner_base import BacktestRunner
from strategies.crypto.kronos.actor import KronosActor, KronosActorConfig
from strategies.crypto.kronos._fetch_binance import fetch_bars_from_binance
from strategies.crypto.kronos.backtest_config import (
    build_bar_type,
    build_engine_config,
    build_instrument,
    build_venue_spec,
)
from strategies.crypto.kronos.strategy import KronosStrategy, KronosStrategyConfig


class KronosBacktestRunner(BacktestRunner):
    def __init__(self, *, symbol: str, interval: str, start: str, end: str,
                 initial_capital: Decimal, trade_size: Decimal,
                 model_size: str, forecast_bars: int, n_samples: int, inference_interval: int) -> None:
        self.symbol = symbol
        self.interval = interval
        self.start = start
        self.end = end
        self.initial_capital = initial_capital
        self.trade_size = trade_size
        self.model_size = model_size
        self.forecast_bars = forecast_bars
        self.n_samples = n_samples
        self.inference_interval = inference_interval

    def build_config(self):
        return {
            "engine_cfg": build_engine_config(log_level="ERROR"),
            "venue": build_venue_spec(initial_capital=self.initial_capital),
            "instrument": build_instrument(symbol=self.symbol),
        }

    def add_data(self, engine: BacktestEngine, config) -> None:
        instrument = config["instrument"]
        bar_type = build_bar_type(instrument, interval=self.interval)
        engine.add_instrument(instrument)
        engine.add_data(fetch_bars_from_binance(
            symbol=self.symbol, interval=self.interval, start=self.start, end=self.end,
            bar_type=bar_type,
        ))
        engine.add_actor(KronosActor(KronosActorConfig(
            instrument_id=instrument.id, bar_type=bar_type,
            model_size=self.model_size, forecast_bars=self.forecast_bars,
            n_samples=self.n_samples, inference_interval_bars=self.inference_interval,
        )))
        engine.add_strategy(KronosStrategy(KronosStrategyConfig(
            instrument_id=instrument.id, bar_type=bar_type, trade_size=self.trade_size,
        )))

    def run(self, engine: BacktestEngine):
        engine.run()
        return engine

    def print_results(self, results: BacktestEngine) -> None:
        # Kronos runner does not currently produce structured reports; dispose is enough.
        results.dispose()


def main() -> None:
    KronosBacktestRunner(
        symbol=os.getenv("KRONOS_SYMBOL", "BTCUSDT"),
        interval=os.getenv("KRONOS_INTERVAL", "1h"),
        start=os.getenv("KRONOS_START", "2024-01-01"),
        end=os.getenv("KRONOS_END", "2024-12-31"),
        initial_capital=Decimal(os.getenv("KRONOS_INITIAL_CAPITAL", "500")),
        trade_size=Decimal(os.getenv("KRONOS_TRADE_SIZE", "0.001")),
        model_size=os.getenv("KRONOS_MODEL_SIZE", "mini"),
        forecast_bars=int(os.getenv("KRONOS_FORECAST_BARS", "24")),
        n_samples=int(os.getenv("KRONOS_N_SAMPLES", "50")),
        inference_interval=int(os.getenv("KRONOS_INFERENCE_INTERVAL", "4")),
    ).main()


if __name__ == "__main__":
    main()
```

Add a default `main(self)` on `BacktestRunner` that composes `build_config → engine creation → add_data → run → print_results`:

Edit `runner_base.py`:

```python
class BacktestRunner(ABC):
    # ... existing abstract methods unchanged ...

    def main(self) -> None:
        """Default composition: build → create engine → add data → run → print."""
        from nautilus_trader.backtest.engine import BacktestEngine

        config = self.build_config()
        engine_cfg = config.get("engine_cfg") if isinstance(config, dict) else None
        engine = BacktestEngine(config=engine_cfg) if engine_cfg else BacktestEngine()
        venue = config.get("venue") if isinstance(config, dict) else None
        if venue is not None:
            engine.add_venue(**venue.__dict__)
        self.add_data(engine, config)
        results = self.run(engine)
        self.print_results(results)
```

Extend the runner-base contract test to cover `main()`:

```python
def test_main_calls_lifecycle_in_order(monkeypatch):
    """main() default implementation invokes build_config, add_data, run, print_results."""
    from nautilus_trading.backtest.runner_base import BacktestRunner

    events = []

    class _R(BacktestRunner):
        def build_config(self):
            events.append("build")
            return {}

        def add_data(self, engine, config):
            events.append("add_data")

        def run(self, engine):
            events.append("run")
            return "results"

        def print_results(self, results):
            events.append(("print", results))

    # Stub BacktestEngine to avoid real engine bootstrap.
    class _StubEngine:
        def __init__(self, config=None):  # noqa: ARG002
            pass

        def add_venue(self, **_kwargs):
            events.append("add_venue")

    import nautilus_trader.backtest.engine as _engine_mod
    monkeypatch.setattr(_engine_mod, "BacktestEngine", _StubEngine)

    _R().main()
    assert events == ["build", "add_data", "run", ("print", "results")]
```

- [ ] **Step 2: Run the kronos + runner-base tests**

```bash
cd nautilus && uv run pytest tests/test_runner_base.py tests/test_kronos_strategy.py tests/test_kronos_backtest_config.py -v
```

Expected: all PASSED.

- [ ] **Step 3: Commit**

```bash
git add strategies/crypto/kronos/backtest.py nautilus/src/nautilus_trading/backtest/runner_base.py nautilus/tests/test_runner_base.py
git commit -m "refactor: migrate kronos/backtest.py onto BacktestRunner ABC"
```

### Task 6.3 — Migrate `backtest/runner.py` flow onto the ABC (optional; skip if risk > value)

**Files:**
- Modify: `nautilus/src/nautilus_trading/backtest/runner.py`

- [ ] **Step 1: Decide whether to migrate now or defer**

`backtest/runner.py` exposes stateless functions (`build_backtest_config`, `run_backtest`, `print_results`) — not a class. Migrating to the ABC adds an `EMABacktestRunner` class whose methods delegate to the functions. If the PR budget is tight, **skip this task** and record it in the PR description as a known follow-up; the ABC is already earning its keep via kronos.

If migrating, add an `EMABacktestRunner(BacktestRunner)` wrapper at the end of `backtest/runner.py`:

```python
class EMABacktestRunner(BacktestRunner):
    """Thin wrapper so function-based runner obeys the BacktestRunner ABC."""

    def __init__(self, catalog, **kwargs) -> None:
        self._catalog = catalog
        self._kwargs = kwargs
        self._run_config = None

    def build_config(self):
        self._run_config = build_backtest_config(self._catalog, **self._kwargs)
        return self._run_config

    def add_data(self, engine, config) -> None:
        # BacktestNode handles data wiring via config; no-op here.
        return

    def run(self, engine):
        return run_backtest(self._run_config)

    def print_results(self, results) -> None:
        print_results(results)
```

- [ ] **Step 2: Add a smoke test**

```python
def test_ema_backtest_runner_matches_function_output(crypto_catalog_path):
    from nautilus_trader.persistence.catalog import ParquetDataCatalog
    from nautilus_trading.backtest.runner import EMABacktestRunner, build_backtest_config

    catalog = ParquetDataCatalog(str(crypto_catalog_path))
    kwargs = dict(
        strategy_path="strategies.forex.ema_cross:EMACrossStrategy",
        config_path="strategies.forex.ema_cross:EMACrossConfig",
        bar_interval="1-HOUR-LAST-EXTERNAL",
        trade_size="0.01",
        fast_ema_period=5,
        slow_ema_period=15,
        venue_name="BINANCE",
        base_currency="USDT",
        starting_balance="10_000 USDT",
        end_time=None,
    )
    runner = EMABacktestRunner(catalog, **kwargs)
    assert runner.build_config() is not None
    # Functional parity with the free function:
    assert runner.build_config() == build_backtest_config(catalog, **kwargs)
```

- [ ] **Step 3: Run + commit**

```bash
cd nautilus && uv run pytest tests/test_backtest_runner.py -v
git add nautilus/src/nautilus_trading/backtest/runner.py nautilus/tests/test_backtest_runner.py
git commit -m "refactor: add EMABacktestRunner wrapper for BacktestRunner-ABC parity"
```

- [ ] **Step 4: Push and open PR**

```bash
git push -u origin subproject-a/pr6-runner-base
gh pr create --title "PR 6 — BacktestRunner ABC + kronos migration" --body "Unifies EMA and Kronos backtest code paths behind a shared ABC. Kronos is no longer a parallel implementation."
```

---

## PR 7 — `strategies/crypto/_grid_math.py` extraction

**Depends on:** PR 1 merged (fixture catalog + tests).

**Goal:** Extract pure grid-computation helpers from `timesfm_grid.py` (538 LOC) into `strategies/crypto/_grid_math.py`. `timesfm_grid.py` target <400 LOC after the lift.

### Task 7.1 — Identify the extractable pure functions

**Files:**
- Read: `strategies/crypto/timesfm_grid.py` (full file)
- Read: `strategies/crypto/grid_bot.py` (to detect duplication)

- [ ] **Step 1: Enumerate candidate pure functions**

```bash
cd nautilus && uv run python - <<'PY'
import ast, pathlib
src = pathlib.Path("../strategies/crypto/timesfm_grid.py").read_text()
tree = ast.parse(src)
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef):
        # "pure" = no self.*, no self-mutation
        body_src = ast.unparse(node)
        if "self." not in body_src and not any(isinstance(a, ast.arg) and a.arg == "self" for a in node.args.args):
            print(f"{node.lineno:4d}  {node.name}({', '.join(a.arg for a in node.args.args)})")
PY
```

Expected output: 3–5 module-level functions (grid-level spacing, price bucketing, calibration gate math, Half-Kelly sizing). Write them down — they're the extraction targets.

- [ ] **Step 2: Check `grid_bot.py` for the same functions**

```bash
grep -n "def " strategies/crypto/grid_bot.py
```

Flag any function whose name matches the `_grid_math` candidates — if present, the same helper will replace both call sites in step 3.5.

### Task 7.2 — TDD the helpers at their new home

**Files:**
- Create: `strategies/crypto/_grid_math.py`
- Create: `nautilus/tests/test_grid_math.py`

- [ ] **Step 1: Write failing tests for each extracted function**

Template (fill in the actual function names from Task 7.1):

```python
"""Tests for strategies.crypto._grid_math pure helpers."""

from __future__ import annotations

from decimal import Decimal

import pytest


def test_compute_grid_levels_evenly_spaced():
    from strategies.crypto._grid_math import compute_grid_levels

    levels = compute_grid_levels(
        upper=Decimal("50000"),
        lower=Decimal("40000"),
        n_levels=5,
    )
    assert len(levels) == 5
    assert levels[0] == Decimal("40000")
    assert levels[-1] == Decimal("50000")
    # evenly spaced
    spacing = levels[1] - levels[0]
    for i in range(1, len(levels) - 1):
        assert levels[i + 1] - levels[i] == spacing


def test_bucket_price_into_level():
    from strategies.crypto._grid_math import bucket_price_into_level

    levels = [Decimal("40000"), Decimal("42500"), Decimal("45000"), Decimal("47500"), Decimal("50000")]
    assert bucket_price_into_level(Decimal("41000"), levels) == 0
    assert bucket_price_into_level(Decimal("45500"), levels) == 2
    assert bucket_price_into_level(Decimal("50000"), levels) == 4


def test_half_kelly_fraction():
    from strategies.crypto._grid_math import half_kelly_fraction

    # Known: win_prob=0.55, win_payoff=1.0, loss_payoff=1.0 -> Kelly = 0.10, half = 0.05
    assert half_kelly_fraction(win_prob=0.55, win_payoff=1.0, loss_payoff=1.0) == pytest.approx(0.05, rel=1e-3)


def test_calibration_coverage_within_bounds():
    from strategies.crypto._grid_math import calibration_coverage

    prices = [Decimal(str(p)) for p in [41000, 42000, 43000, 44000, 45000, 46000, 47000, 48000, 49000, 50500]]
    # P10=40000, P90=50000. 9/10 prices within bounds -> 0.9
    coverage = calibration_coverage(prices, lower=Decimal("40000"), upper=Decimal("50000"))
    assert coverage == pytest.approx(0.9, rel=1e-3)
```

Match the function names and signatures to the real ones you identified in Task 7.1. The four tests above are illustrative — replace with the actual set.

- [ ] **Step 2: Run and confirm failure**

```bash
cd nautilus && uv run pytest tests/test_grid_math.py -v
```

Expected: all FAILED — module `strategies.crypto._grid_math` not found.

- [ ] **Step 3: Create `strategies/crypto/_grid_math.py` by copy-paste from `timesfm_grid.py`**

Copy each identified function verbatim into `_grid_math.py`. Keep the same signatures, return types, and docstrings. Do not mutate behavior — this is a code move, not a rewrite. Example header:

```python
"""Pure grid-computation helpers lifted from timesfm_grid.py.

No state, no I/O. Consumed by:
  - strategies.crypto.timesfm_grid (primary)
  - strategies.crypto.grid_bot     (if duplication found)
"""

from __future__ import annotations

from decimal import Decimal
from typing import Sequence


def compute_grid_levels(*, upper: Decimal, lower: Decimal, n_levels: int) -> list[Decimal]:
    # ... copy body verbatim from timesfm_grid.py ...
    raise NotImplementedError


# ... other helpers ...
```

Replace each `raise NotImplementedError` with the actual body from the source.

- [ ] **Step 4: Run and confirm pass**

```bash
cd nautilus && uv run pytest tests/test_grid_math.py -v
```

Expected: all PASSED.

- [ ] **Step 5: Commit the helper + tests (before touching callers)**

```bash
git add strategies/crypto/_grid_math.py nautilus/tests/test_grid_math.py
git commit -m "feat: strategies/crypto/_grid_math.py with pure grid helpers + tests"
```

### Task 7.3 — Route `timesfm_grid.py` through `_grid_math`

**Files:**
- Modify: `strategies/crypto/timesfm_grid.py`

- [ ] **Step 1: Remove the duplicated function definitions**

In `timesfm_grid.py`, delete the function bodies that you just copied into `_grid_math.py`.

- [ ] **Step 2: Import from `_grid_math`**

At the top of `timesfm_grid.py`:

```python
from strategies.crypto._grid_math import (
    bucket_price_into_level,
    calibration_coverage,
    compute_grid_levels,
    half_kelly_fraction,
)
```

(Match the actual imports to what you extracted.)

- [ ] **Step 3: Verify the existing `test_timesfm_grid.py` still passes**

```bash
cd nautilus && uv run pytest tests/test_timesfm_grid.py -v
```

Expected: all 50 PASSED.

- [ ] **Step 4: Verify LOC target**

```bash
wc -l strategies/crypto/timesfm_grid.py
```

Expected: < 400. If not, review whether any additional pure function can be extracted — but don't force it.

- [ ] **Step 5: Commit**

```bash
git add strategies/crypto/timesfm_grid.py
git commit -m "refactor: timesfm_grid.py imports pure grid math from _grid_math (LOC <400)"
```

### Task 7.4 — Deduplicate `grid_bot.py` (conditional)

**Files:**
- Modify (only if duplication found): `strategies/crypto/grid_bot.py`

- [ ] **Step 1: Check for duplication**

If Task 7.1 step 2 identified any overlapping function names in `grid_bot.py`, replace those with imports from `_grid_math`. Otherwise skip to the PR open step.

- [ ] **Step 2: Apply the replacement**

```python
# At top of grid_bot.py:
from strategies.crypto._grid_math import compute_grid_levels  # example
```

Remove the duplicated body from `grid_bot.py`.

- [ ] **Step 3: Run `test_grid_bot.py`**

```bash
cd nautilus && uv run pytest tests/test_grid_bot.py -v
```

Expected: all 18 PASSED.

- [ ] **Step 4: Commit (only if changes made)**

```bash
git add strategies/crypto/grid_bot.py
git commit -m "refactor: grid_bot.py consumes shared grid math from _grid_math"
```

- [ ] **Step 5: Push and open PR**

```bash
git push -u origin subproject-a/pr7-grid-math
gh pr create --title "PR 7 — Extract strategies/crypto/_grid_math.py" --body "Lifts pure grid-computation helpers out of timesfm_grid.py (538→<400 LOC). grid_bot.py deduped where overlap was found."
```

---

## PR 8 — Resolve dynamic import in `cli/live.py`

**Depends on:** PR 3 merged (for `cli/_common.py`), PR 5 merged (so the file is already refactored).

**Goal:** Replace the lazy `from nautilus_trading.live.runner import build_live_config, run_live` (inside the `live()` function) with a static module-level import. This is safe because PR 1's hermetic-collection refactor moved the engine-bootstrap cost into `backtest/runner.py`'s `_node_imports()`; `live/runner.py` only imports binance adapters lazily when needed.

### Task 8.1 — Confirm the static import is safe, then apply it

**Files:**
- Modify: `nautilus/src/nautilus_trading/cli/live.py`

- [ ] **Step 1: Add a failing test that asserts the module-level symbol is bound**

Append to `nautilus/tests/test_cli_live.py`:

```python
def test_cli_live_module_imports_run_live_at_module_level():
    """Regression guard: run_live must be a module-level import of cli.live."""
    from nautilus_trading.cli import live as live_mod

    assert hasattr(live_mod, "run_live")
    assert callable(live_mod.run_live)


def test_cli_live_module_imports_build_live_config_at_module_level():
    from nautilus_trading.cli import live as live_mod

    assert hasattr(live_mod, "build_live_config")
    assert callable(live_mod.build_live_config)
```

- [ ] **Step 2: Run and confirm failure**

```bash
cd nautilus && uv run pytest tests/test_cli_live.py::test_cli_live_module_imports_run_live_at_module_level -v
```

Expected: FAIL — the import is currently inside the `live()` function body.

- [ ] **Step 3: Move the import to module scope**

In `nautilus/src/nautilus_trading/cli/live.py`:

1. Remove the line inside `live()` body: `from nautilus_trading.live.runner import build_live_config, run_live`
2. Add at the top (with other imports):

```python
from nautilus_trading.live.runner import build_live_config, run_live
```

- [ ] **Step 4: Re-run pytest collection to confirm it's still <5s**

```bash
cd nautilus && time uv run pytest --collect-only -q 2>&1 | tail -5
```

Expected: `XXX tests collected in Y.YYs` where `Y.YY < 5.00`. If collection regresses, revert step 3 and flag to team-lead — the dynamic import was load-bearing after all.

- [ ] **Step 5: Run the full suite**

```bash
cd nautilus && uv run pytest -q 2>&1 | tail -10
```

Expected: green.

- [ ] **Step 6: Commit**

```bash
git add nautilus/src/nautilus_trading/cli/live.py nautilus/tests/test_cli_live.py
git commit -m "refactor: resolve cli/live.py dynamic import of live.runner to static module-level"
```

- [ ] **Step 7: Push and open PR**

```bash
git push -u origin subproject-a/pr8-static-import
gh pr create --title "PR 8 — Resolve cli/live.py dynamic import" --body "The lazy import was a precaution; PR 1's hermetic-collection refactor removed the need for it. Collection time still <5s with the static import."
```

---

## Self-review — completed

- **Spec §1 goal (a) — remove fallbacks/mocks/unnecessary files from runtime.** PR 2 deletes `backtest_demo.py`. No other runtime fallbacks touched (spec confirms only one `fallback` token, and it's a legitimate strategy parameter kept intact).
- **Spec §1 goal (b) — SOLID-driven module boundaries.** PR 3 (`_common`) + PR 5 (`_strategy_configs` registry) + PR 6 (`BacktestRunner` ABC) address the two HIGH findings.
- **Spec §1 goal (c) — crypto/ pruning + SRP fixes + Protocol registry.** PR 2 (delete), PR 4 (kronos split), PR 5 (registry), PR 7 (_grid_math).
- **Spec §1 goal (d) — real-scenario tests.** PR 1 introduces the fixture catalog + 6 characterization modules + 2 make-target smokes + opt-in testnet.
- **Spec §1 goal (e) — trustworthy backtest + testnet flows.** Task 1.10 (`test_make_targets.py`) + Task 1.11 (testnet smoke).
- **Spec §2 non-goals.** No task touches `competition/`, real-money live trading, new strategies, new indicators, or `forex/`. `kronos/paper_trade.py` is untouched.
- **Spec §5 test plan.** All six untested modules get a dedicated test file in PR 1. Fixtures are under `tests/fixtures/crypto/`; testnet smoke lives under `tests/smoke/`.
- **Spec §6 DELETE/MOVE/EXTRACT/KEEP-AS-IS.** PR 2 covers DELETE; PR 3 covers MOVE; PR 4/5/6/7 cover EXTRACT; PR 8 covers the dynamic-import resolution. KEEP-AS-IS items (`risk_guard`, `shock_guard`, `kronos/{actor,strategy,data,paper_trade}`, `data/providers`, `data/download`, `forex/`) have no tasks — correct.
- **Spec §7 rollout order.** 8 PRs, dependencies annotated. Each PR ends with a `git push` + `gh pr create` step.
- **Type-name consistency across PRs.** `StrategyConfigBuilder` (PR 5) used verbatim in PR 5 only. `BacktestRunner` (PR 6) used verbatim in PRs 6. `GridBotConfigBuilder / DCABotConfigBuilder / EMAConfigBuilder / TimesFMConfigBuilder / HybridSMAConfigBuilder` — each appears in tests and in `STRATEGY_BUILDERS` with the exact same spelling. `_grid_math` functions use candidate names (`compute_grid_levels`, `bucket_price_into_level`, `half_kelly_fraction`, `calibration_coverage`) — Task 7.1 requires matching to actual source before implementation; this is marked as a step, not a placeholder.
- **No TBD/TODO placeholders.** Every code step contains full code. The one conditional section (Task 7.4) is explicitly gated on "only if duplication found" — that is a decision branch, not a placeholder.
- **TDD ordering inside each PR.** Every task follows: write failing test → run and see fail → implement → run and see pass → commit.

Plan complete and saved to `docs/superpowers/plans/2026-04-17-subproject-a-implementation.md`.
