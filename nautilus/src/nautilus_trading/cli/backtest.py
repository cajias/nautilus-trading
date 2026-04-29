"""``nt backtest`` — run a strategy backtest.

Two code paths:

1. **New ``--config <yaml>`` path** (canonical from sub-project B.5 PR 2).
   Loads a :class:`~nautilus_trading.backtest.run_config.BacktestRunConfig`
   from YAML, resolves the strategy via the unified ``STRATEGY_SPECS``
   registry, builds a :class:`~nautilus_trading.backtest.data_sources.DataSource`
   adapter from the discriminated ``data_source`` block, and dispatches
   to :class:`~nautilus_trading.backtest.strategy_runner.BacktestStrategyRunner`.
   Mirror of ``nt paper-trade --config``.

2. **Legacy ``--strategy …`` path** (deprecated).
   Wraps :func:`~nautilus_trading.backtest.runner.build_backtest_config`
   and :func:`~nautilus_trading.backtest.runner.run_backtest`. Emits a
   ``DeprecationWarning``. Will be removed in sub-project B.5 PR 4.

Kronos still rides the legacy path until PR 3 ports it to the generic
runner via a parity-snapshot test. ``configs/backtest/kronos.yaml``
intentionally doesn't ship in PR 2; passing a hand-rolled YAML with
``strategy: kronos`` to the new path is rejected with a friendly
message pointing the user at the legacy ``--strategy`` invocation.

Build-once contract
===================
Per Task A's lesson on the paper-trade CLI: the new path doesn't
double-build the engine config. ``runner.main()`` calls
:meth:`BacktestStrategyRunner.build_config` once internally; the CLI
catches ``(TypeError, ValueError)`` from that single call and maps it
to ``typer.BadParameter`` for friendly error output. Engine boot is
local (no Testnet credentials), so eager pre-validation isn't needed
— failures in ``build_config`` happen before engine construction.
"""

from __future__ import annotations

import warnings
from pathlib import Path
from typing import Annotated

import msgspec
import typer

from nautilus_trading.cli._common import _ensure_project_root_on_path, _resolve_strategy_paths


def backtest(
    config: Annotated[
        Path | None,
        typer.Option(
            "--config",
            "-c",
            help="Path to a YAML run config (see configs/backtest/ for examples).",
            exists=True,
            dir_okay=False,
            readable=True,
        ),
    ] = None,
    # Legacy options retained behind DeprecationWarning. Will be removed in
    # sub-project B.5 PR 4.
    strategy_path: Annotated[
        str,
        typer.Option(
            "--strategy",
            "-s",
            help="(deprecated) Strategy module path or full import path.",
        ),
    ] = "strategies.forex.ema_cross:EMACrossStrategy",
    catalog_dir: Annotated[
        str,
        typer.Option("--catalog", help="(legacy) Path to the Parquet data catalog directory."),
    ] = "catalog",
    instrument_index: Annotated[
        int,
        typer.Option("--instrument-index", help="(legacy) Index of instrument in catalog."),
    ] = 0,
    bar_interval: Annotated[
        str,
        typer.Option("--bar-interval", help="(legacy) Bar interval spec."),
    ] = "1-MINUTE-MID-INTERNAL",
    trade_size: Annotated[
        str,
        typer.Option("--trade-size", help="(legacy) Order quantity per trade."),
    ] = "100000",
    fast_ema: Annotated[
        int,
        typer.Option("--fast-ema", help="(legacy) Fast EMA period."),
    ] = 10,
    slow_ema: Annotated[
        int,
        typer.Option("--slow-ema", help="(legacy) Slow EMA period."),
    ] = 20,
    venue: Annotated[
        str,
        typer.Option("--venue", help="(legacy) Venue name."),
    ] = "SIM",
    base_currency: Annotated[
        str,
        typer.Option("--currency", help="(legacy) Base currency for the venue account."),
    ] = "USD",
    starting_balance: Annotated[
        str,
        typer.Option("--balance", help="(legacy) Starting balance."),
    ] = "1_000_000 USD",
    end_time: Annotated[
        str | None,
        typer.Option("--end-time", help="(legacy) End time filter for data."),
    ] = "2020-01-10",
    data_provider: Annotated[
        str,
        typer.Option("--data-provider", help="(legacy) Data provider name."),
    ] = "test",
    log_level: Annotated[
        str,
        typer.Option("--log-level", help="(legacy) Logging level (DEBUG, INFO, WARNING, ERROR)."),
    ] = "INFO",
) -> None:
    """Run a strategy backtest on historical data.

    Pass ``--config configs/backtest/<name>.yaml`` for the canonical
    YAML-driven path. Legacy ``--strategy`` flags still work but emit a
    ``DeprecationWarning``.
    """
    if config is not None:
        _run_yaml_backtest(config)
        return

    # Legacy path — emit deprecation, then delegate.
    warnings.warn(
        "Running `nt backtest` without --config is deprecated. "
        "Use --config configs/backtest/<name>.yaml instead. "
        "The legacy --strategy path will be removed in sub-project B.5 PR 4.",
        DeprecationWarning,
        stacklevel=2,
    )
    _run_legacy_backtest(
        strategy_path=strategy_path,
        catalog_dir=catalog_dir,
        instrument_index=instrument_index,
        bar_interval=bar_interval,
        trade_size=trade_size,
        fast_ema=fast_ema,
        slow_ema=slow_ema,
        venue=venue,
        base_currency=base_currency,
        starting_balance=starting_balance,
        end_time=end_time,
        data_provider=data_provider,
        log_level=log_level,
    )


