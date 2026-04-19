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


@pytest.mark.integration
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
