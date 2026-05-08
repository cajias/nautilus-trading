"""Smoke test — a third-party package can register an external strategy via entry-point.

The fixture under ``_external_strategy_fixture/`` is a minimal pip-installable
package. We install it (editable) into the same venv pytest is running in
and verify two contracts:

1. After install, ``external_strat`` appears in
   :data:`nautilus_trading.cli._strategy_specs.STRATEGY_SPECS`.
2. ``nt strategies`` lists ``external_strat`` alongside the 9 in-repo
   strategies, with ``external-strat-fixture`` shown as the source package.

``STRATEGY_SPECS`` is computed at module-import time and cached for the
process lifetime. The assertions therefore run in a fresh subprocess so the
discovery happens AFTER the install has landed. This avoids the
``importlib.reload()`` trap: reloading ``_strategy_specs`` mints a new
``StrategySpec`` class identity, while the in-repo strategies still hold
the OLD class — every ``isinstance`` check then fails, leaving the registry
empty for every downstream test in the session.

Implementation note: this repo is uv-managed and the venv does NOT include
``pip`` — ``python -m pip`` would fail with ``No module named pip``. We use
``uv pip install --editable`` instead, which is the project's blessed
package manager (per ``CLAUDE.md``) and operates on the active ``VIRTUAL_ENV``.
If ``uv`` is unavailable on PATH, the test skips rather than failing
spuriously.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

FIXTURE_DIR = Path(__file__).parent / "_external_strategy_fixture"
FIXTURE_DIST_NAME = "external-strat-fixture"


def _uv_or_skip() -> str:
    """Locate the ``uv`` binary or skip the test if it's missing.

    The repo is uv-managed (see ``CLAUDE.md``); this skip-rather-than-fail
    path matters for IDE / one-off runs that bypass ``uv run``.
    """
    uv = shutil.which("uv")
    if uv is None:  # pragma: no cover — exercised only off-uv environments.
        pytest.skip("uv binary not found on PATH; smoke test requires uv")
    return uv


def _run_in_subprocess(code: str) -> subprocess.CompletedProcess[str]:
    """Run ``code`` in a fresh interpreter using the same Python as the test session.

    The fresh interpreter is the whole point: ``STRATEGY_SPECS`` is computed
    at module-import time, so a freshly-spawned Python sees the
    just-installed external package's entry-point and rebuilds the registry
    from scratch — without polluting the parent test session, where any
    reload trick would leave dangling ``StrategySpec`` class identities and
    break every downstream test that imports the registry.
    """
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture(scope="module")
def installed_external_strategy() -> Iterator[None]:
    """Editable-install the synthetic fixture; uninstall on teardown.

    No cache flushing or ``sys.path`` manipulation in the parent — the
    assertions run in subprocesses (see ``_run_in_subprocess``) so the
    parent's ``STRATEGY_SPECS`` is never observed and never needs to be
    recomputed.
    """
    uv = _uv_or_skip()
    subprocess.run(
        [uv, "pip", "install", "--editable", str(FIXTURE_DIR), "--quiet"],
        check=True,
    )
    try:
        yield
    finally:
        # ``uv pip uninstall`` is non-interactive (no ``-y`` needed) and
        # exits 0 even if the package was already removed.
        subprocess.run(
            [uv, "pip", "uninstall", FIXTURE_DIST_NAME, "--quiet"],
            check=True,
        )


def test_external_strategy_is_discovered(installed_external_strategy: None) -> None:
    """After install, ``external_strat`` is present in ``STRATEGY_SPECS``."""
    code = (
        "from nautilus_trading.cli._strategy_specs import STRATEGY_SPECS\n"
        "assert 'external_strat' in STRATEGY_SPECS, sorted(STRATEGY_SPECS)\n"
        "spec = STRATEGY_SPECS['external_strat']\n"
        "assert spec.name == 'external_strat', spec.name\n"
        "assert spec.strategy_path == 'external_strat.strategy:ExternalStratStrategy', spec.strategy_path\n"
        "assert spec.config_path == 'external_strat.strategy:ExternalStratConfig', spec.config_path\n"
    )
    result = _run_in_subprocess(code)
    assert result.returncode == 0, (
        f"subprocess failed (exit {result.returncode}):\n"
        f"--- stdout ---\n{result.stdout}\n"
        f"--- stderr ---\n{result.stderr}"
    )


def test_external_strategy_listed_by_strategies_command(
    installed_external_strategy: None,
) -> None:
    """``nt strategies`` lists ``external_strat`` and its source distribution."""
    code = (
        "from typer.testing import CliRunner\n"
        "from nautilus_trading.cli import app\n"
        "runner = CliRunner()\n"
        "result = runner.invoke(app, ['strategies'])\n"
        "if result.exit_code != 0:\n"
        "    print(result.output)\n"
        "    raise SystemExit(1)\n"
        "print(result.output)\n"
    )
    result = _run_in_subprocess(code)
    assert result.returncode == 0, (
        f"subprocess failed (exit {result.returncode}):\n"
        f"--- stdout ---\n{result.stdout}\n"
        f"--- stderr ---\n{result.stderr}"
    )
    assert "external_strat" in result.stdout, result.stdout
    assert "external-strat-fixture" in result.stdout, result.stdout
