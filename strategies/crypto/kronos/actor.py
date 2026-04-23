"""KronosActor — NautilusTrader Actor that runs Kronos inference and publishes signals.

Architecture:
    KronosActor subscribes to OHLCV bars, maintains a rolling window, runs the
    Kronos model every N bars, and publishes KronosSignal objects via the
    NautilusTrader MessageBus. KronosStrategy subscribes to these signals.

Kronos model API (https://github.com/shiyu-coder/Kronos):
    Kronos is not a pip-installable package. Installation requires:
        git clone https://github.com/shiyu-coder/Kronos.git /path/to/Kronos
        pip install -r /path/to/Kronos/requirements.txt
        export KRONOS_REPO_PATH=/path/to/Kronos   # or pass via actor config

    The `model/` subdirectory of the repo is added to sys.path. Then:
        from model import Kronos, KronosTokenizer, KronosPredictor

    Model zoo (all on HuggingFace NeoQuasar namespace):
        mini  : 4.1M params, 2048-token context,  tokenizer "NeoQuasar/Kronos-Tokenizer-2k"
        small : 24.7M params, 512-token context,  tokenizer "NeoQuasar/Kronos-Tokenizer-base"
        base  : 102.3M params, 512-token context, tokenizer "NeoQuasar/Kronos-Tokenizer-base"

    Predict API:
        predictor = KronosPredictor(model, tokenizer, max_context=<ctx_len>)
        pred_df = predictor.predict(
            df=x_df,            # DataFrame: columns ['open','high','low','close'] required;
                                #            'volume','amount' optional
            x_timestamp=x_ts,  # pd.Series of timestamps for historical bars
            y_timestamp=y_ts,  # pd.Series of future timestamps (one per forecast bar)
            pred_len=pred_len,  # number of bars to forecast
            T=1.0,              # temperature (higher = more random)
            top_p=0.9,          # nucleus sampling probability
            sample_count=1,     # number of MC paths to generate and average
        )
        # Returns pd.DataFrame with columns ['open','high','low','close','volume','amount']
        # indexed by y_timestamp
"""

from __future__ import annotations

import os
import sys
from collections import deque

import pandas as pd
from nautilus_trader.common.actor import Actor
from nautilus_trader.config import ActorConfig
from nautilus_trader.model.data import Bar, BarType, DataType
from nautilus_trader.model.identifiers import InstrumentId

from strategies.crypto.kronos.data import KronosSignal

# ---------------------------------------------------------------------------
# Module-level availability flag
# ---------------------------------------------------------------------------

# Kronos is not a pip package — availability is determined at runtime by
# _try_import_kronos(). This module-level flag defaults to False and is used
# as a patchable sentinel in tests (mirrors the TIMESFM_AVAILABLE pattern).
KRONOS_AVAILABLE: bool = False

# ---------------------------------------------------------------------------
# Model registry
# ---------------------------------------------------------------------------

# HuggingFace model IDs and matching tokenizers per size variant
_MODEL_REGISTRY: dict[str, dict[str, str | int]] = {
    "mini": {
        "model_id": "NeoQuasar/Kronos-mini",
        "tokenizer_id": "NeoQuasar/Kronos-Tokenizer-2k",
        "max_context": 2048,
    },
    "small": {
        "model_id": "NeoQuasar/Kronos-small",
        "tokenizer_id": "NeoQuasar/Kronos-Tokenizer-base",
        "max_context": 512,
    },
    "base": {
        "model_id": "NeoQuasar/Kronos-base",
        "tokenizer_id": "NeoQuasar/Kronos-Tokenizer-base",
        "max_context": 512,
    },
}


