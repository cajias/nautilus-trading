"""Unit tests for the Hybrid SMA R10 ensemble strategy.

Covers the pure-logic helpers extracted from the strategy class:
    - compute_sma against a reference sequence
    - SubState transitions: entry, no-reentry, SMA exit, trailing-stop exit
    - compute_target_fraction (0.0 / 0.5 / 1.0)
    - compute_rebalance_delta sizing and skip-below-increment behaviour

These tests deliberately avoid the BacktestEngine: the strategy delegates all
signal logic to module-level functions so they can be exercised directly.
"""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

import pytest

PROJECT_ROOT = str(Path(__file__).resolve().parents[1])
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from nautilus_trader.model.data import BarType  # noqa: E402
from nautilus_trader.model.identifiers import InstrumentId  # noqa: E402
from strategies.crypto.hybrid_sma_r10 import (  # noqa: E402
    HybridSMAR10Config,
    SubState,
    compute_rebalance_delta,
    compute_sma,
    compute_target_fraction,
    update_sub_state,
)

INSTRUMENT_ID = InstrumentId.from_str("BNBUSDT.BINANCE")
BAR_TYPE = BarType.from_str("BNBUSDT.BINANCE-1-DAY-LAST-EXTERNAL")


def _decimals(values: list[float]) -> list[Decimal]:
    return [Decimal(str(v)) for v in values]


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


class TestConfig:
    def test_defaults(self) -> None:
        config = HybridSMAR10Config(instrument_id=INSTRUMENT_ID, bar_type=BAR_TYPE)
        assert config.sma_fast == 20
        assert config.sma_slow == 30
        assert config.stop_fast == Decimal("0.07")
        assert config.stop_slow == Decimal("0.08")
        assert config.capital_fraction == Decimal("0.99")

    def test_frozen(self) -> None:
        config = HybridSMAR10Config(instrument_id=INSTRUMENT_ID, bar_type=BAR_TYPE)
        with pytest.raises(AttributeError):
            config.sma_fast = 50  # type: ignore[misc]

    def test_overrides(self) -> None:
        config = HybridSMAR10Config(
            instrument_id=INSTRUMENT_ID,
            bar_type=BAR_TYPE,
            sma_fast=10,
            sma_slow=40,
            stop_fast=Decimal("0.05"),
            stop_slow=Decimal("0.10"),
        )
        assert config.sma_fast == 10
        assert config.sma_slow == 40
        assert config.stop_fast == Decimal("0.05")
        assert config.stop_slow == Decimal("0.10")


# ---------------------------------------------------------------------------
# compute_sma
# ---------------------------------------------------------------------------


class TestComputeSMA:
    def test_returns_none_when_insufficient(self) -> None:
        assert compute_sma(_decimals([1.0, 2.0]), period=3) is None

    def test_period_zero_returns_none(self) -> None:
        assert compute_sma(_decimals([1.0, 2.0, 3.0]), period=0) is None

    def test_matches_reference(self) -> None:
        # Reference: numpy.mean of last 3 values of [1..6] = (4 + 5 + 6) / 3
        sma = compute_sma(_decimals([1, 2, 3, 4, 5, 6]), period=3)
        assert sma == Decimal(5)

    def test_uses_only_last_period(self) -> None:
        # Older values must be ignored.
        sma = compute_sma(_decimals([100, 100, 1, 2, 3]), period=3)
        assert sma == Decimal(2)


# ---------------------------------------------------------------------------
# SubState transitions
# ---------------------------------------------------------------------------


class TestSubStateEntry:
    def test_no_action_until_window_full(self) -> None:
        sub = SubState(period=3, stop_pct=Decimal("0.07"))
        update_sub_state(sub, _decimals([10, 11, 12]))  # only period samples, no prev
        assert sub.is_long is False

    def test_entry_when_close_above_sma_and_rising(self) -> None:
        sub = SubState(period=3, stop_pct=Decimal("0.07"))
        # closes: [10, 10, 10, 11] -> SMA(3) over [10,10,11] = 10.333..., prev=10, curr=11
        update_sub_state(sub, _decimals([10, 10, 10, 11]))
        assert sub.is_long is True
        assert sub.entry_price == Decimal("11")
        assert sub.peak_price == Decimal("11")

    def test_no_entry_when_close_below_sma(self) -> None:
        sub = SubState(period=3, stop_pct=Decimal("0.07"))
        # SMA(3) over [10,10,8] = 9.333; current=8, below SMA -> no entry
        update_sub_state(sub, _decimals([10, 10, 10, 8]))
        assert sub.is_long is False

    def test_no_entry_when_not_rising(self) -> None:
        sub = SubState(period=3, stop_pct=Decimal("0.07"))
        # SMA(3) over [10, 11, 11] = 10.666..., current=11, prev=11 -> not strictly rising
        update_sub_state(sub, _decimals([10, 10, 11, 11]))
        assert sub.is_long is False

    def test_no_reentry_while_long(self) -> None:
        sub = SubState(period=3, stop_pct=Decimal("0.07"))
        update_sub_state(sub, _decimals([10, 10, 10, 11]))
        assert sub.is_long is True
        original_entry = sub.entry_price
        # Push another rising bar -> should remain long, entry unchanged.
        update_sub_state(sub, _decimals([10, 10, 10, 11, 12]))
        assert sub.is_long is True
        assert sub.entry_price == original_entry  # entry stuck at original


