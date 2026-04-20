"""Runner script that composes kronos/backtest_config.py builders into a BacktestEngine invocation."""

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

from nautilus_trading.backtest.runner_base import BacktestRunner
from strategies.crypto.kronos._fetch_binance import fetch_bars_from_binance
from strategies.crypto.kronos.actor import KronosActor, KronosActorConfig
from strategies.crypto.kronos.backtest_config import (
    build_bar_type,
    build_engine_config,
    build_instrument,
    build_venue_spec,
)
from strategies.crypto.kronos.strategy import KronosStrategy, KronosStrategyConfig


class KronosBacktestRunner(BacktestRunner):
    """Kronos integration backtest — builds engine/venue/data via kronos/backtest_config.py builders."""

    def __init__(
        self,
        *,
        symbol: str,
        interval: str,
        start: str,
        end: str,
        initial_capital: Decimal,
        trade_size: Decimal,
        model_size: str,
        forecast_bars: int,
        n_samples: int,
        inference_interval: int,
    ) -> None:
        self.symbol = symbol
        self.interval = interval
        self.start = start
        self.end = end
        self.initial_capital = initial_capital
        self.trade_size = trade_size
        self.model_size = model_size
        self.forecast_bars = forecast_bars
        self.n_samples = n_samples
        self.inference_interval = inference_interval

    def build_config(self) -> dict:
        return {
            "engine_cfg": build_engine_config(log_level="ERROR"),
            "venue": build_venue_spec(initial_capital=self.initial_capital),
            "instrument": build_instrument(symbol=self.symbol),
        }

    def add_data(self, engine: BacktestEngine, config: dict) -> None:
        instrument = config["instrument"]
        bar_type = build_bar_type(instrument, interval=self.interval)
        engine.add_instrument(instrument)
        bars = fetch_bars_from_binance(
            symbol=self.symbol,
            interval=self.interval,
            start=self.start,
            end=self.end,
            bar_type=bar_type,
            price_precision=instrument.price_precision,
            size_precision=instrument.size_precision,
        )
        engine.add_data(bars)
        engine.add_actor(
            KronosActor(
                KronosActorConfig(
                    instrument_id=instrument.id,
                    bar_type=bar_type,
                    model_size=self.model_size,
                    forecast_horizon=self.forecast_bars,
                    n_samples=self.n_samples,
                    inference_interval_bars=self.inference_interval,
                )
            )
        )
        engine.add_strategy(
            KronosStrategy(
                KronosStrategyConfig(
                    instrument_id=instrument.id,
                    bar_type=bar_type,
                    trade_size=self.trade_size,
                )
            )
        )

    def run(self, engine: BacktestEngine) -> BacktestEngine:
        engine.run()
        return engine

    def print_results(self, results: BacktestEngine) -> None:
        # Kronos runner doesn't currently produce structured reports; dispose is enough.
        results.dispose()

    def main(self) -> None:
        """Override the base main() to handle Nautilus venue/Money wiring.

        The base runner_base.BacktestRunner.main() uses ``add_venue(**venue.__dict__)``
        which doesn't work for BacktestVenueConfig (msgspec Struct) and can't
        express Venue(spec.name) / Money.from_str(...) wrapping. This override
        preserves the exact pre-migration wiring.
        """
        config = self.build_config()
        engine = BacktestEngine(config=config["engine_cfg"])
        spec = config["venue"]
        engine.add_venue(
            venue=Venue(spec.name),
            oms_type=spec.oms_type,
            account_type=spec.account_type,
            base_currency=spec.base_currency,
            starting_balances=[Money.from_str(b) for b in spec.starting_balances],
        )
        self.add_data(engine, config)
        try:
            results = self.run(engine)
            self.print_results(results)
        finally:
            engine.dispose()


def main() -> None:
    KronosBacktestRunner(
        symbol=os.getenv("KRONOS_SYMBOL", "BTCUSDT"),
        interval=os.getenv("KRONOS_INTERVAL", "1h"),
        start=os.getenv("KRONOS_START", "2024-01-01"),
        end=os.getenv("KRONOS_END", "2024-12-31"),
        initial_capital=Decimal(os.getenv("KRONOS_INITIAL_CAPITAL", "500")),
        trade_size=Decimal(os.getenv("KRONOS_TRADE_SIZE", "0.001")),
        model_size=os.getenv("KRONOS_MODEL_SIZE", "mini"),
        forecast_bars=int(os.getenv("KRONOS_FORECAST_BARS", "24")),
        n_samples=int(os.getenv("KRONOS_N_SAMPLES", "50")),
        inference_interval=int(os.getenv("KRONOS_INFERENCE_INTERVAL", "4")),
    ).main()


if __name__ == "__main__":
    main()
