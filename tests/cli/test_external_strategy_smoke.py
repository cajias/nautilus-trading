"""Smoke test — a third-party package can register an external strategy via entry-point.

The fixture under ``_external_strategy_fixture/`` is a minimal pip-installable
package. We install it (editable) into the same venv pytest is running in
and verify two contracts:

1. After install, ``external_strat`` appears in
   :data:`nautilus_trading.cli._strategy_specs.STRATEGY_SPECS`.
2. ``nt strategies`` lists ``external_strat`` alongside the 9 in-repo
   strategies, with ``external-strat-fixture`` shown as the source package.

``STRATEGY_SPECS`` is computed at module-import time and cached for the
process lifetime, so the install must be followed by an
``importlib.reload()`` chain (specs → strategies → cli) to flush the cache.
The teardown reverses both steps so subsequent test modules see the
in-repo-only registry.

Implementation note: this repo is uv-managed and the venv does NOT include
``pip`` — ``python -m pip`` would fail with ``No module named pip``. We use
``uv pip install --editable`` instead, which is the project's blessed
package manager (per ``CLAUDE.md``) and operates on the active ``VIRTUAL_ENV``.
If ``uv`` is unavailable on PATH, the test skips rather than failing
spuriously.
"""

from __future__ import annotations

import importlib
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


def _flush_strategy_caches() -> None:
    """Reload the discovery chain so STRATEGY_SPECS is recomputed.

    Order matters:

    * ``_strategy_specs`` re-runs ``_discover_strategy_specs()`` and rebuilds
      the cached dict.
    * ``cli.strategies`` re-binds its ``STRATEGY_SPECS`` import to the now-
      fresh dict (otherwise it keeps the pre-reload reference).
    * ``cli`` (``__init__``) re-registers the Typer commands so ``app`` points
      at the freshly-reloaded ``strategies`` function.
    """
    import nautilus_trading.cli as cli_module
    import nautilus_trading.cli._strategy_specs as specs_module
    import nautilus_trading.cli.strategies as strategies_module

    importlib.reload(specs_module)
    importlib.reload(strategies_module)
    importlib.reload(cli_module)


def _drop_external_strat_from_sys_modules() -> None:
    """Evict the (now-uninstalled) external strat package from the import cache.

    Without this, a future ``ep.load()`` could resurrect the stale module
    object even after the .dist-info has been removed.
    """
    for mod_name in list(sys.modules):
        if mod_name == "external_strat" or mod_name.startswith("external_strat."):
            del sys.modules[mod_name]


@pytest.fixture(scope="module")
def installed_external_strategy() -> Iterator[None]:
    """Editable-install the synthetic fixture; uninstall + flush caches on teardown.

    Setuptools editable installs activate via a ``.pth``-loaded meta-path finder
    that runs at ``site.py`` init — i.e. only at fresh interpreter startup.
    Since pytest is already up, the finder isn't active, so ``ep.load()`` would
    fail with ``ModuleNotFoundError: external_strat``. We work around this by
    appending ``FIXTURE_DIR`` (the dir containing the ``external_strat/``
    package) to ``sys.path`` ourselves, then popping it back on teardown.
    """
    uv = _uv_or_skip()
    subprocess.run(
        [uv, "pip", "install", "--editable", str(FIXTURE_DIR), "--quiet"],
        check=True,
    )
    fixture_path = str(FIXTURE_DIR)
    added_path = fixture_path not in sys.path
    if added_path:
        sys.path.insert(0, fixture_path)
    _flush_strategy_caches()
    try:
        yield
    finally:
        if added_path and fixture_path in sys.path:
            sys.path.remove(fixture_path)
        # ``uv pip uninstall`` is non-interactive (no ``-y`` needed) and
        # exits 0 even if the package was already removed.
        subprocess.run(
            [uv, "pip", "uninstall", FIXTURE_DIST_NAME, "--quiet"],
            check=True,
        )
        _drop_external_strat_from_sys_modules()
        _flush_strategy_caches()


def test_external_strategy_is_discovered(installed_external_strategy: None) -> None:
    """After install, ``external_strat`` is present in ``STRATEGY_SPECS``."""
    from nautilus_trading.cli._strategy_specs import STRATEGY_SPECS

    assert "external_strat" in STRATEGY_SPECS, (
        f"external_strat not discovered; available: {sorted(STRATEGY_SPECS)}"
    )
    spec = STRATEGY_SPECS["external_strat"]
    assert spec.name == "external_strat"
    assert spec.strategy_path == "external_strat.strategy:ExternalStratStrategy"
    assert spec.config_path == "external_strat.strategy:ExternalStratConfig"


def test_external_strategy_listed_by_strategies_command(
    installed_external_strategy: None,
) -> None:
    """``nt strategies`` lists ``external_strat`` and its source distribution."""
    from typer.testing import CliRunner

    from nautilus_trading.cli import app

    runner = CliRunner()
    result = runner.invoke(app, ["strategies"])
    assert result.exit_code == 0, result.output
    assert "external_strat" in result.output
    assert "external-strat-fixture" in result.output
