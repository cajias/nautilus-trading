# Sub-project B · PR 6 — YAML Run Configs

**Date:** 2026-04-22
**Scope:** Replace the 16-option, 8-branch per-strategy CLI flag ladder in `nt paper-trade` with a single `--config path/to/run.yaml` option backed by committed example configs. Delete the flag path entirely — no deprecation window.
**Status:** Approved design — ready for implementation plan.

## Motivation

PR 3–5 grew the `paper-trade` CLI to 16 Typer options and an 8-branch `if/elif` ladder inside `paper_trade()` that builds per-strategy kwargs. Adding strategies is linear-cost and each addition risks the same class of bug (flag name drift, branch ordering, forgotten default). The project's strategy configs are already `msgspec` frozen dataclasses, so decoding YAML straight into them is the natural next step.

The user's explicit direction for this PR: **no fallbacks, no deprecation warnings, no mutual-exclusion dance**. The flag path is deleted in the same commit range as the new YAML path goes in.

## Non-goals

- No profile inheritance, env-var interpolation, or `--config` layering with `--flag` overrides.
- No change to `StrategyConfigBuilder` Protocol, `STRATEGY_BUILDERS` registry, or any runner class.
- No change to `BacktestRunner` / `nt backtest`.

## Architecture

### New file: `nautilus/src/nautilus_trading/paper_trade/run_config.py`

Defines the YAML schema via a `msgspec.Struct, frozen=True, forbid_unknown_fields=True` dataclass:

```python
class PaperRunConfig(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    strategy: str                          # registry key: "ema_cross" | "grid_bot" | …
    instrument_id: str                     # "BTCUSDT.BINANCE"
    bar_type: str                          # "BTCUSDT.BINANCE-1-MINUTE-LAST-EXTERNAL"
    trade_size: str | None = None          # Decimal-string; None for hybrid_sma_r10
    log_level: str = "INFO"
    duration: str | None = None            # "30m" | "2h" | None
    params: dict[str, object] = msgspec.field(default_factory=dict)
```

`load_run_config(path: Path) -> PaperRunConfig` reads the YAML, validates, returns the Struct. Unknown top-level keys → `msgspec.ValidationError`. File-not-found, malformed YAML, missing required fields → raise a typed error the CLI remaps to `typer.BadParameter`.

The `params` field holds every strategy-specific value (`fast_ema`, `sma_fast`, `upper_price`, etc.). It is a dict because it must accept variable-shape input; validation happens at the existing `StrategyConfigBuilder.build()` boundary, which already raises `ValueError` for missing/bad fields. That error path is already wired through the `try/except (TypeError, ValueError) → typer.BadParameter` remap in `cli/paper_trade.py`.

### CLI change: `nautilus/src/nautilus_trading/cli/paper_trade.py`

**The entire flag surface is deleted.** New `paper_trade()` signature:

```python
def paper_trade(
    config: Path = typer.Option(
        ...,
        "--config",
        help="Path to a YAML run config (see configs/paper/ for examples).",
        exists=True,
        dir_okay=False,
        readable=True,
    ),
) -> None:
    """Run a strategy on Binance Spot Testnet (paper trading)."""
    from nautilus_trading.paper_trade.secrets import load_dotenv_local

    _ensure_project_root_on_path()
    load_dotenv_local()
    _load_runners()

    try:
        run_config = load_run_config(config)
    except msgspec.ValidationError as exc:
        raise typer.BadParameter(
            f"Invalid config {config}: {exc}",
            param_hint="--config",
        ) from exc

    if run_config.strategy not in _RUNNERS:
        valid = ", ".join(sorted(_RUNNERS))
        raise typer.BadParameter(
            f"Unknown strategy '{run_config.strategy}'. Valid: {valid}",
            param_hint="--config",
        )
    runner_cls = _RUNNERS[run_config.strategy]

    runner_kwargs: dict[str, object] = {
        "instrument_id": run_config.instrument_id,
        "bar_type": run_config.bar_type,
        "log_level": run_config.log_level,
        **run_config.params,
    }
    if run_config.trade_size is not None:
        runner_kwargs["trade_size"] = run_config.trade_size

    try:
        runner = runner_cls(**runner_kwargs)
        runner.build_config()                            # eager validate
    except (TypeError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc

    runner.main()
```

Rationale for the `trade_size is not None` branch: runners that don't declare `trade_size` as a dataclass field (currently only `HybridSMAR10PaperTradeRunner`) would reject it as an unexpected kwarg. Dropping the `None` value before `runner_cls(**kwargs)` sidesteps that asymmetry without encoding runner-shape assumptions in the CLI.

**Deleted, in the same PR:**
- All 16 strategy-specific and per-strategy-shared Typer options (`--strategy`, `--instrument-id`, `--bar-type`, `--trade-size`, `--fast-ema`, `--slow-ema`, `--upper-price`, `--lower-price`, `--grid-levels`, `--buy-interval-bars`, `--buy-amount`, `--sma-fast`, `--sma-slow`, `--stop-fast`, `--stop-slow`, `--duration`, `--log-level`).
- The 8-branch `if/elif` ladder that built per-strategy kwargs.
- Every flag-based dispatch test in `tests/cli/test_paper_trade_cli.py` (replaced by the parametrized config-file test).

