"""Kronos paper trading runner — Binance Testnet.

Runs KronosActor + KronosStrategy on Binance Spot Testnet.
This is identical code to the backtest; only the node type and venue wiring
change (BacktestEngine → TradingNode, simulated venue → live Binance adapter).

Prerequisites
-------------
1. Install Kronos + deps:
       make install-kronos
2. Set Binance Testnet credentials:
       export BINANCE_TESTNET_API_KEY="..."
       export BINANCE_TESTNET_API_SECRET="..."
3. Clone Kronos repo and set path:
       export KRONOS_REPO_PATH=~/kronos
4. Run:
       cd nautilus && uv run python ../strategies/crypto/kronos/paper_trade.py

Environment variables (all optional)
-------------------------------------
    KRONOS_MODEL_SIZE       mini | small | base    (default: mini)
    KRONOS_SYMBOL           e.g. BTCUSDT           (default: BTCUSDT)
    KRONOS_INTERVAL         1h | 4h | 1d           (default: 1h)
    KRONOS_TRADE_SIZE       float (base units)     (default: 0.001)
    KRONOS_N_SAMPLES        MC samples             (default: 10)
    KRONOS_FORECAST_BARS    forecast horizon       (default: 24)
    KRONOS_INFERENCE_INTERVAL every N bars         (default: 4)

Architecture
------------
    TradingNode (Binance Testnet SPOT)
        ├── KronosActor           ← subscribes to live bars, runs Kronos inference
        └── KronosStrategy        ← subscribes to KronosSignal, submits test orders

Note: Testnet uses real API calls but with paper money. Orders hit the testnet
order book, not live markets. Use BINANCE_TESTNET_API_KEY / _SECRET, not prod keys.
"""

from __future__ import annotations

import os
import sys
from decimal import Decimal
from pathlib import Path

# ---------------------------------------------------------------------------
# Ensure repo root is on sys.path for strategy imports
# ---------------------------------------------------------------------------

_REPO_ROOT = str(Path(__file__).resolve().parents[4])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from nautilus_trader.adapters.binance import (
    BINANCE,
    BinanceDataClientConfig,
    BinanceExecClientConfig,
    BinanceLiveDataClientFactory,
    BinanceLiveExecClientFactory,
)
from nautilus_trader.adapters.binance.common.enums import BinanceAccountType, BinanceEnvironment
from nautilus_trader.config import LoggingConfig
from nautilus_trader.live.config import TradingNodeConfig
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.data import BarSpecification, BarType
from nautilus_trader.model.enums import AggregationSource, BarAggregation, PriceType
from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue

from strategies.crypto.kronos.actor import KronosActor, KronosActorConfig
from strategies.crypto.kronos.strategy import KronosStrategy, KronosStrategyConfig

# ---------------------------------------------------------------------------
# Configuration from environment
# ---------------------------------------------------------------------------

MODEL_SIZE = os.getenv("KRONOS_MODEL_SIZE", "mini")
SYMBOL = os.getenv("KRONOS_SYMBOL", "BTCUSDT")
INTERVAL = os.getenv("KRONOS_INTERVAL", "1h")
TRADE_SIZE = Decimal(os.getenv("KRONOS_TRADE_SIZE", "0.001"))
N_SAMPLES = int(os.getenv("KRONOS_N_SAMPLES", "10"))
FORECAST_BARS = int(os.getenv("KRONOS_FORECAST_BARS", "24"))
INFERENCE_INTERVAL = int(os.getenv("KRONOS_INFERENCE_INTERVAL", "4"))

# Binance interval → NautilusTrader BarAggregation mapping
_NT_AGGREGATION: dict[str, tuple[int, BarAggregation]] = {
    "1m": (1, BarAggregation.MINUTE),
    "5m": (5, BarAggregation.MINUTE),
    "15m": (15, BarAggregation.MINUTE),
    "1h": (1, BarAggregation.HOUR),
    "4h": (4, BarAggregation.HOUR),
    "1d": (1, BarAggregation.DAY),
}

VENUE = Venue("BINANCE")
INSTRUMENT_ID = InstrumentId(Symbol(SYMBOL), VENUE)

step, aggregation = _NT_AGGREGATION.get(INTERVAL, (1, BarAggregation.HOUR))
BAR_TYPE = BarType(
    INSTRUMENT_ID,
    BarSpecification(step, aggregation, PriceType.LAST),
    AggregationSource.EXTERNAL,
)


def _check_env() -> None:
    """Validate required environment variables are present."""
    missing = []
    if not os.getenv("BINANCE_TESTNET_API_KEY"):
        missing.append("BINANCE_TESTNET_API_KEY")
    if not os.getenv("BINANCE_TESTNET_API_SECRET"):
        missing.append("BINANCE_TESTNET_API_SECRET")
    if missing:
        raise EnvironmentError(
            f"Missing required environment variables: {', '.join(missing)}\n"
            "Set Binance Testnet credentials — see https://testnet.binance.vision/"
        )
    if not os.getenv("KRONOS_REPO_PATH"):
        print(
            "WARNING: KRONOS_REPO_PATH not set — actor will run in EMA fallback mode. "
            "Run 'make install-kronos' to install the model."
        )


def run_paper_trade() -> None:
    """Build and start a TradingNode with KronosActor + KronosStrategy on Testnet."""
    _check_env()

    # 1. Node config — Binance Testnet SPOT
    node_config = TradingNodeConfig(
        logging=LoggingConfig(log_level="INFO"),
        data_clients={
            BINANCE: BinanceDataClientConfig(
                account_type=BinanceAccountType.SPOT,
                environment=BinanceEnvironment.TESTNET,
            ),
        },
        exec_clients={
            BINANCE: BinanceExecClientConfig(
                account_type=BinanceAccountType.SPOT,
                environment=BinanceEnvironment.TESTNET,
            ),
        },
    )

    node = TradingNode(config=node_config)
    node.add_data_client_factory(BINANCE, BinanceLiveDataClientFactory)
    node.add_exec_client_factory(BINANCE, BinanceLiveExecClientFactory)

    # 2. Add actor (inference + signal publication)
    actor_config = KronosActorConfig(
        instrument_id=INSTRUMENT_ID,
        bar_type=BAR_TYPE,
        model_size=MODEL_SIZE,
        forecast_horizon=FORECAST_BARS,
        inference_interval_bars=INFERENCE_INTERVAL,
        n_samples=N_SAMPLES,
    )
    actor = KronosActor(config=actor_config)
    node.add_actor(actor)

    # 3. Add strategy (subscribes to KronosSignal, manages orders)
    strategy_config = KronosStrategyConfig(
        instrument_id=INSTRUMENT_ID,
        bar_type=BAR_TYPE,
        trade_size=TRADE_SIZE,
    )
    strategy = KronosStrategy(config=strategy_config)
    node.add_strategy(strategy)

    # 4. Build + run (blocks — runs the event loop)
    node.build()
    node.run()


def main() -> None:
    print("=" * 60)
    print("KRONOS PAPER TRADING — BINANCE TESTNET")
    print(f"  Model  : {MODEL_SIZE}")
    print(f"  Symbol : {SYMBOL} ({INTERVAL})")
    print(f"  Size   : {TRADE_SIZE} {SYMBOL.replace('USDT', '')}")
    print("  Ctrl+C  to stop gracefully")
    print("=" * 60)
    run_paper_trade()


if __name__ == "__main__":
    main()
