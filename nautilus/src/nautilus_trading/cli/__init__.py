"""CLI entry point for nautilus-trading."""

import typer

from nautilus_trading.cli import strategies as strategies_mod
from nautilus_trading.cli.backtest import backtest
from nautilus_trading.cli.live import live
from nautilus_trading.cli.paper_trade import paper_trade

app = typer.Typer(
    name="nt",
    help="Nautilus Trading CLI - algorithmic trading powered by NautilusTrader.",
    no_args_is_help=True,
)

app.command(name="backtest")(backtest)
app.command(name="live")(live)
app.command(name="paper-trade")(paper_trade)
app.command(name="strategies")(strategies_mod.strategies)
