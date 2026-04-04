"""BacktestNode configuration and execution."""

from __future__ import annotations

from typing import Any

from nautilus_trader.backtest.node import (
    BacktestDataConfig,
    BacktestEngineConfig,
    BacktestNode,
    BacktestRunConfig,
    BacktestVenueConfig,
)
from nautilus_trader.config import ImportableStrategyConfig, LoggingConfig
from nautilus_trader.model import QuoteTick
from nautilus_trader.persistence.catalog import ParquetDataCatalog


def build_backtest_config(
    catalog: ParquetDataCatalog,
    *,
    strategy_path: str = "strategies.forex.ema_cross:EMACrossStrategy",
    config_path: str = "strategies.forex.ema_cross:EMACrossConfig",
    instrument_index: int = 0,
    bar_interval: str = "1-MINUTE-MID-INTERNAL",
    trade_size: str = "100000",
    fast_ema_period: int = 10,
    slow_ema_period: int = 20,
    venue_name: str = "SIM",
    oms_type: str = "NETTING",
    account_type: str = "MARGIN",
    base_currency: str = "USD",
    starting_balance: str = "1_000_000 USD",
    end_time: str | None = "2020-01-10",
    log_level: str = "INFO",
    strategy_config_overrides: dict[str, Any] | None = None,
) -> BacktestRunConfig:
    """Build a complete BacktestRunConfig from high-level parameters.

    The strategy config receives instrument_id, bar_type, trade_size,
    fast_ema_period, and slow_ema_period by default. Pass
    ``strategy_config_overrides`` to supply or override arbitrary keys.
    """
    instruments = catalog.instruments()
    if not instruments:
        raise RuntimeError(f"No instruments found in catalog at {catalog.path}")

    instrument = instruments[instrument_index]
    instrument_id = str(instrument.id)
    bar_type = f"{instrument.id}-{bar_interval}"

    strat_config: dict[str, Any] = {
        "instrument_id": instrument_id,
        "bar_type": bar_type,
        "trade_size": trade_size,
        "fast_ema_period": fast_ema_period,
        "slow_ema_period": slow_ema_period,
    }
    if strategy_config_overrides:
        strat_config.update(strategy_config_overrides)

    data_kwargs: dict[str, Any] = {
        "catalog_path": str(catalog.path),
        "data_cls": QuoteTick,
        "instrument_id": instrument.id,
    }
    if end_time is not None:
        data_kwargs["end_time"] = end_time

    return BacktestRunConfig(
        engine=BacktestEngineConfig(
            strategies=[
                ImportableStrategyConfig(
                    strategy_path=strategy_path,
                    config_path=config_path,
                    config=strat_config,
                ),
            ],
            logging=LoggingConfig(log_level=log_level),
        ),
        venues=[
            BacktestVenueConfig(
                name=venue_name,
                oms_type=oms_type,
                account_type=account_type,
                base_currency=base_currency,
                starting_balances=[starting_balance],
            ),
        ],
        data=[BacktestDataConfig(**data_kwargs)],
    )


def run_backtest(
    config: BacktestRunConfig,
) -> list:
    """Execute a backtest and return the results list."""
    node = BacktestNode(configs=[config])
    return node.run()


def print_results(results: list) -> None:
    """Pretty-print backtest results to stdout."""
    print("\n" + "=" * 60)
    print("BACKTEST RESULTS")
    print("=" * 60)
    for result in results:
        print(result)