def build_kronos_signal(
    pred_df: pd.DataFrame,
    current_close: float,
    instrument_id: str,
    model_size: str,
    bar_ts_event: int,
    bar_ts_init: int,
) -> KronosSignal | None:
    """Derive a KronosSignal from a Kronos forecast DataFrame.

    This is a module-level pure function so it can be unit-tested without a
    live Actor instance (Actor.log is a read-only Cython property).

    Parameters
    ----------
    pred_df : pd.DataFrame
        Output of KronosPredictor.predict() — must have 'close', 'high', 'low' columns.
    current_close : float
        Current close price used to compute predicted return.
    instrument_id : str
        Instrument ID string (e.g. "BTCUSDT.BINANCE").
    model_size : str
        Model variant label stored on the signal.
    bar_ts_event, bar_ts_init : int
        Nanosecond timestamps from the source bar.

    Returns
    -------
    KronosSignal or None if pred_df is empty / None or parsing fails.
    """
    if pred_df is None or pred_df.empty:
        return None

    try:
        forecast_close = float(pred_df["close"].iloc[-1])
        forecast_high = float(pred_df["high"].max())
        forecast_low = float(pred_df["low"].min())

        predicted_return_pct = (
            (forecast_close - current_close) / current_close if current_close > 0 else 0.0
        )

        # Confidence: magnitude of predicted move relative to forecast price range.
        # Wider range (higher uncertainty) → lower confidence.
        predicted_range = forecast_high - forecast_low
        if predicted_range > 0 and current_close > 0:
            move_magnitude = abs(forecast_close - current_close)
            confidence = float(min(1.0, move_magnitude / (predicted_range * 0.5)))
        else:
            confidence = float(min(1.0, abs(predicted_return_pct) * 20))

        direction = 1.0 if predicted_return_pct > 0 else (-1.0 if predicted_return_pct < 0 else 0.0)

        return KronosSignal(
            instrument_id=instrument_id,
            direction=direction,
            confidence=confidence,
            predicted_return_pct=predicted_return_pct,
            forecast_close=forecast_close,
            forecast_high=forecast_high,
            forecast_low=forecast_low,
            model_size=model_size,
            ts_event=bar_ts_event,
            ts_init=bar_ts_init,
        )
    except Exception:
        return None


def _try_import_kronos(repo_path: str | None) -> bool:
    """Add Kronos model directory to sys.path and test the import.

    Returns True if import succeeds, False otherwise.
    The Kronos repo must be cloned locally — it is not a PyPI package.
    Set KRONOS_REPO_PATH env var or pass repo_path to KronosActorConfig.
    """
    search_paths = []
    if repo_path:
        search_paths.append(repo_path)
    env_path = os.environ.get("KRONOS_REPO_PATH")
    if env_path:
        search_paths.append(env_path)

    for base in search_paths:
        model_dir = os.path.join(base, "model")
        if os.path.isdir(model_dir) and model_dir not in sys.path:
            sys.path.insert(0, model_dir)
            sys.path.insert(0, base)

    try:
        import importlib

        importlib.import_module("model")
        return True
    except ImportError:
        return False


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


class KronosActorConfig(ActorConfig, frozen=True):
    """Configuration for KronosActor.

    Parameters
    ----------
    instrument_id : InstrumentId
        Instrument to monitor and publish signals for.
    bar_type : BarType
        Bar type to subscribe to (must match KronosStrategy's bar_type).
    model_size : str
        Kronos model variant: "mini" (4.1M), "small" (24.7M), or "base" (102.3M).
    kronos_repo_path : str | None
        Path to the locally cloned Kronos repo directory. Falls back to
        the KRONOS_REPO_PATH environment variable.
    forecast_horizon : int
        Number of bars ahead to forecast (pred_len).
    inference_interval_bars : int
        Run inference every N bars (throttles CPU/GPU usage).
    n_samples : int
        Number of MC paths to generate and average (sample_count). Higher =
        smoother mean forecast, slower inference.
    temperature : float
        Sampling temperature (T). Lower = more deterministic, higher = more
        diverse. Default 1.0 (neutral sampling).
    top_p : float
        Nucleus sampling probability. Default 0.9 (standard).
    huggingface_model_id : str | None
        Override the model HuggingFace repo ID (e.g. for a fine-tuned variant).
    huggingface_tokenizer_id : str | None
        Override the tokenizer HuggingFace repo ID.
    """

    instrument_id: InstrumentId
    bar_type: BarType
    model_size: str = "mini"
    kronos_repo_path: str | None = None
    forecast_horizon: int = 24
    inference_interval_bars: int = 4
    n_samples: int = 1
    temperature: float = 1.0
    top_p: float = 0.9
    huggingface_model_id: str | None = None
    huggingface_tokenizer_id: str | None = None


