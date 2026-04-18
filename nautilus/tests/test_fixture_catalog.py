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


def test_conftest_exposes_crypto_catalog_path(crypto_catalog_path: Path) -> None:
    assert crypto_catalog_path == CATALOG_DIR
    assert crypto_catalog_path.exists()
