"""Data download and catalog management."""

from __future__ import annotations

from pathlib import Path

from nautilus_trader.persistence.catalog import ParquetDataCatalog

from nautilus_trading.data.providers import BinanceDataProvider, DataProvider, TestDataProvider

# Registry of available providers
PROVIDERS: dict[str, type[DataProvider]] = {
    "test": TestDataProvider,
    "binance": BinanceDataProvider,
}


def get_provider(name: str = "test") -> DataProvider:
    """Get a data provider by name."""
    if name not in PROVIDERS:
        available = ", ".join(PROVIDERS.keys())
        raise ValueError(f"Unknown provider '{name}'. Available: {available}")
    return PROVIDERS[name]()


def ensure_catalog(catalog_path: Path, provider: str = "test") -> ParquetDataCatalog:
    """Download data using the specified provider and return a catalog."""
    return get_provider(provider).ensure_catalog(catalog_path)