class TestSubStateExit:
    def test_exit_when_close_below_sma(self) -> None:
        sub = SubState(period=3, stop_pct=Decimal("0.20"))  # wide stop so SMA triggers first
        # Enter
        update_sub_state(sub, _decimals([10, 10, 10, 11]))
        assert sub.is_long is True
        # Drop below SMA: closes [10, 10, 11, 8] -> SMA(3) over [10,11,8]=9.666..., curr=8 < SMA
        update_sub_state(sub, _decimals([10, 10, 11, 8]))
        assert sub.is_long is False

    def test_exit_via_trailing_stop(self) -> None:
        sub = SubState(period=3, stop_pct=Decimal("0.05"))
        # Enter at 11 with peak=11; subsequent close at 10.40 = -5.45% from peak -> stop hit.
        # SMA over the window must remain below 10.40 so we exit via trailing not via SMA.
        update_sub_state(sub, _decimals([10, 10, 10, 11]))  # enter long, peak=11
        assert sub.is_long is True
        # Window now: [10, 10, 11, 10.40] -> SMA(3) over [10, 11, 10.40] ~= 10.466
        # current 10.40 < SMA (10.466) -> SMA exit triggers first; widen stop test:
        sub2 = SubState(period=3, stop_pct=Decimal("0.05"))
        update_sub_state(sub2, _decimals([10, 10, 10, 12]))  # enter at 12
        assert sub2.is_long is True
        # Window: [10, 10, 12, 11.30] -> SMA = 11.10; current 11.30 > SMA so no SMA exit.
        # Trailing: peak=12 -> stop = 12 * 0.95 = 11.40 ; current 11.30 < 11.40 -> stop hit.
        update_sub_state(sub2, _decimals([10, 10, 12, Decimal("11.30")]))
        assert sub2.is_long is False

    def test_peak_tracks_upward_only(self) -> None:
        sub = SubState(period=3, stop_pct=Decimal("0.20"))
        update_sub_state(sub, _decimals([10, 10, 10, 11]))
        assert sub.peak_price == Decimal("11")
        # Push higher
        update_sub_state(sub, _decimals([10, 10, 11, 13]))
        assert sub.peak_price == Decimal("13")
        # Pull back but stay above stop and SMA
        update_sub_state(sub, _decimals([10, 11, 13, Decimal("12.50")]))
        assert sub.is_long is True
        assert sub.peak_price == Decimal("13")  # peak does not move down


# ---------------------------------------------------------------------------
# Ensemble target fraction
# ---------------------------------------------------------------------------


class TestTargetFraction:
    def test_both_flat(self) -> None:
        a = SubState(period=20, stop_pct=Decimal("0.07"))
        b = SubState(period=30, stop_pct=Decimal("0.08"))
        assert compute_target_fraction([a, b]) == Decimal(0)

    def test_one_long(self) -> None:
        a = SubState(period=20, stop_pct=Decimal("0.07"), is_long=True)
        b = SubState(period=30, stop_pct=Decimal("0.08"))
        assert compute_target_fraction([a, b]) == Decimal("0.5")

    def test_both_long(self) -> None:
        a = SubState(period=20, stop_pct=Decimal("0.07"), is_long=True)
        b = SubState(period=30, stop_pct=Decimal("0.08"), is_long=True)
        assert compute_target_fraction([a, b]) == Decimal(1)

    def test_empty_returns_zero(self) -> None:
        assert compute_target_fraction([]) == Decimal(0)


# ---------------------------------------------------------------------------
# Rebalance delta
# ---------------------------------------------------------------------------


class TestRebalanceDelta:
    def test_buy_when_under(self) -> None:
        delta = compute_rebalance_delta(
            target_qty=Decimal("1.0"),
            current_qty=Decimal("0.4"),
            size_increment=Decimal("0.001"),
        )
        assert delta == Decimal("0.6")

    def test_sell_when_over(self) -> None:
        delta = compute_rebalance_delta(
            target_qty=Decimal("0.2"),
            current_qty=Decimal("1.0"),
            size_increment=Decimal("0.001"),
        )
        assert delta == Decimal("-0.8")

    def test_no_op_when_in_sync(self) -> None:
        delta = compute_rebalance_delta(
            target_qty=Decimal("0.5"),
            current_qty=Decimal("0.5"),
            size_increment=Decimal("0.001"),
        )
        assert delta == Decimal("0")

    def test_skip_when_below_increment(self) -> None:
        delta = compute_rebalance_delta(
            target_qty=Decimal("0.5005"),
            current_qty=Decimal("0.5"),
            size_increment=Decimal("0.01"),
        )
        assert delta == Decimal("0")

    def test_exact_increment_passes(self) -> None:
        # |delta| == size_increment should NOT be skipped (we use strict less-than).
        delta = compute_rebalance_delta(
            target_qty=Decimal("0.51"),
            current_qty=Decimal("0.50"),
            size_increment=Decimal("0.01"),
        )
        assert delta == Decimal("0.01")
