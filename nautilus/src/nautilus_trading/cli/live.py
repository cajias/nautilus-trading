"""CLI command for live/paper trading."""

from __future__ import annotations

from typing import Annotated

import typer

from nautilus_trading.cli._common import _ensure_project_root_on_path, _resolve_strategy_paths
from nautilus_trading.live.runner import build_live_config, run_live


def live(
    strategy_path: Annotated[
        str,
        typer.Option(
            "--strategy",
            "-s",
            help="Strategy module path (e.g. strategies.crypto.grid_bot) or full import path.",
        ),
    ],
    instrument_id: Annotated[
        str,
        typer.Option("--instrument", "-i", help="Instrument ID (e.g. BTCUSDT.BINANCE)."),
    ],
    bar_type: Annotated[
        str,
        typer.Option("--bar-type", help="Bar type (e.g. BTCUSDT.BINANCE-1-HOUR-LAST-EXTERNAL)."),
    ],
    trade_size: Annotated[
        str,
        typer.Option("--trade-size", help="Order quantity per trade."),
    ],
    config_path: Annotated[
        str | None,
        typer.Option("--config", "-c", help="Config import path (auto-derived if omitted)."),
    ] = None,
    testnet: Annotated[
        bool,
        typer.Option("--testnet/--live", help="Use Binance testnet (default) or production."),
    ] = True,
    account_type: Annotated[
        str,
        typer.Option("--account-type", help="Binance account type: SPOT, MARGIN, USDT_FUTURE."),
    ] = "SPOT",
    log_level: Annotated[
        str,
        typer.Option("--log-level", help="Logging level (DEBUG, INFO, WARNING, ERROR)."),
    ] = "INFO",
    # Grid Bot specific
    upper_price: Annotated[
        str | None,
        typer.Option("--upper-price", help="Grid Bot: upper price bound."),
    ] = None,
    lower_price: Annotated[
        str | None,
        typer.Option("--lower-price", help="Grid Bot: lower price bound."),
    ] = None,
    grid_levels: Annotated[
        int,
        typer.Option("--grid-levels", help="Grid Bot: number of grid levels."),
    ] = 20,
    # DCA Bot specific
    buy_amount: Annotated[
        str | None,
        typer.Option("--buy-amount", help="DCA Bot: fixed dollar amount per buy."),
    ] = None,
    buy_interval_bars: Annotated[
        int,
        typer.Option("--buy-interval", help="DCA Bot: buy every N bars."),
    ] = 60,
    # EMA periods
    fast_ema: Annotated[
        int,
        typer.Option("--fast-ema", help="Fast EMA period."),
    ] = 50,
    slow_ema: Annotated[
        int,
        typer.Option("--slow-ema", help="Slow EMA period."),
    ] = 200,
    # Hybrid SMA R10 ensemble specific
    sma_fast: Annotated[
        int,
        typer.Option("--sma-fast", help="Hybrid SMA R10: fast SMA period."),
    ] = 20,
    sma_slow: Annotated[
        int,
        typer.Option("--sma-slow", help="Hybrid SMA R10: slow SMA period."),
    ] = 30,
    stop_fast: Annotated[
        float,
        typer.Option("--stop-fast", help="Hybrid SMA R10: fast trailing stop pct."),
    ] = 0.07,
    stop_slow: Annotated[
        float,
        typer.Option("--stop-slow", help="Hybrid SMA R10: slow trailing stop pct."),
    ] = 0.08,
) -> None:
    """Run a strategy on Binance (testnet by default, --live for production).

    Examples:

        nt live -s strategies.crypto.grid_bot -i SOLUSDT.BINANCE \\
            --bar-type SOLUSDT.BINANCE-1-HOUR-LAST-EXTERNAL \\
            --trade-size 0.10 --upper-price 100 --lower-price 60

        nt live -s strategies.crypto.dca_bot -i BTCUSDT.BINANCE \\
            --bar-type BTCUSDT.BINANCE-1-HOUR-LAST-EXTERNAL \\
            --buy-amount 5.0 --trade-size 0.001
    """
    _ensure_project_root_on_path()

    resolved_strategy, resolved_config = _resolve_strategy_paths(strategy_path)
    if config_path is not None:
        resolved_config = config_path

    # Build strategy config dict via STRATEGY_BUILDERS registry.
    from nautilus_trading.cli._strategy_configs import STRATEGY_BUILDERS

    module_name = strategy_path.rsplit(".", 1)[-1].split(":")[0]
    builder = STRATEGY_BUILDERS.get(module_name)

    builder_args = {
        "instrument_id": instrument_id,
        "bar_type": bar_type,
        "trade_size": trade_size,
        "upper_price": upper_price,
        "lower_price": lower_price,
        "grid_levels": grid_levels,
        "buy_amount": buy_amount,
        "buy_interval_bars": buy_interval_bars,
        "fast_ema": fast_ema,
        "slow_ema": slow_ema,
        "sma_fast": sma_fast,
        "sma_slow": sma_slow,
        "stop_fast": stop_fast,
        "stop_slow": stop_slow,
        "module_name": module_name,
    }

    if builder is None:
        # Unknown strategy — fall back to the minimal base dict (preserves old behavior).
        strat_config: dict = {
            "instrument_id": instrument_id,
            "bar_type": bar_type,
            "trade_size": trade_size,
        }
    else:
        try:
            strat_config = builder.build(builder_args)
        except ValueError as exc:
            typer.echo(f"ERROR: {exc}", err=True)
            raise typer.Exit(1) from exc

    env_label = "TESTNET" if testnet else "PRODUCTION"
    typer.echo(f"Starting {module_name} on {instrument_id} ({env_label})")
    typer.echo(f"Strategy: {resolved_strategy}")
    typer.echo(f"Bar type: {bar_type}")
    typer.echo(f"Trade size: {trade_size}")
    if not testnet:
        typer.confirm("You are about to trade with REAL money. Continue?", abort=True)

    config = build_live_config(
        strategy_path=resolved_strategy,
        config_path=resolved_config,
        strategy_config=strat_config,
        instrument_id=instrument_id,
        account_type=account_type,
        testnet=testnet,
        log_level=log_level,
    )

    run_live(config)
