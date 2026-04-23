"""``nt paper-trade`` — Binance Spot Testnet paper-trade entry point.

Loads a YAML run config, resolves the strategy name to a ``StrategySpec`` from
the unified registry in ``cli/_strategy_specs.py``, hands the spec + parsed
params to :class:`PaperTradeStrategyRunner`, and delegates to ``runner.main()``.

Supersedes the sub-project A ``_RUNNERS`` dispatch table. Every paper-trade
strategy now flows through the same generic runner — no per-strategy shim.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import msgspec
import typer

from nautilus_trading.cli._common import _ensure_project_root_on_path


def paper_trade(
    config: Annotated[
        Path,
        typer.Option(
            "--config",
            help="Path to a YAML run config (see configs/paper/ for examples).",
            exists=True,
            dir_okay=False,
            readable=True,
        ),
    ],
) -> None:
    """Run a strategy on Binance Spot Testnet (paper trading)."""
    # Lazy imports so `import nautilus_trading.cli` stays cheap at collection
    # time. ``_strategy_specs`` + the generic runner import transitively pull
    # in msgspec-heavy adapter config modules; deferring that cost keeps
    # ``nt --help`` snappy.
    from nautilus_trading.cli._strategy_specs import STRATEGY_SPECS
    from nautilus_trading.paper_trade.run_config import load_run_config
    from nautilus_trading.paper_trade.secrets import load_dotenv_local
    from nautilus_trading.paper_trade.strategy_runner import PaperTradeStrategyRunner

    _ensure_project_root_on_path()
    load_dotenv_local()

    try:
        run_config = load_run_config(config)
    except msgspec.ValidationError as exc:
        raise typer.BadParameter(f"Invalid config {config}: {exc}", param_hint="--config") from exc

    if run_config.strategy not in STRATEGY_SPECS:
        valid = ", ".join(sorted(STRATEGY_SPECS))
        raise typer.BadParameter(
            f"Unknown strategy '{run_config.strategy}'. Valid: {valid}",
            param_hint="--config",
        )

    # Merge the three top-level PaperRunConfig fields with per-strategy
    # ``params``. The StrategySpec builder plucks what it needs; extra keys
    # are ignored by the builder.
    merged_params: dict[str, object] = {
        "instrument_id": run_config.instrument_id,
        "bar_type": run_config.bar_type,
        **run_config.params,
    }
    if run_config.trade_size is not None:
        merged_params["trade_size"] = run_config.trade_size

    runner = PaperTradeStrategyRunner(
        spec=STRATEGY_SPECS[run_config.strategy],
        params=merged_params,
        log_level=run_config.log_level,
    )

    # Build the config eagerly so any ValueError from a builder (missing
    # required field, bad type) surfaces as typer.BadParameter rather than
    # an uncaught stack trace once the TradingNode starts booting.
    try:
        runner.build_config()
    except (TypeError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc

    runner.main()
