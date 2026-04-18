"""Kronos backtest runner — thin composition of kronos/backtest_config.py builders."""

from __future__ import annotations

import os
import sys
from decimal import Decimal
from pathlib import Path

_REPO_ROOT = str(Path(__file__).resolve().parents[3])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.objects import Money

from strategies.crypto.kronos._fetch_binance import fetch_bars_from_binance
from strategies.crypto.kronos.actor import KronosActor, KronosActorConfig
from strategies.crypto.kronos.backtest_config import (
    build_bar_type,
    build_engine_config,
    build_instrument,
    build_venue_spec,
)
from strategies.crypto.kronos.strategy import KronosStrategy, KronosStrategyConfig


def main() -> None:
    symbol = os.getenv("KRONOS_SYMBOL", "BTCUSDT")
    interval = os.getenv("KRONOS_INTERVAL", "1h")
    start = os.getenv("KRONOS_START", "2024-01-01")
    end = os.getenv("KRONOS_END", "2024-12-31")
    initial_capital = Decimal(os.getenv("KRONOS_INITIAL_CAPITAL", "500"))
    trade_size = Decimal(os.getenv("KRONOS_TRADE_SIZE", "0.001"))

    engine = BacktestEngine(config=build_engine_config(log_level="ERROR"))
    spec = build_venue_spec(initial_capital=initial_capital)
    engine.add_venue(
        venue=Venue(spec.name),
        oms_type=spec.oms_type,
        account_type=spec.account_type,
        base_currency=spec.base_currency,
        starting_balances=[Money.from_str(b) for b in spec.starting_balances],
    )
    instrument = build_instrument(symbol=symbol)
    engine.add_instrument(instrument)
    bar_type = build_bar_type(instrument, interval=interval)
    bars = fetch_bars_from_binance(
        symbol=symbol,
        interval=interval,
        start=start,
        end=end,
        bar_type=bar_type,
        price_precision=instrument.price_precision,
        size_precision=instrument.size_precision,
    )
    engine.add_data(bars)
    actor = KronosActor(KronosActorConfig(
        instrument_id=instrument.id,
        bar_type=bar_type,
        model_size=os.getenv("KRONOS_MODEL_SIZE", "mini"),
        forecast_horizon=int(os.getenv("KRONOS_FORECAST_BARS", "24")),
        n_samples=int(os.getenv("KRONOS_N_SAMPLES", "50")),
        inference_interval_bars=int(os.getenv("KRONOS_INFERENCE_INTERVAL", "4")),
    ))
    engine.add_actor(actor)
    engine.add_strategy(KronosStrategy(KronosStrategyConfig(
        instrument_id=instrument.id,
        bar_type=bar_type,
        trade_size=trade_size,
    )))
    engine.run()
    engine.dispose()


if __name__ == "__main__":
    main()
