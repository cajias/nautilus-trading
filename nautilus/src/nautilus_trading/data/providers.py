"""Data provider abstraction for multiple market data sources."""

from __future__ import annotations

import os
import urllib.request
from abc import ABC, abstractmethod
from pathlib import Path

from nautilus_trader.model.data import QuoteTick
from nautilus_trader.persistence.catalog import ParquetDataCatalog
from nautilus_trader.persistence.wranglers import QuoteTickDataWrangler
from nautilus_trader.test_kit.providers import CSVTickDataLoader, TestInstrumentProvider


class DataProvider(ABC):
    """Base class for market data providers."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider identifier (e.g., 'test', 'binance')."""

    @abstractmethod
    def ensure_catalog(self, catalog_path: Path) -> ParquetDataCatalog:
        """Download data and return a ready catalog."""


class TestDataProvider(DataProvider):
    """Built-in test data provider (EUR/USD ticks from nautilus_data)."""

    _DEFAULT_URL = (
        "https://raw.githubusercontent.com/nautechsystems/nautilus_data/"
        "main/raw_data/fx_hist_data/DAT_ASCII_EURUSD_T_202001.csv.gz"
    )
    _DEFAULT_FILENAME = "EURUSD_202001.csv.gz"
    _DEFAULT_PAIR = "EUR/USD"

    def __init__(
        self,
        *,
        url: str = _DEFAULT_URL,
        filename: str = _DEFAULT_FILENAME,
        pair: str = _DEFAULT_PAIR,
    ) -> None:
        self._url = url
        self._filename = filename
        self._pair = pair

    @property
    def name(self) -> str:
        return "test"

    def ensure_catalog(self, catalog_path: Path) -> ParquetDataCatalog:
        """Download sample EUR/USD tick data (if needed) and write to a Parquet catalog.

        Returns the populated catalog instance.
        """
        catalog_path.mkdir(parents=True, exist_ok=True)

        download_path = catalog_path / self._filename
        needs_download = not self._catalog_has_data(catalog_path)

        if needs_download:
            if not download_path.exists():
                print(f"Downloading sample tick data from {self._url} ...")
                urllib.request.urlretrieve(self._url, str(download_path))

            instrument = TestInstrumentProvider.default_fx_ccy(self._pair)
            wrangler = QuoteTickDataWrangler(instrument)

            df = CSVTickDataLoader.load(
                str(download_path), index_col=0, datetime_format="%Y%m%d %H%M%S%f"
            )
            df.columns = ["bid_price", "ask_price", "size"]
            ticks: list[QuoteTick] = wrangler.process(df)

            catalog = ParquetDataCatalog(str(catalog_path))
            catalog.write_data([instrument])
            catalog.write_data(ticks)

            # Clean up the compressed CSV after ingestion
            if download_path.exists():
                os.unlink(str(download_path))

            print(f"Loaded {len(ticks):,} ticks into catalog at {catalog_path}")
        else:
            catalog = ParquetDataCatalog(str(catalog_path))
            print(f"Catalog already populated at {catalog_path}")

        return catalog

    def _catalog_has_data(self, catalog_path: Path) -> bool:
        """Check whether the catalog directory already contains instrument data."""
        try:
            catalog = ParquetDataCatalog(str(catalog_path))
            instruments = catalog.instruments()
            return any(
                self._pair.replace("/", "") in str(inst.id) for inst in instruments
            )
        except Exception:
            return False
