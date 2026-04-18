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
