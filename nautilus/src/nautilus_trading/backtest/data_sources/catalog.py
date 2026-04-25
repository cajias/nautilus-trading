"""``CatalogDataSource`` — read instrument + bars from a local
:class:`~nautilus_trader.persistence.catalog.ParquetDataCatalog`.

Default adapter for committed-catalog crypto strategies (the 8 PR-2
backtests all point at ``catalog/`` or the test fixture catalog under
``tests/fixtures/crypto/catalog/``).

Doesn't *populate* a catalog — that's the job of
:class:`~nautilus_trading.data.providers.DataProvider`. This adapter
just reads from one that already exists.

Precision coercion
==================
The :class:`BacktestEngine` validates that every bar's price + size
precision matches the registered instrument's. ParquetDataCatalog
stores precisions verbatim from how the bars were originally
constructed — older fixtures (and any bars built via ``Price.from_str(
str(float_val))``) end up with float-artifact precision (8 decimals)
that mismatches the instrument's declared precision.

We re-emit each loaded bar with prices/quantities cast to the
instrument's declared precisions before handing it to the engine. The
underlying numeric values are unchanged — only the precision marker is
adjusted, and the cast goes through ``Price(double, precision=...)``
which rounds to the target precision.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.objects import Price, Quantity
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
        raw_bars = catalog.bars(bar_types=[bt])
        bars = [_coerce_bar_precision(b, instrument) for b in raw_bars]

        return DataSourceResult(instrument=instrument, data=bars)


def _coerce_bar_precision(bar: Bar, instrument) -> Bar:
    """Return a copy of ``bar`` whose Price/Quantity precisions match
    the instrument's declared precisions.

    BacktestEngine rejects bars whose price.precision != instrument.
    price_precision (and similarly for size). The fix is to re-emit the
    bar with the same numeric values but the right precision markers.
    """
    pp = instrument.price_precision
    sp = instrument.size_precision
    if (
        bar.open.precision == pp
        and bar.high.precision == pp
        and bar.low.precision == pp
        and bar.close.precision == pp
        and bar.volume.precision == sp
    ):
        return bar  # already matching — fast path
    return Bar(
        bar_type=bar.bar_type,
        open=Price(bar.open.as_double(), precision=pp),
        high=Price(bar.high.as_double(), precision=pp),
        low=Price(bar.low.as_double(), precision=pp),
        close=Price(bar.close.as_double(), precision=pp),
        volume=Quantity(bar.volume.as_double(), precision=sp),
        ts_event=bar.ts_event,
        ts_init=bar.ts_init,
    )
