"""``DataSource`` Protocol + concrete adapters for the generic
``BacktestStrategyRunner`` (Task C of sub-project B.5 PR 2).

Three adapters ship in PR 2:

- :class:`CatalogDataSource`  — reads from an existing
  :class:`~nautilus_trader.persistence.catalog.ParquetDataCatalog`.
  Default for crypto strategies that use the committed fixture catalog.
- :class:`BinanceRestDataSource` — fetches klines via the public
  ``/api/v3/klines`` REST endpoint. Ports the logic from
  ``strategies/crypto/kronos/_fetch_binance.py`` behind the protocol;
  the original kronos helper is left in place — PR 3 retires it once
  the kronos backtest moves to the generic runner.
- :class:`TestDataSource` — wraps the existing
  :class:`~nautilus_trading.data.providers.TestDataProvider` (sample
  EUR/USD ticks). Useful for end-to-end smoke tests in Task C.

Why a new Protocol when ``data.providers.DataProvider`` already exists
=====================================================================
``DataProvider.ensure_catalog(path)`` solves a different problem:
*populating* a parquet catalog from a remote source (run-once orchestration).
``DataSource.load(...)`` solves the per-backtest-run problem of
*materializing* an ``(Instrument, bars)`` slice ready to feed
``BacktestEngine.add_instrument`` / ``add_data``. The two compose —
``DataProvider`` writes a catalog; ``CatalogDataSource`` reads from it.
Keeping them as separate abstractions keeps each one's responsibilities
small and avoids forcing every adapter to deal with a ``catalog_path``
intermediate when there isn't one (``BinanceRestDataSource`` is purely
in-memory).

ValueError discipline
=====================
``build_data_source`` and adapter constructors / ``load()`` all raise
``ValueError`` (or ``TypeError`` from msgspec / ``**kwargs``) on
malformed input — never returns ``None`` or a sentinel. This mirrors the
``StrategyConfigBuilder._base()`` contract so the ``nt backtest`` CLI
(Task D) can map ``(TypeError, ValueError) → typer.BadParameter``
exactly like ``nt paper-trade`` does today.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

__all__ = [
    "DataSource",
    "DataSourceResult",
    "build_data_source",
]


@dataclass(frozen=True)
class DataSourceResult:
    """One adapter's output for a single backtest run.

    ``data`` is a list of ``Bar`` or ``QuoteTick`` objects in
    chronological order, ready for :meth:`BacktestEngine.add_data`.
    Frozen so adapters can't accidentally mutate the buffer mid-run.
    """

    instrument: Any  # nautilus_trader.model.instruments.Instrument
    data: list[Any]  # list[Bar | QuoteTick] — both are valid engine inputs


@runtime_checkable
class DataSource(Protocol):
    """Per-run market-data adapter consumed by ``BacktestStrategyRunner``.

    Adapters are constructed with whatever they need (``path``,
    ``symbol``/``interval``, …) and are called once per backtest run.
    ``load`` returns the ``Instrument`` and the bar/tick stream; the
    runner is responsible for engine attachment.

    Parameters
    ----------
    instrument_id : str
        Engine-side identifier for the loaded instrument; adapters that
        select from a multi-instrument catalog use it as a filter.
    bar_type : str
        Engine-side bar-type string (parsed via
        :meth:`BarType.from_str`); adapters that build bars from
        non-NautilusTrader sources use it to tag the produced bars.
    start, end : str | None
        ISO date strings; required for ``binance_rest``, optional for
        ``catalog`` / ``test``. The runner forwards
        :class:`~nautilus_trading.backtest.run_config.DateRange` fields
        if a date_range was declared in YAML.
    """

    def load(
        self,
        *,
        instrument_id: str,
        bar_type: str,
        start: str | None = None,
        end: str | None = None,
    ) -> DataSourceResult: ...


def build_data_source(spec: Any) -> DataSource:
    """Dispatch a YAML ``data_source:`` block to the matching adapter.

    Parameters
    ----------
    spec : dict
        The decoded ``data_source`` block from a ``BacktestRunConfig``.
        Must contain a ``type`` discriminator (``catalog`` |
        ``binance_rest`` | ``test``); remaining keys are forwarded as
        kwargs to the adapter constructor.

    Raises
    ------
    ValueError
        ``spec`` is not a dict, missing ``type``, declares an unknown
        type, or contains a kwarg the adapter doesn't accept (the
        ``TypeError`` from ``**spec`` is re-raised as ``ValueError`` for
        a uniform CLI error contract).
    """
    if not isinstance(spec, dict):
        raise ValueError(
            f"data_source must be a dict, got {type(spec).__name__}",
        )
    payload = dict(spec)  # copy so the caller's dict stays intact
    type_ = payload.pop("type", None)
    if type_ is None:
        raise ValueError("data_source missing required field: type")

    # Lazy imports keep the top-level package import cheap; only the
    # selected adapter pulls in its (sometimes heavy) dependencies.
    adapter: DataSource
    if type_ == "catalog":
        from nautilus_trading.backtest.data_sources.catalog import CatalogDataSource

        adapter = _construct(CatalogDataSource, payload)
    elif type_ == "binance_rest":
        from nautilus_trading.backtest.data_sources.binance_rest import BinanceRestDataSource

        adapter = _construct(BinanceRestDataSource, payload)
    elif type_ == "test":
        from nautilus_trading.backtest.data_sources.test import TestDataSource

        adapter = _construct(TestDataSource, payload)
    else:
        raise ValueError(
            f"Unknown data_source type {type_!r}. Valid: catalog, binance_rest, test",
        )
    return adapter


def _construct(cls: type, kwargs: dict[str, Any]) -> "DataSource":
    """Wrap ``cls(**kwargs)`` so a TypeError (unknown / missing kwarg)
    surfaces as ValueError — uniform CLI mapping per project conventions.
    """
    try:
        return cls(**kwargs)  # type: ignore[no-any-return]
    except TypeError as exc:
        raise ValueError(f"data_source(type={cls.__name__}): {exc}") from exc
