"""`nt paper-trade` — Binance Spot Testnet paper-trade entry point.

This module parses args shared across strategies, loads secrets, resolves the
strategy-name to a concrete PaperTradeRunner class, instantiates it, and
delegates to `runner.main()`.
"""

from __future__ import annotations

from typing import Any

import typer

from nautilus_trading.cli._common import _ensure_project_root_on_path

# Strategy-name → runner class, populated lazily to keep CLI import cheap.
_RUNNERS: dict[str, type] = {}


def _load_runners() -> None:
    """Populate the strategy-name → runner class registry on first use."""
    if _RUNNERS:
        return
    # Lazy import: strategies/ lives at the project root, not inside the
    # nautilus/ package, so it only resolves after _ensure_project_root_on_path()
    # has run. mypy can't see it — but the import is exercised at runtime by the
    # CLI tests in tests/cli/test_paper_trade_cli.py.
    from strategies.crypto.dca_bot_paper import (  # type: ignore[import-not-found]
        DCABotPaperTradeRunner,
    )
    from strategies.crypto.ema_cross_paper import (  # type: ignore[import-not-found]
        EMACrossPaperTradeRunner,
    )
    from strategies.crypto.grid_bot_paper import (  # type: ignore[import-not-found]
        GridBotPaperTradeRunner,
    )
    from strategies.crypto.hybrid_sma_r10_paper import (  # type: ignore[import-not-found]
        HybridSMAR10PaperTradeRunner,
    )
    from strategies.crypto.timesfm_grid_paper import (  # type: ignore[import-not-found]
        TimesFMGridPaperTradeRunner,
    )
    from strategies.crypto.timesfm_swing_paper import (  # type: ignore[import-not-found]
        TimesFMSwingPaperTradeRunner,
    )

    _RUNNERS["ema_cross"] = EMACrossPaperTradeRunner
    _RUNNERS["grid_bot"] = GridBotPaperTradeRunner
    _RUNNERS["dca_bot"] = DCABotPaperTradeRunner
    _RUNNERS["timesfm_swing"] = TimesFMSwingPaperTradeRunner
    _RUNNERS["hybrid_sma_r10"] = HybridSMAR10PaperTradeRunner
    _RUNNERS["timesfm_grid"] = TimesFMGridPaperTradeRunner


def paper_trade(
    strategy: str = typer.Option(
        ...,
        "--strategy",
        help="Strategy module name (e.g. 'ema_cross').",
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
    trade_size: str = typer.Option(..., "--trade-size"),
    fast_ema: int = typer.Option(10, "--fast-ema"),
    slow_ema: int = typer.Option(20, "--slow-ema"),
    upper_price: str | None = typer.Option(None, "--upper-price"),
    lower_price: str | None = typer.Option(None, "--lower-price"),
    grid_levels: int | None = typer.Option(None, "--grid-levels"),
    buy_interval_bars: int | None = typer.Option(None, "--buy-interval-bars"),
    buy_amount: str | None = typer.Option(None, "--buy-amount"),
    sma_fast: int | None = typer.Option(None, "--sma-fast"),
    sma_slow: int | None = typer.Option(None, "--sma-slow"),
    stop_fast: str | None = typer.Option(None, "--stop-fast"),
    stop_slow: str | None = typer.Option(None, "--stop-slow"),
    duration: str | None = typer.Option(
        None,
        "--duration",
        help="Optional time-box like '30m' or '2h'. Omit for continuous run.",
    ),
    log_level: str = typer.Option("INFO", "--log-level"),
) -> None:
    """Run a strategy on Binance Spot Testnet (paper trading)."""
    # Lazy imports so `import nautilus_trading.cli` stays cheap at collection time.
    from nautilus_trading.paper_trade.secrets import load_dotenv_local

    _ensure_project_root_on_path()
    load_dotenv_local()
    _load_runners()

    if strategy not in _RUNNERS:
        valid = ", ".join(sorted(_RUNNERS))
        raise typer.BadParameter(
            f"Unknown strategy '{strategy}'. Valid: {valid}",
            param_hint="--strategy",
        )

    runner_cls = _RUNNERS[strategy]

    # Build per-strategy kwargs so options don't leak across strategies
    # (e.g. fast_ema has no place on a grid runner, and vice versa).
    base_kwargs: dict[str, Any] = {
        "instrument_id": instrument_id,
        "bar_type": bar_type,
        "trade_size": trade_size,
        "log_level": log_level,
    }
    if strategy == "ema_cross":
        kwargs = {**base_kwargs, "fast_ema": fast_ema, "slow_ema": slow_ema}
    elif strategy == "timesfm_swing":
        kwargs = {**base_kwargs, "fast_ema": fast_ema, "slow_ema": slow_ema}
    elif strategy == "grid_bot":
        kwargs = {
            **base_kwargs,
            "upper_price": upper_price,
            "lower_price": lower_price,
            "grid_levels": grid_levels,
        }
    elif strategy == "dca_bot":
        kwargs = {**base_kwargs, "buy_interval_bars": buy_interval_bars}
        if buy_amount is not None:
            kwargs["buy_amount"] = buy_amount
    elif strategy == "hybrid_sma_r10":
        # HybridSMA sizes from equity; intentionally omit trade_size.
        kwargs = {
            "instrument_id": instrument_id,
            "bar_type": bar_type,
            "log_level": log_level,
            "sma_fast": sma_fast,
            "sma_slow": sma_slow,
            "stop_fast": stop_fast,
            "stop_slow": stop_slow,
        }
    elif strategy == "timesfm_grid":
        # TimesFMGrid: base fields only — all ML/grid/stop params default.
        kwargs = base_kwargs
    else:
        kwargs = base_kwargs

    # Dispatch: each runner accepts only the kwargs its dataclass declares.
    # TypeError → unexpected kwargs (wrong field name); ValueError → strategy
    # builder rejected missing required args (e.g. grid_bot without --upper-price).
    # We eagerly call build_config() here so the builder validates *before* main()
    # boots a TradingNode — a raw traceback would be hostile CLI UX.
    try:
        runner = runner_cls(**kwargs)
        runner.build_config()
    except (TypeError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc

    runner.main()
