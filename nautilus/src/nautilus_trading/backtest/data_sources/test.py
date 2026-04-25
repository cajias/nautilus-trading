"""``TestDataSource`` — wraps :class:`TestDataProvider` for end-to-end smoke.

Built for Task C's smoke test (and any future runner-level tests) so a
generic ``BacktestStrategyRunner`` invocation can run end-to-end
without a network-bound or fixture-dependent path.

Caches into a per-call ``catalog_path`` (typically a ``tmp_path`` in
tests, or a project-local scratch dir at runtime). The first call
populates via :meth:`TestDataProvider.ensure_catalog`; subsequent calls
read the populated catalog.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from nautilus_trading.backtest.data_sources import DataSourceResult

# Project-local default catalog directory. ``make backtest`` targets
# already cd into ``nautilus/``, so a cwd-relative default lands inside
# the workspace and is gitignored under ``data/``.
_DEFAULT_CATALOG_PATH = Path("data") / "test_catalog"


@dataclass(frozen=True)
class TestDataSource:
    """Sample EUR/USD ticks via :class:`TestDataProvider`.

    Parameters
    ----------
    catalog_path : Path, optional
        Where to write the populated catalog. Idempotent — populating
        an already-populated path is a no-op. Defaults to a project-
        local ``data/test_catalog/`` so YAML configs can declare
        ``data_source: {type: test}`` without an explicit path.
    """

    catalog_path: Path = field(default_factory=lambda: _DEFAULT_CATALOG_PATH)

    def load(
        self,
        *,
        instrument_id: str,
        bar_type: str,  # noqa: ARG002 — sample data is QuoteTick, not bars
        start: str | None = None,  # noqa: ARG002
        end: str | None = None,  # noqa: ARG002
    ) -> DataSourceResult:
        from nautilus_trader.persistence.catalog import ParquetDataCatalog

        from nautilus_trading.data.providers import TestDataProvider

        provider = TestDataProvider()
        catalog: ParquetDataCatalog = provider.ensure_catalog(self.catalog_path)

        instruments = catalog.instruments()
        instrument = next(
            (inst for inst in instruments if str(inst.id) == instrument_id),
            None,
        )
        if instrument is None:
            available = ", ".join(str(i.id) for i in instruments) or "<empty catalog>"
            raise ValueError(
                f"instrument {instrument_id!r} not found in test catalog at "
                f"{self.catalog_path}. Available: {available}",
            )

        ticks = catalog.quote_ticks(instrument_ids=[str(instrument.id)])
        return DataSourceResult(instrument=instrument, data=list(ticks))
