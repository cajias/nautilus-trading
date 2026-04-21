"""`nt paper-trade` — Binance Spot Testnet paper-trade entry point.

This module is intentionally slim: it parses the args shared with `nt backtest`,
loads secrets, resolves the strategy builder, and delegates to a concrete
PaperTradeRunner implementation (wired in PR 3 and onward).
"""

from __future__ import annotations

import typer

from nautilus_trading.cli._common import (
    _ensure_project_root_on_path,
    _resolve_strategy_paths,
)


def paper_trade(
    strategy: str = typer.Option(
        ...,
        "--strategy",
        help="Strategy module name (e.g. 'ema_cross', 'grid_bot').",
    ),
    instrument_id: str = typer.Option(
        ...,
        "--instrument-id",
        help="Binance instrument, e.g. 'BTCUSDT.BINANCE'.",
    ),
    bar_type: str = typer.Option(
        ...,
        "--bar-type",
        help="Bar type, e.g. 'BTCUSDT.BINANCE-1-MINUTE-LAST-EXTERNAL'.",
    ),
    trade_size: float = typer.Option(..., "--trade-size"),
    duration: str | None = typer.Option(
        None,
        "--duration",
        help="Optional time-box like '30m' or '2h'. Omit for continuous run.",
    ),
    log_level: str = typer.Option("INFO", "--log-level"),
) -> None:
    """Run a strategy on Binance Spot Testnet (paper trading)."""
    # Lazy imports so `import nautilus_trading.cli` stays cheap at collection time.
    from nautilus_trading.cli._strategy_configs import STRATEGY_BUILDERS
    from nautilus_trading.paper_trade.secrets import load_dotenv_local

    _ensure_project_root_on_path()
    load_dotenv_local()

    if strategy not in STRATEGY_BUILDERS:
        valid = ", ".join(sorted(STRATEGY_BUILDERS))
        raise typer.BadParameter(
            f"Unknown strategy '{strategy}'. Valid: {valid}",
            param_hint="--strategy",
        )

    _strategy_path, _config_path = _resolve_strategy_paths(strategy)
    typer.echo(
        f"paper-trade stub: strategy={strategy} instrument={instrument_id} "
        f"bar_type={bar_type} trade_size={trade_size} duration={duration} "
        f"log_level={log_level} (runner wiring arrives in PR 3)",
        err=True,
    )
    raise typer.Exit(code=1)
