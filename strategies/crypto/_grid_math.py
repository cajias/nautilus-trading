"""Pure grid-computation helpers lifted from timesfm_grid.py.

No state, no I/O, no indicator access. All functions are side-effect-free and
depend only on their arguments. Consumed by:
  - strategies.crypto.timesfm_grid (primary — Task 7.3 will rewire)
  - strategies.crypto.grid_bot     (Task 7.4 will dedupe the uniform-grid math)

Callers are responsible for downstream price-shape concerns:
  - Tick-snapping: callers call instrument.make_price() or round() themselves
  - Precision: callers pass precision or apply it post-hoc
"""

from __future__ import annotations

from decimal import Decimal


def compute_uniform_grid_levels(
    *,
    lower: Decimal,
    upper: Decimal,
    n_levels: int,
) -> list[Decimal]:
    """Return n_levels evenly-spaced Decimal prices from lower to upper (inclusive).

    n_levels must be >= 2. Returns raw Decimals — callers apply tick-snapping.
    Extracted from timesfm_grid._calculate_grid lines 261-265 and
    grid_bot._calculate_grid lines 77-82 (same math, different snap discipline).
    """
    step = (upper - lower) / (n_levels - 1)
    return [lower + step * i for i in range(n_levels)]


def compute_atr_adjusted_step(
    *,
    total_range: Decimal,
    atr_value: Decimal,
    n_levels: int,
) -> Decimal:
    """Return the ATR-adjusted inter-level step size.

    adjusted_step = base_step * (1 + (atr_value / total_range) * 0.5)
    where base_step = total_range / (n_levels - 1).

    Caller must guard against total_range <= 0 and atr_value <= 0 before calling
    (those produce nonsense output). Extracted from
    timesfm_grid._recalculate_grid_with_atr lines 287-290.
    """
    base_step = total_range / (n_levels - 1)
    adjusted_step = base_step * (Decimal("1") + (atr_value / total_range) * Decimal("0.5"))
    return adjusted_step


def compute_calibration_coverage(
    *,
    quantile_range: float,
    recent_range: float,
) -> float:
    """Return coverage ratio = quantile_range / recent_range.

    Caller must guard against recent_range <= 0 before calling.
    The bool gate (coverage >= min_coverage) stays in the caller — this helper
    returns only the ratio. Extracted from timesfm_grid._check_calibration
    lines 327-328.
    """
    return quantile_range / recent_range


def compute_kelly_size(
    *,
    p10: float,
    p90: float,
    current_price: float,
    kelly_fraction: float,
    total_capital: float,
    grid_levels: int,
) -> float:
    """Half-Kelly dollar amount per grid level.

    Returns 0.0 if p10 >= p90 or current_price <= 0 (no edge / invalid price).
    Output is capped at total_capital / grid_levels per level.

    Extracted from timesfm_grid.compute_kelly_size (method at line 442) —
    converted self.config.* references to explicit args.
    """
    if p10 >= p90 or current_price <= 0:
        return 0.0

    spread = p90 - p10
    edge = spread / current_price  # Expected range as fraction
    # Simplified Kelly: fraction of capital = edge * kelly_cap
    kelly_raw = edge * kelly_fraction
    max_per_level = float(total_capital) / grid_levels
    return min(kelly_raw * float(total_capital), max_per_level)
