"""Tests for strategies.crypto._grid_math pure helpers."""

from __future__ import annotations

from decimal import Decimal

import pytest

# ---- compute_uniform_grid_levels -----------------------------------------


def test_uniform_grid_levels_boundary_inclusion():
    from strategies.crypto._grid_math import compute_uniform_grid_levels

    levels = compute_uniform_grid_levels(
        lower=Decimal("40000"),
        upper=Decimal("50000"),
        n_levels=5,
    )
    assert len(levels) == 5
    assert levels[0] == Decimal("40000")
    assert levels[-1] == Decimal("50000")


def test_uniform_grid_levels_even_spacing():
    from strategies.crypto._grid_math import compute_uniform_grid_levels

    levels = compute_uniform_grid_levels(
        lower=Decimal("100"),
        upper=Decimal("200"),
        n_levels=5,
    )
    step = levels[1] - levels[0]
    for i in range(1, len(levels) - 1):
        assert levels[i + 1] - levels[i] == step


def test_uniform_grid_levels_two_levels():
    from strategies.crypto._grid_math import compute_uniform_grid_levels

    assert compute_uniform_grid_levels(
        lower=Decimal("10"),
        upper=Decimal("20"),
        n_levels=2,
    ) == [Decimal("10"), Decimal("20")]


# ---- compute_atr_adjusted_step -------------------------------------------


def test_atr_adjusted_step_zero_atr_equals_base():
    """atr=0 → step == base_step (no adjustment)."""
    from strategies.crypto._grid_math import compute_atr_adjusted_step

    step = compute_atr_adjusted_step(
        total_range=Decimal("1000"),
        atr_value=Decimal("0"),
        n_levels=11,
    )
    base = Decimal("1000") / Decimal("10")  # = 100
    assert step == base


def test_atr_adjusted_step_positive_atr_widens():
    """atr > 0 → adjusted step strictly larger than base."""
    from strategies.crypto._grid_math import compute_atr_adjusted_step

    step = compute_atr_adjusted_step(
        total_range=Decimal("1000"),
        atr_value=Decimal("100"),
        n_levels=11,
    )
    base = Decimal("1000") / Decimal("10")
    assert step > base


# ---- compute_calibration_coverage ----------------------------------------


def test_calibration_coverage_exact_match():
    from strategies.crypto._grid_math import compute_calibration_coverage

    assert compute_calibration_coverage(
        quantile_range=10.0,
        recent_range=10.0,
    ) == pytest.approx(1.0, rel=1e-9)


def test_calibration_coverage_partial():
    from strategies.crypto._grid_math import compute_calibration_coverage

    assert compute_calibration_coverage(
        quantile_range=7.5,
        recent_range=10.0,
    ) == pytest.approx(0.75, rel=1e-9)


# ---- compute_kelly_size --------------------------------------------------


def test_kelly_size_zero_when_no_edge():
    """p10 >= p90 → no edge → 0."""
    from strategies.crypto._grid_math import compute_kelly_size

    assert (
        compute_kelly_size(
            p10=100.0,
            p90=100.0,
            current_price=100.0,
            kelly_fraction=0.5,
            total_capital=500.0,
            grid_levels=10,
        )
        == 0.0
    )


def test_kelly_size_zero_when_invalid_price():
    from strategies.crypto._grid_math import compute_kelly_size

    assert (
        compute_kelly_size(
            p10=90.0,
            p90=110.0,
            current_price=0.0,
            kelly_fraction=0.5,
            total_capital=500.0,
            grid_levels=10,
        )
        == 0.0
    )


def test_kelly_size_capped_at_per_level_max():
    """Output never exceeds total_capital / grid_levels."""
    from strategies.crypto._grid_math import compute_kelly_size

    out = compute_kelly_size(
        p10=10.0,
        p90=1000.0,
        current_price=100.0,  # huge edge
        kelly_fraction=1.0,
        total_capital=500.0,
        grid_levels=10,
    )
    assert out <= 500.0 / 10


def test_kelly_size_known_value():
    """Sanity check arithmetic: spread/current * kelly_fraction * total_capital."""
    from strategies.crypto._grid_math import compute_kelly_size

    # spread = 10, current = 100 → edge = 0.1; kelly_raw = 0.1 * 0.5 = 0.05
    # raw_dollar = 0.05 * 1000 = 50; per_level_max = 1000/10 = 100 → return 50
    assert compute_kelly_size(
        p10=95.0,
        p90=105.0,
        current_price=100.0,
        kelly_fraction=0.5,
        total_capital=1000.0,
        grid_levels=10,
    ) == pytest.approx(50.0, rel=1e-6)
