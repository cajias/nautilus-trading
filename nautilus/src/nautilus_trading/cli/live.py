"""``nt live`` — Binance PROD entry point (SCAFFOLD).

Real-money execution is **out of scope** per the 2026-04-21 no-real-money
directive. This subcommand exists so the
``nt {backtest, paper-trade, live}`` CLI surface is complete and so future
real-money work has a structural template — booting a ``TradingNode`` is
deferred until the directive lifts.

Shape mirrors :mod:`nautilus_trading.cli.paper_trade`:

1. Load and msgspec-validate the YAML into a
   :class:`~nautilus_trading.live.run_config.LiveRunConfig` (rejects YAMLs
   missing — or with ``false`` — ``i_understand_real_money``).
2. Resolve the strategy name against the unified
   :data:`~nautilus_trading.cli._strategy_specs.STRATEGY_SPECS` registry.
3. Merge top-level run-config fields over the per-strategy ``params``
   bucket (top-level wins — same merge direction as paper-trade).
4. Eagerly call :meth:`LiveStrategyRunner.build_config` for friendly
   ``BadParameter`` mapping of builder errors. The build is allowed
   because shape-symmetric construction is the scaffold's contract — it
   gives a future real-money implementer a config object to hand to
   ``TradingNode``.
5. Invoke :meth:`LiveStrategyRunner.main`, which **always raises**
   :class:`NotImplementedError` per the directive. The exception
   propagates uncaught — Typer surfaces the message and exits non-zero.

Build-once contract: the eager ``build_config()`` call is the only
construction; ``main()`` raises immediately and never re-builds.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import msgspec
import typer

from nautilus_trading.cli._common import _ensure_project_root_on_path


def live(
    config: Annotated[
        Path,
        typer.Option(
            "--config",
            "-c",
            help="Path to a YAML run config (see configs/live/ — empty until real-money is in scope).",
            exists=True,
            dir_okay=False,
            readable=True,
        ),
    ],
) -> None:
    """Run a strategy against Binance PROD (real money). Currently scaffolded only.

    Always raises :class:`NotImplementedError` per the 2026-04-21
    no-real-money directive — the scaffold proves the dispatch surface
    works without ever booting a real-money :class:`TradingNode`.
    """
    # Lazy imports so ``import nautilus_trading.cli`` stays cheap at
    # collection time. The live module pulls in the Binance adapter
    # configs transitively; deferring that cost keeps ``nt --help`` snappy.
    from nautilus_trading.cli._strategy_specs import STRATEGY_SPECS
    from nautilus_trading.live.run_config import load_run_config
    from nautilus_trading.live.strategy_runner import LiveStrategyRunner

    _ensure_project_root_on_path()

    try:
        run_config = load_run_config(config)
    except msgspec.ValidationError as exc:
        # Funnels both schema violations (unknown field, wrong type, missing
        # required field) AND the post-decode ``i_understand_real_money is
        # not True`` rejection through one BadParameter — uniform with
        # ``cli/paper_trade.py``'s contract.
        raise typer.BadParameter(f"Invalid config {config}: {exc}", param_hint="--config") from exc

    if run_config.strategy not in STRATEGY_SPECS:
        valid = ", ".join(sorted(STRATEGY_SPECS))
        raise typer.BadParameter(
            f"Unknown strategy '{run_config.strategy}'. Valid: {valid}",
            param_hint="--config",
        )

    # Merge per-strategy ``params`` with top-level fields. Top-level lands
    # LAST so it wins against any stray duplicate inside ``params:``.
    # Same merge direction as ``cli/paper_trade.py`` — top-level YAML is
    # the canonical source of truth for instrument_id / bar_type /
    # trade_size; if a user copies one of those into a per-strategy block
    # by mistake, it gets harmlessly overwritten.
    merged_params: dict[str, object] = {
        **run_config.params,
        "instrument_id": run_config.instrument_id,
        "bar_type": run_config.bar_type,
    }
    if run_config.trade_size is not None:
        merged_params["trade_size"] = run_config.trade_size

    runner = LiveStrategyRunner(
        spec=STRATEGY_SPECS[run_config.strategy],
        params=merged_params,
        log_level=run_config.log_level,
    )

    # Eager build for friendly-error mapping. ``build_config()`` is allowed
    # to succeed — the scaffold's contract is "config is buildable, boot
    # is gated" — so any failure here is a genuine builder/spec error
    # (e.g. missing required field for grid_bot's upper_price). Mapping
    # to BadParameter keeps the operator-facing message clean.
    try:
        runner.build_config()
    except (TypeError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc

    # The contract gate. Always raises NotImplementedError per the
    # 2026-04-21 directive. We let it propagate — Typer surfaces the
    # message and exits non-zero. Wrapping it in try/except would defeat
    # the purpose: operators must see the directive message, not a
    # downcast error.
    runner.main()
