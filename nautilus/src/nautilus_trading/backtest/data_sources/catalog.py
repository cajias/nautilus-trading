"""``CatalogDataSource`` — read instrument + bars from a local
:class:`~nautilus_trader.persistence.catalog.ParquetDataCatalog`.

Default adapter for committed-catalog crypto strategies (the 8 PR-2
backtests all point at ``catalog/`` or the test fixture catalog under
``tests/fixtures/crypto/catalog/``).

Doesn't *populate* a catalog — that's the job of
:class:`~nautilus_trading.data.providers.DataProvider`. This adapter
just reads from one that already exists.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from nautilus_trader.model.data import BarType
from nautilus_trader.persistence.catalog import ParquetDataCatalog

from nautilus_trading.backtest.data_sources import DataSourceResult


@dataclass(frozen=True)
class CatalogDataSource:
    """Read ``(Instrument, bars)`` from a Parquet catalog.

    Parameters
    ----------
    path : str
        Filesystem path to the catalog directory.
    """

    path: str

    def load(
        self,
        *,
        instrument_id: str,
        bar_type: str,
        start: str | None = None,  # noqa: ARG002 — protocol-required, slicing TBD
        end: str | None = None,  # noqa: ARG002
    ) -> DataSourceResult:
        catalog = ParquetDataCatalog(str(Path(self.path)))

        instruments = catalog.instruments()
        instrument = next(
            (inst for inst in instruments if str(inst.id) == instrument_id),
            None,
        )
        if instrument is None:
            available = ", ".join(str(i.id) for i in instruments) or "<empty catalog>"
            raise ValueError(
                f"instrument {instrument_id!r} not found in catalog at "
                f"{self.path}. Available: {available}",
            )

        # Bars are loaded by BarType — the catalog stores each bar set
        # under its full BarType key (instrument-aggregation-source).
        bt = BarType.from_str(bar_type)
        bars = catalog.bars(bar_types=[bt])

        return DataSourceResult(instrument=instrument, data=list(bars))