# ---------------------------------------------------------------------------
# Actor
# ---------------------------------------------------------------------------


class KronosActor(Actor):
    """Actor that maintains an OHLCV buffer, runs Kronos inference, and publishes signals.

    The actor produces KronosSignal objects on the MessageBus. Any strategy
    that subscribes to DataType(KronosSignal, metadata={"instrument_id": ...})
    will receive them via its on_data() handler.
    """

    def __init__(self, config: KronosActorConfig) -> None:
        super().__init__(config)

        registry = _MODEL_REGISTRY.get(config.model_size, _MODEL_REGISTRY["mini"])
        self._max_context: int = int(registry["max_context"])

        # Rolling OHLCV buffer — also stores bar timestamps for Kronos input
        self._ohlcv_buffer: deque[dict] = deque(maxlen=self._max_context)
        self._bars_since_inference: int = 0

        self._predictor = None
        self._model_available: bool = False
        self._signal_count: int = 0

    # -- Lifecycle ---------------------------------------------------------------

    def on_start(self) -> None:
        self._load_model()
        self.subscribe_bars(self.config.bar_type)
        self.log.info(
            f"KronosActor started: model={self.config.model_size} "
            f"max_context={self._max_context} "
            f"horizon={self.config.forecast_horizon} "
            f"interval={self.config.inference_interval_bars} "
            f"model_available={self._model_available}"
        )

    def on_bar(self, bar: Bar) -> None:
        self._update_buffer(bar)
        self._bars_since_inference += 1

        if not self._model_available:
            return

        if len(self._ohlcv_buffer) < min(self._max_context, 50):
            return  # still warming up (need at least 50 bars for stable forecasts)

        if self._bars_since_inference >= self.config.inference_interval_bars:
            self._bars_since_inference = 0
            signal = self._run_inference(bar)
            if signal is not None:
                data_type = DataType(
                    KronosSignal,
                    metadata={"instrument_id": str(self.config.instrument_id)},
                )
                self.publish_data(data_type, signal)
                self._signal_count += 1
                self.log.debug(f"Published signal #{self._signal_count}: {signal}")

    def on_stop(self) -> None:
        self.unsubscribe_bars(self.config.bar_type)

    def on_reset(self) -> None:
        self._ohlcv_buffer.clear()
        self._bars_since_inference = 0
        self._signal_count = 0

    # -- Model loading -----------------------------------------------------------

    def _load_model(self) -> None:
        """Load Kronos tokenizer + model + predictor. Falls back gracefully."""
        kronos_available = _try_import_kronos(self.config.kronos_repo_path)
        if not kronos_available:
            self.log.warning(
                "Kronos repo not found — actor will not publish signals. "
                "Clone the repo and set KRONOS_REPO_PATH: "
                "  git clone https://github.com/shiyu-coder/Kronos.git /path/to/Kronos "
                "  pip install -r /path/to/Kronos/requirements.txt "
                "  export KRONOS_REPO_PATH=/path/to/Kronos"
            )
            return

        registry = _MODEL_REGISTRY.get(self.config.model_size, _MODEL_REGISTRY["mini"])
        model_id = self.config.huggingface_model_id or str(registry["model_id"])
        tokenizer_id = self.config.huggingface_tokenizer_id or str(registry["tokenizer_id"])
        max_context = self._max_context

        try:
            from model import Kronos, KronosPredictor, KronosTokenizer  # type: ignore[import]

            self.log.info(f"Loading Kronos tokenizer from {tokenizer_id} ...")
            tokenizer = KronosTokenizer.from_pretrained(tokenizer_id)

            self.log.info(f"Loading Kronos model from {model_id} ...")
            model = Kronos.from_pretrained(model_id)

            self._predictor = KronosPredictor(model, tokenizer, max_context=max_context)
            self._model_available = True
            self.log.info(f"Kronos predictor ready: {model_id}")
        except Exception as e:
            self.log.warning(f"Failed to load Kronos model ({model_id}): {e}")
            self._predictor = None
            self._model_available = False

    # -- Buffer management -------------------------------------------------------

    def _update_buffer(self, bar: Bar) -> None:
        """Append the latest bar's OHLCV + timestamp to the rolling buffer."""
        self._ohlcv_buffer.append(
            {
                "open": float(bar.open),
                "high": float(bar.high),
                "low": float(bar.low),
                "close": float(bar.close),
                "volume": float(bar.volume),
                "ts_ns": bar.ts_event,  # nanoseconds — used to build x_timestamp
            }
        )

    def _build_context(self) -> tuple[pd.DataFrame, pd.Series]:
        """Build (x_df, x_timestamp) for the Kronos predictor input.

        Returns
        -------
        x_df : pd.DataFrame
            OHLCV columns ['open', 'high', 'low', 'close', 'volume'].
        x_timestamp : pd.Series
            UTC timestamps for each row in x_df.
        """
        rows = list(self._ohlcv_buffer)
        df = pd.DataFrame(rows)
        x_timestamp = pd.to_datetime(df.pop("ts_ns"), unit="ns", utc=True)
        return df, x_timestamp

    def _infer_bar_freq(self, x_timestamp: pd.Series) -> pd.Timedelta:
        """Infer bar frequency from the last two timestamps in the context."""
        if len(x_timestamp) >= 2:
            delta = x_timestamp.iloc[-1] - x_timestamp.iloc[-2]
            if delta.total_seconds() > 0:
                return delta
        return pd.Timedelta(hours=1)  # safe default

    def _build_y_timestamp(self, x_timestamp: pd.Series, freq: pd.Timedelta) -> pd.Series:
        """Build future timestamps for the forecast horizon."""
        last_ts = x_timestamp.iloc[-1]
        future = pd.date_range(
            start=last_ts + freq,
            periods=self.config.forecast_horizon,
            freq=freq,
            tz="UTC",
        )
        return pd.Series(future)

    # -- Inference ---------------------------------------------------------------

    def _run_inference(self, bar: Bar) -> KronosSignal | None:
        """Run Kronos predictor and return a KronosSignal, or None on failure."""
        x_df, x_timestamp = self._build_context()
        freq = self._infer_bar_freq(x_timestamp)
        y_timestamp = self._build_y_timestamp(x_timestamp, freq)
        current_close = float(bar.close)

        try:
            pred_df: pd.DataFrame = self._predictor.predict(
                df=x_df,
                x_timestamp=x_timestamp,
                y_timestamp=y_timestamp,
                pred_len=self.config.forecast_horizon,
                T=self.config.temperature,
                top_p=self.config.top_p,
                sample_count=self.config.n_samples,
            )
        except Exception as e:
            self.log.warning(f"Kronos inference failed: {e}")
            return None

        signal = build_kronos_signal(
            pred_df=pred_df,
            current_close=current_close,
            instrument_id=str(self.config.instrument_id),
            model_size=self.config.model_size,
            bar_ts_event=bar.ts_event,
            bar_ts_init=bar.ts_init,
        )
        if signal is None:
            self.log.warning("build_kronos_signal returned None for non-empty forecast")
        return signal
