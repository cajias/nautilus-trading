"""RVS (Relative Volume Sentiment) signal data type.

Encapsulates the multi-source sentiment signal used by the RVS Swing strategy:
- Volume z-score (vs rolling baseline)
- Sentiment polarity (0-1 scale)
- Engagement ratio (vs baseline)
- Whale wallet concentration change (%)
- Source platform (reddit, twitter)
- TimesFM forecast delta (%)
"""

from dataclasses import dataclass


@dataclass
class RVSSignal:
    """Custom data type for an RVS sentiment signal.

    Attributes:
        volume_zscore: Standard deviations above volume baseline.
        polarity: Sentiment polarity score (0.0 to 1.0).
        engagement_ratio: Engagement relative to baseline (1.0 = normal).
        whale_concentration_change: % change in top-10 wallet concentration (24h).
        source: Signal source platform ("reddit" or "twitter").
        forecast_delta_pct: TimesFM P50 forecast delta as fraction (e.g., 0.02 = +2%).
    """

    volume_zscore: float
    polarity: float
    engagement_ratio: float
    whale_concentration_change: float
    source: str
    forecast_delta_pct: float
