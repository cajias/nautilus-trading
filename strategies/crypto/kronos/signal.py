"""KronosSignal — custom data type published by KronosActor via MessageBus.

Carries the probabilistic OHLCV forecast output from the Kronos foundation
model. The strategy subscribes to this type in on_start() and receives it
via on_data().
"""

from dataclasses import dataclass


@dataclass
class KronosSignal:
    """Kronos model prediction signal for a single instrument.

    Attributes
    ----------
    instrument_id : str
        Instrument ID string (e.g. "BTCUSDT.BINANCE").
    direction : float
        Directional signal: +1.0 = bullish, -1.0 = bearish, 0.0 = neutral.
    confidence : float
        Confidence score in [0.0, 1.0]. Derived from MC sample spread:
        tighter spread → higher confidence.
    predicted_return_pct : float
        Mean predicted return as a fraction (e.g. 0.015 = +1.5%).
        Positive = expected price increase, negative = decrease.
    forecast_close : float
        Mean predicted close price at the end of the forecast horizon.
    forecast_high : float
        Mean predicted high price across the forecast horizon.
    forecast_low : float
        Mean predicted low price across the forecast horizon.
    model_size : str
        Model variant used ("mini" or "base").
    ts_event : int
        Event timestamp in nanoseconds (bar's ts_event).
    ts_init : int
        Init timestamp in nanoseconds (when signal was created).
    """

    instrument_id: str
    direction: float
    confidence: float
    predicted_return_pct: float
    forecast_close: float
    forecast_high: float
    forecast_low: float
    model_size: str
    ts_event: int
    ts_init: int

    def is_bullish(self) -> bool:
        return self.direction > 0

    def is_bearish(self) -> bool:
        return self.direction < 0

    def __repr__(self) -> str:
        sign = "↑" if self.is_bullish() else "↓" if self.is_bearish() else "→"
        return (
            f"KronosSignal({self.instrument_id} {sign} "
            f"ret={self.predicted_return_pct:+.3f} conf={self.confidence:.2f} "
            f"close={self.forecast_close:.4f})"
        )
