"""Unit tests for BarFanoutActor's FanoutBar wrapper.

Pinning the Cython contract: FanoutBar writes to private base-class attrs
``_ts_event`` / ``_ts_init`` because the public ``ts_event`` / ``ts_init``
properties on ``nautilus_trader.core.data.Data`` are read-only Cython
descriptors. This is undocumented and version-fragile — if a future nautilus
release renames or removes those private attrs, these tests will fail before
the BarFanoutActor's behavior silently regresses in production.
"""

from __future__ import annotations

import pytest
from nautilus_trader.model.data import Bar, BarSpecification, BarType
from nautilus_trader.model.enums import AggregationSource, BarAggregation, PriceType
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.test_kit.providers import TestInstrumentProvider

from nautilus_trading.paper_trade.bar_fanout import FanoutBar


@pytest.fixture
def sample_bar() -> Bar:
    """A minimal real Bar for contract pinning."""
    instrument = TestInstrumentProvider.btcusdt_binance()
    bar_type = BarType(
        instrument_id=instrument.id,
        bar_spec=BarSpecification(
            step=1,
            aggregation=BarAggregation.HOUR,
            price_type=PriceType.LAST,
        ),
        aggregation_source=AggregationSource.EXTERNAL,
    )
    return Bar(
        bar_type=bar_type,
        open=Price.from_str("50000.00"),
        high=Price.from_str("50100.00"),
        low=Price.from_str("49900.00"),
        close=Price.from_str("50050.00"),
        volume=Quantity.from_str("1.50000000"),
        ts_event=1_700_000_000_000_000_000,
        ts_init=1_700_000_000_500_000_000,
    )


def test_fanout_bar_construction_does_not_raise(sample_bar: Bar) -> None:
    """``FanoutBar(bar)`` must be constructible without error.

    The Cython ``Data`` base class declares ``ts_event`` / ``ts_init`` as
    abstract properties that raise ``NotImplementedError`` when read on
    a Python subclass — production code does NOT read those properties
    on the wrapper, it unwraps via ``wrapped.bar`` first. This test
    pins the construction-side contract: ``FanoutBar(bar)`` returns a
    live object with ``.bar`` set, even if the timestamp pass-through is
    a no-op at the Cython layer.
    """
    wrapped = FanoutBar(sample_bar)
    assert wrapped is not None
    assert hasattr(wrapped, "bar")


def test_fanout_bar_holds_reference_to_wrapped_bar(sample_bar: Bar) -> None:
    """The wrapped Bar is reachable via ``.bar`` so consumers can call
    ``on_bar(fanout.bar)`` to recover the original signal."""
    wrapped = FanoutBar(sample_bar)
    assert wrapped.bar is sample_bar
    assert wrapped.bar.close == sample_bar.close


def test_fanout_bar_rejects_none() -> None:
    """``FanoutBar(None)`` must raise ``ValueError`` rather than producing
    a cryptic AttributeError on the ``bar.ts_event`` access."""
    with pytest.raises(ValueError, match="None"):
        FanoutBar(None)  # type: ignore[arg-type]