def _run_yaml_backtest(config_path: Path) -> None:
    """Resolve the YAML run-config to a ``BacktestStrategyRunner`` invocation.

    Mirrors ``cli/paper_trade.py``'s shape post-Task-#10: dispatch to a
    single ``runner.main()`` call, mapping builder errors to
    ``typer.BadParameter`` so the user sees a clean message instead of
    a stack trace. ``runner.main()`` builds the engine config exactly
    once.
    """
    # Lazy imports so `import nautilus_trading.cli` stays cheap at
    # collection time. The backtest module pulls in
    # nautilus_trader.backtest.engine + the data-source adapters,
    # which is heavier than the top-level import budget tolerates.
    from nautilus_trading.backtest.data_sources import build_data_source
    from nautilus_trading.backtest.run_config import load_run_config
    from nautilus_trading.backtest.strategy_runner import BacktestStrategyRunner
    from nautilus_trading.cli._strategy_specs import STRATEGY_SPECS

    _ensure_project_root_on_path()

    try:
        run_config = load_run_config(config_path)
    except msgspec.ValidationError as exc:
        raise typer.BadParameter(
            f"Invalid config {config_path}: {exc}",
            param_hint="--config",
        ) from exc

    if run_config.strategy not in STRATEGY_SPECS:
        valid = ", ".join(sorted(STRATEGY_SPECS))
        raise typer.BadParameter(
            f"Unknown strategy '{run_config.strategy}'. Valid: {valid}",
            param_hint="--config",
        )

    if run_config.strategy == "kronos":
        # PR 2 keeps kronos on the legacy KronosBacktestRunner. PR 3 ports
        # it via parity-snapshot test and ships configs/backtest/kronos.yaml
        # at the same time. A hand-rolled YAML with `strategy: kronos`
        # would otherwise crash deep inside the runner; surface a clear
        # message instead.
        raise typer.BadParameter(
            "kronos backtest still uses the legacy KronosBacktestRunner in PR 2. "
            "Run via `nt backtest --strategy strategies.crypto.kronos.strategy:KronosStrategy` "
            "until PR 3 ports it to the generic runner.",
            param_hint="--config",
        )

    try:
        data_source = build_data_source(run_config.data_source)
    except (TypeError, ValueError) as exc:
        raise typer.BadParameter(str(exc), param_hint="--config") from exc

    runner = BacktestStrategyRunner(
        spec=STRATEGY_SPECS[run_config.strategy],
        run_config=run_config,
        data_source=data_source,
    )

    # Single-build contract (Task A's lesson): runner.main() builds the
    # engine config once internally. Builder / strategy-config errors
    # raise from there; map (TypeError, ValueError) to BadParameter so
    # friendly errors stay friendly.
    try:
        runner.main()
    except (TypeError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc


def _run_legacy_backtest(
    *,
    strategy_path: str,
    catalog_dir: str,
    instrument_index: int,
    bar_interval: str,
    trade_size: str,
    fast_ema: int,
    slow_ema: int,
    venue: str,
    base_currency: str,
    starting_balance: str,
    end_time: str | None,
    data_provider: str,
    log_level: str,
) -> None:
    """Pre-B.5 backtest path. Retained for one release behind
    ``DeprecationWarning``; deleted in sub-project B.5 PR 4.

    Kronos backtest still routes here in PR 2 — its
    :class:`KronosBacktestRunner` hasn't been ported yet.
    """
    from nautilus_trading.backtest.runner import build_backtest_config, print_results, run_backtest
    from nautilus_trading.data.download import ensure_catalog

    _ensure_project_root_on_path()

    resolved_strategy, resolved_config = _resolve_strategy_paths(strategy_path)

    catalog_path = Path(catalog_dir).resolve()
    catalog = ensure_catalog(catalog_path, provider=data_provider)

    cfg = build_backtest_config(
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

    results = run_backtest(cfg)
    print_results(results)
