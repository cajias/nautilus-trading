"""Kronos foundation model integration for NautilusTrader.

Components
----------
KronosSignal
    Custom data object published by KronosActor via the MessageBus.
KronosActor / KronosActorConfig
    Actor that maintains a rolling OHLCV window, runs Kronos inference,
    and publishes KronosSignal objects every N bars.
KronosStrategy / KronosStrategyConfig
    Strategy that subscribes to KronosSignal and makes trading decisions
    with stop-loss, take-profit, and a peak-drawdown circuit breaker.

Quick start (backtest)
----------------------
See strategies/crypto/kronos/backtest.py for a complete runnable example.

Model selection
---------------
Set model_size="mini" (default) or model_size="base" in KronosActorConfig.
Or override the HuggingFace repo ID:
    KronosActorConfig(huggingface_repo_id="NeoQuasar/Kronos-mini")

Environment variable alternative:
    export KRONOS_MODEL_SIZE=mini   # read in backtest.py
"""

from strategies.crypto.kronos.actor import KronosActor, KronosActorConfig
from strategies.crypto.kronos.signal import KronosSignal
from strategies.crypto.kronos.strategy import KronosStrategy, KronosStrategyConfig

__all__ = [
    "KronosSignal",
    "KronosActor",
    "KronosActorConfig",
    "KronosStrategy",
    "KronosStrategyConfig",
]
