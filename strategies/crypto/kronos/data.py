"""KronosSignal — custom Data type published by KronosActor via MessageBus.

Carries the probabilistic OHLCV forecast output from the Kronos foundation
model. The strategy subscribes to this type in on_start() and receives it
via on_data().

Note on inheritance:
    KronosSignal subclasses nautilus_trader.core.data.Data, which is required
    for DataType(...) to accept the class in subscribe_data / publish_data.
    The Data Cython class exposes ts_event and ts_init as read-only properties
    backed by _ts_event and _ts_init — subclasses must use those private names.
    A plain @dataclass cannot be used here because dataclass field assignment
    goes through self.ts_event = ... which is blocked by the Cython property.
"""

from __future__ import annotations

from nautilus_trader.core.data import Data


class KronosSignal(Data):
    """Kronos model prediction signal for a single instrument.

    Attributes
    ----------
    instrument_id : str
        Instrument ID string (e.g. "BTCUSDT.BINANCE").
    direction : float
        Directional signal: +1.0 = bullish, -1.0 = bearish, 0.0 = neutral.
    confidence : float
        Confidence score in [0.0, 1.0].
    predicted_return_pct : float
        Mean predicted return as a fraction (e.g. 0.015 = +1.5%).
    forecast_close : float
        Mean predicted close price at end of forecast horizon.
    forecast_high : float
        Mean predicted high price across the forecast horizon.
    forecast_low : float
        Mean predicted low price across the forecast horizon.
    model_size : str
        Model variant used ("mini", "small", or "base").
    ts_event : int
        Event timestamp in nanoseconds (bar's ts_event).
    ts_init : int
        Init timestamp in nanoseconds (when signal was created).
    """

    def __init__(
        self,
        instrument_id: str,
        direction: float,
        confidence: float,
        predicted_return_pct: float,
        forecast_close: float,
        forecast_high: float,
        forecast_low: float,
        model_size: str,
        ts_event: int,
        ts_init: int,
    ) -> None:
        # ts_event and ts_init are Cython read-only properties on Data
        # backed by _ts_event / _ts_init — must use the private names
        self._ts_event = ts_event
        self._ts_init = ts_init

        self.instrument_id = instrument_id
        self.direction = direction
        self.confidence = confidence
        self.predicted_return_pct = predicted_return_pct
        self.forecast_close = forecast_close
        self.forecast_high = forecast_high
        self.forecast_low = forecast_low
        self.model_size = model_size

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
