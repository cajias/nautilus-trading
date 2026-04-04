"""CLI command for running backtests."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated

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


# Maps strategy module names to their class names
_STRATEGY_CLASSES: dict[str, tuple[str, str]] = {
    "ema_cross": ("EMACrossStrategy", "EMACrossConfig"),
    "grid_bot": ("GridBotStrategy", "GridBotConfig"),
    "dca_bot": ("DCABotStrategy", "DCABotConfig"),
    "timesfm_swing": ("TimesFMSwingStrategy", "TimesFMSwingConfig"),
}


def _resolve_strategy_paths(module_path: str) -> tuple[str, str]:
    """Resolve a module path like 'strategies.crypto.grid_bot' to full import paths.

    If the path already contains ':', it's treated as an explicit import path.
    Otherwise, the strategy/config class names are inferred from the module name.
    """
    if ":" in module_path:
        module, cls = module_path.rsplit(":", 1)
        config_cls = cls.replace("Strategy", "Config")
        return module_path, f"{module}:{config_cls}"

    module_name = module_path.rsplit(".", 1)[-1]
    if module_name in _STRATEGY_CLASSES:
        strategy_cls, config_cls = _STRATEGY_CLASSES[module_name]
    else:
        # Fallback: PascalCase the module name
        parts = module_name.split("_")
        base = "".join(p.capitalize() for p in parts)
        strategy_cls = f"{base}Strategy"
        config_cls = f"{base}Config"

    return f"{module_path}:{strategy_cls}", f"{module_path}:{config_cls}"


def backtest(
    strategy_path: Annotated[
        str,
        typer.Option(
            "--strategy", "-s",
            help="Strategy module path (e.g. strategies.crypto.grid_bot) or full import path.",
        ),
    ] = "strategies.forex.ema_cross:EMACrossStrategy",
    config_path: Annotated[
        str | None,
        typer.Option("--config", "-c", help="Config import path (auto-derived if omitted)."),
    ] = None,
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
        str | None,
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
    """Run a strategy backtest on historical data."""
    _ensure_project_root_on_path()

    # Auto-resolve strategy/config paths if not explicitly provided
    resolved_strategy, resolved_config = _resolve_strategy_paths(strategy_path)
    if config_path is not None:
        resolved_config = config_path

    catalog_path = Path(catalog_dir).resolve()
    catalog = ensure_catalog(catalog_path, provider=data_provider)

    config = build_backtest_config(
        catalog,
        strategy_path=resolved_strategy,
        config_path=resolved_config,
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
