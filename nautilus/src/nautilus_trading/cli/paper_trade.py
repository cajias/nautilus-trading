"""`nt paper-trade` — Binance Spot Testnet paper-trade entry point.

This module loads a YAML run config, resolves the strategy-name to a concrete
PaperTradeRunner class, instantiates it, and delegates to `runner.main()`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import msgspec
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
    from strategies.crypto.rvs_swing_paper import (  # type: ignore[import-not-found]
        RVSSwingPaperTradeRunner,
    )
    from strategies.crypto.shock_guard_paper import (  # type: ignore[import-not-found]
        ShockGuardPaperTradeRunner,
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
    _RUNNERS["rvs_swing"] = RVSSwingPaperTradeRunner
    _RUNNERS["shock_guard"] = ShockGuardPaperTradeRunner


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
    # Lazy imports so `import nautilus_trading.cli` stays cheap at collection time.
    from nautilus_trading.paper_trade.run_config import load_run_config
    from nautilus_trading.paper_trade.secrets import load_dotenv_local

    _ensure_project_root_on_path()
    load_dotenv_local()
    _load_runners()

    try:
        run_config = load_run_config(config)
    except msgspec.ValidationError as exc:
        raise typer.BadParameter(f"Invalid config {config}: {exc}", param_hint="--config") from exc

    if run_config.strategy not in _RUNNERS:
        valid = ", ".join(sorted(_RUNNERS))
        raise typer.BadParameter(
            f"Unknown strategy '{run_config.strategy}'. Valid: {valid}",
            param_hint="--config",
        )

    runner_cls = _RUNNERS[run_config.strategy]

    kwargs: dict[str, object] = {
        "instrument_id": run_config.instrument_id,
        "bar_type": run_config.bar_type,
        "log_level": run_config.log_level,
        **run_config.params,
    }
    if run_config.trade_size is not None:
        kwargs["trade_size"] = run_config.trade_size

    try:
        runner = runner_cls(**kwargs)
        runner.build_config()
    except (TypeError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc

    runner.main()
