"""CLI command for running backtests."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated, Optional

import typer

from nautilus_trading.backtest.runner import build_backtest_config, print_results, run_backtest
from nautilus_trading.data.download import ensure_catalog


def _ensure_project_root_on_path() -> None:
    """Add the project root (parent of the ``nautilus/`` package dir) to sys.path.

    This allows strategy modules like ``strategies.forex.ema_cross`` that live at the
    project root to be imported via ``ImportableStrategyConfig``.
    """
    # Walk up from this file:
    #   nautilus/src/nautilus_trading/cli/backtest.py -> project root is 4 levels up
    project_root = str(Path(__file__).resolve().parents[4])
    if project_root not in sys.path:
        sys.path.insert(0, project_root)


def backtest(
    strategy_path: Annotated[
        str,
        typer.Option("--strategy", "-s", help="Import path for Strategy class."),
    ] = "strategies.forex.ema_cross:EMACrossStrategy",
    config_path: Annotated[
        str,
        typer.Option("--config", "-c", help="Import path for StrategyConfig class."),
    ] = "strategies.forex.ema_cross:EMACrossConfig",
    catalog_dir: Annotated[
        str,
        typer.Option("--catalog", help="Path to the Parquet data catalog directory."),
    ] = "catalog",
    instrument_index: Annotated[
        int,
        typer.Option("--instrument-index", help="Index of instrument in catalog."),
    ] = 0,
    bar_interval: Annotated[
        str,
        typer.Option("--bar-interval", help="Bar interval spec (e.g. 1-MINUTE-MID-INTERNAL)."),
    ] = "1-MINUTE-MID-INTERNAL",
    trade_size: Annotated[
        str,
        typer.Option("--trade-size", help="Order quantity per trade."),
    ] = "100000",
    fast_ema: Annotated[
        int,
        typer.Option("--fast-ema", help="Fast EMA period."),
    ] = 10,
    slow_ema: Annotated[
        int,
        typer.Option("--slow-ema", help="Slow EMA period."),
    ] = 20,
    venue: Annotated[
        str,
        typer.Option("--venue", help="Venue name."),
    ] = "SIM",
    base_currency: Annotated[
        str,
        typer.Option("--currency", help="Base currency for the venue account."),
    ] = "USD",
    starting_balance: Annotated[
        str,
        typer.Option("--balance", help="Starting balance (e.g. '1_000_000 USD')."),
    ] = "1_000_000 USD",
    end_time: Annotated[
        Optional[str],
        typer.Option("--end-time", help="End time filter for data (e.g. 2020-01-10)."),
    ] = "2020-01-10",
    data_provider: Annotated[
        str,
        typer.Option("--data-provider", help="Data provider name (e.g. 'test', 'binance')."),
    ] = "test",
    log_level: Annotated[
        str,
        typer.Option("--log-level", help="Logging level (DEBUG, INFO, WARNING, ERROR)."),
    ] = "INFO",
) -> None:
    """Run an EMA crossover backtest (or any importable strategy) on sample data."""
    _ensure_project_root_on_path()

    catalog_path = Path(catalog_dir).resolve()
    catalog = ensure_catalog(catalog_path, provider=data_provider)

    config = build_backtest_config(
        catalog,
        strategy_path=strategy_path,
        config_path=config_path,
        instrument_index=instrument_index,
        bar_interval=bar_interval,
        trade_size=trade_size,
        fast_ema_period=fast_ema,
        slow_ema_period=slow_ema,
        venue_name=venue,
        base_currency=base_currency,
        starting_balance=starting_balance,
        end_time=end_time,
        log_level=log_level,
    )

    results = run_backtest(config)
    print_results(results)
