"""Characterization tests for nautilus_trading.data.providers."""

from __future__ import annotations

from pathlib import Path

import pytest

from nautilus_trading.data.providers import DataProvider, TestDataProvider


def test_data_provider_is_abstract() -> None:
    with pytest.raises(TypeError):
        DataProvider()


def test_test_provider_name() -> None:
    assert TestDataProvider().name == "test"


@pytest.mark.integration
def test_test_provider_ensure_catalog_idempotent(tmp_path: Path) -> None:
    provider = TestDataProvider()
    catalog = provider.ensure_catalog(tmp_path / "cat")
    assert catalog.instruments(), "first call produced empty catalog"
    # Second call must not re-download — should short-circuit.
    catalog2 = provider.ensure_catalog(tmp_path / "cat")
    assert [str(i.id) for i in catalog2.instruments()] == [str(i.id) for i in catalog.instruments()]