Typer's own `exists=True, dir_okay=False, readable=True` handles missing-file errors — no manual `FileNotFoundError` remap needed.

### Example configs: `configs/paper/*.yaml` (committed)

One per strategy, each runnable end-to-end:

```
configs/paper/
├── ema_cross.yaml
├── grid_bot.yaml
├── dca_bot.yaml
├── timesfm_swing.yaml
├── hybrid_sma_r10.yaml
├── timesfm_grid.yaml
├── rvs_swing.yaml
└── shock_guard.yaml
```

Example `configs/paper/hybrid_sma_r10.yaml`:

```yaml
strategy: hybrid_sma_r10
instrument_id: BTCUSDT.BINANCE
bar_type: BTCUSDT.BINANCE-1-MINUTE-LAST-EXTERNAL
# trade_size intentionally omitted — HybridSMA sizes from equity
trade_size: null
log_level: INFO
params:
  sma_fast: 10
  sma_slow: 30
  stop_fast: "0.05"
  stop_slow: "0.10"
```

## Tests

1. **Schema round-trip** (`tests/paper_trade/test_run_config.py`) — valid YAML → `PaperRunConfig`; unknown field → `ValidationError`; missing required → `ValidationError`; `trade_size: null` decodes to `None`. 4 new tests.
2. **CLI dispatch via `--config`** (`tests/cli/test_paper_trade_configs.py`) — one parametrized test per committed example that loads the YAML, monkeypatches the runner's `.main()` to a recorder, and asserts the right runner class is instantiated with the expected kwargs. 8 new parametrized cases.
3. **Bad YAML paths** — two tests: unknown strategy in YAML → `BadParameter`; unknown top-level YAML field → `BadParameter` (not raw `ValidationError`).
4. **Missing-config error** — Typer's own `exists=True` guard produces the error; one test confirms exit code ≠ 0 with a mention of the bogus path.

**Deleted tests** (moved out of scope — redundant with the parametrized dispatch test, or no longer applicable because the flags are gone):
- `test_paper_trade_ema_cross_dispatches_to_runner`
- `test_paper_trade_grid_bot_dispatches_to_runner`
- `test_paper_trade_dca_bot_dispatches_to_runner`
- `test_paper_trade_timesfm_swing_dispatches_to_runner`
- `test_paper_trade_hybrid_sma_r10_dispatches_to_runner`
- `test_paper_trade_timesfm_grid_dispatches_to_runner`
- `test_paper_trade_rvs_swing_dispatches_to_runner`
- `test_paper_trade_shock_guard_dispatches_to_runner`
- `test_paper_trade_grid_bot_missing_required_args_is_usage_error`
- `test_paper_trade_hybrid_sma_r10_missing_required_args_is_usage_error`
- `test_paper_trade_unknown_strategy_is_usage_error` (flag form; replaced by YAML form)

Expected test-count delta: 286 → 286 − 11 + 4 (schema) + 8 (parametrized dispatch) + 2 (bad-YAML) + 1 (missing-config) = **290 passed, 19 skipped**.

## Tooling & docs touched

- `nautilus/pyproject.toml` — add `pyyaml>=6.0` dependency (`msgspec.yaml` requires it).
- `Makefile` — if there is a `paper-trade` target or helper that passes flags, rewrite it to accept `CONFIG=path/to.yaml` and pass `--config "$(CONFIG)"`. If no such target exists, no change.
- `CLAUDE.md` — if it documents the `nt paper-trade` flag form, update to the `--config` form.
- `docs/superpowers/plans/2026-04-21-subproject-b-implementation.md` — renumber PR 6/7 → 7/8 and insert a pointer to this plan.

## Roadmap impact

Renumbering of sub-project B implementation plan:

| Slot | Before | After |
|------|--------|-------|
| PR 6 | Kronos migration + parity gate | **YAML run configs (this PR)** |
| PR 7 | CI smoke + runbook + roadmap | **Kronos migration + parity gate** (was PR 6) |
| PR 8 | — | **CI smoke + runbook + roadmap** (was PR 7) |

Kronos benefits immediately: it ships a single `configs/paper/kronos.yaml` instead of adding ~17 new CLI flags to an already-overgrown signature.

## Open questions

None — all decisions locked:

- ✅ Format: YAML (not TOML/JSON)
- ✅ Location: committed under `configs/paper/` (not gitignored `runs/`)
- ✅ Scope: new PR between current PR 5 and Kronos
- ✅ Schema: `msgspec.Struct` with strict unknown-field rejection at the top level; strategy-specific values live in `params: dict`
- ✅ Backwards compat: **none** — flags deleted in this PR, no deprecation window. User directive: "no fallbacks."
