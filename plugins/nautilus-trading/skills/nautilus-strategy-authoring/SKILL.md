---
name: nautilus-strategy-authoring
description: Authoring contract for NautilusTrader strategies pluggable into the cajias/nautilus-trading `nt` CLI. Use when the user asks to "write a new strategy", "author a strategy for nautilus", says "STRATEGY_SPEC", "register a strategy", "external strategy", "build an algo for nt", or wants to port a backtest into the registry. Covers the StrategySpec / ImportableStrategyConfig / msgspec-frozen StrategyConfig contract, entry-point registration via pyproject.toml, plus the eight bug landmines (RSI [0,1] scale, bar-close vs intra-bar exits, Binance MARKET+IOC, BarFanoutActor for shared bar_type, monetary-string parsing, indicator imports, signal-handler unsafety, frozen config defaults).
---

# Authoring a Strategy for the `nt` Registry

This skill is the contract for adding a strategy that
[`cajias/nautilus-trading`](https://github.com/cajias/nautilus-trading)'s
`nt` CLI can pick up — either inside the repo at
`strategies/<market_type>/` or in an external package installed via
`uv pip install --editable .`.

The contract is **identical** in both cases: expose a `STRATEGY_SPEC`,
register it under the `nautilus_trading.strategies` entry-point group,
and provide importable `Strategy` + `StrategyConfig` classes at the
paths the spec references.

## The four-piece contract

### 1. `StrategySpec`

Every authoring path imports the public surface from
`nautilus_trading.specs`:

```python
from nautilus_trading.specs import StrategySpec
```

Never import from `nautilus_trading.cli._strategy_specs` — that module is
private discovery glue (the underscore prefix is a deliberate "do not
depend on this" signal).

`StrategySpec` is a frozen dataclass with five fields:

```python
@dataclass(frozen=True)
class StrategySpec:
    name: str                              # CLI/YAML key
    builder: ConfigBuilder                 # parsed-args -> kwargs dict
    strategy_path: str                     # "pkg.mod:ClassName"
    config_path: str                       # "pkg.mod:ConfigName"
    actor_specs: tuple[ActorSpec, ...] = ()
```

The `name` is what users type after `--strategy` and what YAML configs
put in their `strategy:` field. It must match the entry-point key in
`pyproject.toml` byte-for-byte; mismatch raises `RuntimeError` at
discovery.

### 2. `ConfigBuilder`

A `ConfigBuilder` maps a parsed-args dict (CLI flags or YAML body) to a
kwargs dict that goes straight into `ImportableStrategyConfig.config`.
The Protocol is one method:

```python
class ConfigBuilder(Protocol):
    def build(self, args: dict[str, Any]) -> dict[str, Any]: ...
```

Raise `ValueError` for missing or invalid fields; the CLI dispatcher maps
that uniformly to `typer.BadParameter`.

```python
class MyStrategyConfigBuilder:
    def build(self, args: dict[str, Any]) -> dict[str, Any]:
        if not args.get("instrument_id") or not args.get("bar_type"):
            raise ValueError("my_strategy requires instrument_id and bar_type")
        return {
            "instrument_id": args["instrument_id"],
            "bar_type": args["bar_type"],
            "fast_ema_period": args.get("fast_ema_period", 10),
            "slow_ema_period": args.get("slow_ema_period", 20),
        }
```

### 3. `StrategyConfig` (msgspec frozen) and `Strategy`

```python
from typing import Any
from nautilus_trader.config import StrategyConfig
from nautilus_trader.indicators import ExponentialMovingAverage
from nautilus_trader.model.data import BarType
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.trading.strategy import Strategy


class MyStrategyConfig(StrategyConfig, frozen=True):
    instrument_id: InstrumentId
    bar_type: BarType
    fast_ema_period: int = 10
    slow_ema_period: int = 20


class MyStrategy(Strategy):
    def __init__(self, config: MyStrategyConfig) -> None:
        super().__init__(config)
        self.fast_ema = ExponentialMovingAverage(config.fast_ema_period)
        self.slow_ema = ExponentialMovingAverage(config.slow_ema_period)

    def on_start(self) -> None:
        self.register_indicator_for_bars(self.config.bar_type, self.fast_ema)
        self.register_indicator_for_bars(self.config.bar_type, self.slow_ema)
        self.subscribe_bars(self.config.bar_type)

    def on_bar(self, bar) -> None:
        if not self.indicators_initialized():
            return
        # ... your signal logic — see "bar-close vs intra-bar" below ...
```

`StrategyConfig` MUST be `frozen=True`. The base class uses msgspec
Structs, and freezing is the only way the registry can hash and dedupe
configs.

### 4. Entry-point registration in `pyproject.toml`

In your package's `pyproject.toml`:

```toml
[project.entry-points."nautilus_trading.strategies"]
my_strategy = "my_pkg.my_module:STRATEGY_SPEC"
```

Then in `my_pkg/my_module.py`, expose the constant:

```python
STRATEGY_SPEC = StrategySpec(
    name="my_strategy",
    builder=MyStrategyConfigBuilder(),
    strategy_path="my_pkg.my_module:MyStrategy",
    config_path="my_pkg.my_module:MyStrategyConfig",
)
```

The entry-point key (`my_strategy`) must equal `STRATEGY_SPEC.name`
byte-for-byte. After `uv pip install --editable .`, run
`uv run nt strategies` and you'll see `my_strategy` listed alongside the
in-repo strategies, with your package as the source label.

For the in-repo authoring path, drop the file under
`strategies/<market_type>/<name>.py` and add it to the in-repo
`pyproject.toml`'s entry-point table. See `strategies/forex/ema_cross.py`
for the canonical worked example.

## The eight bug landmines

These bite if you don't know them. Each is sourced from a real production
incident.

### 1. `RelativeStrengthIndex.value` is in `[0.0, 1.0]`, not `[0, 100]`

```python
# WRONG — never fires:
if self.rsi.value > 55:
    self.submit_order(...)

# RIGHT:
if self.rsi.value > 0.55:
    self.submit_order(...)
```

Same gotcha for `RateOfChange` (returns a fraction). Always verify
indicator scale by reading the source or print-debugging — the docs are
inconsistent.

### 2. Indicator imports come from the package root

```python
# WRONG — these submodules don't exist as importable paths:
from nautilus_trader.indicators.rsi import RelativeStrengthIndex
from nautilus_trader.indicators.average import ExponentialMovingAverage

# RIGHT:
from nautilus_trader.indicators import (
    ExponentialMovingAverage,
    RelativeStrengthIndex,
    BollingerBands,
    AverageTrueRange,
)
```

The package root re-exports every indicator class. LLM-generated
strategies often pick the wrong path; the file compiles fine but raises
`ModuleNotFoundError` at import.

### 3. Bar-close vs intra-bar exits on 1H+ bars

```python
# WRONG on 1H+ bars — `bar.high` and `bar.low` are wick prices that fired
# during the bar; a MARKET order from on_bar fills at *next-bar-open*, so
# you get 1-3% slippage per stop-out.
if bar.high >= take_profit or bar.low <= stop_loss:
    self.close_all_positions(...)

# RIGHT — evaluate on bar.close, accept missed wicks as the cost of doing
# business at this aggregation:
if bar.close >= take_profit or bar.close <= stop_loss:
    self.close_all_positions(...)
```

For *real* intra-bar triggers, post a venue-side `STOP-MARKET` order
ahead of time rather than evaluating `bar.high`/`bar.low` after the fact.

### 4. Binance Spot rejects `MARKET` + `GTC`; use `IOC`

The backtest engine doesn't enforce this — the bug only surfaces at
paper-trade or live time. When building a market order destined for
Binance Spot, set `time_in_force=TimeInForce.IOC`. See
`strategies/crypto/shock_guard.py` in the repo for the pattern.

### 5. Multiple strategies sharing a `bar_type` need a `BarFanoutActor`

`TradingNodeConfig.strategies=[s1, s2, ...]` with a shared `bar_type`
silently delivers bars only to the FIRST subscriber's `on_bar`; the rest
stay empty forever. This is a NautilusTrader dispatch bug. The repo's
workaround is `nautilus_trading.paper_trade.BarFanoutActor` — attach it
as an actor and have each strategy subscribe to a fanned-out
`bar_type`. See `nautilus_trading/paper_trade/` for the wiring pattern.

### 6. Frozen `StrategyConfig` defaults vs. spec-builder defaults

```python
class MyConfig(StrategyConfig, frozen=True):
    fast_ema_period: int = 10   # class-level default
```

If your spec builder also has `args.get("fast_ema_period", 12)`, the
**builder's default wins** — the kwarg is always passed, so the
class-level default never fires. Either keep them in sync, or make the
spec builder the single source of truth and drop the class-level default.

### 7. Signal handlers around `TradingNode` must not call engine queries

Python signal handlers must NOT directly call `engine.portfolio` or
`engine.trader` queries — NautilusTrader's C/Cython main loop doesn't
yield to Python's signal queue safely, and the process crashes with
exit 1 / no traceback. Pattern: signal handler does `event.set()`; a
daemon Python thread does the actual query work.

### 8. Account-report monetary columns are strings, not floats

```python
# WRONG — TypeError or wrong sort order:
df["realized_pnl"].sum()

# RIGHT — strip currency suffix first:
df["realized_pnl_num"] = (
    df["realized_pnl"]
    .str.replace(r"\s+\w+$", "", regex=True)
    .astype(float)
)
```

`realized_pnl` returns `"-9.48 USD"`, not a float. Same for `commissions`
and any column produced by `engine.portfolio.analyzer` that carries a
currency unit.

## Verifying

After authoring:

```bash
cd nautilus
uv run nt strategies                    # confirm the new name appears
uv run nt backtest --strategy <name>   # smoke the import + signal path
```

`--help` does NOT exercise discovery — invoking `--strategy` does. Don't
let a passing `--help` smoke convince you the strategy is wired.

## Reference paths in the repo

- Public spec types: `nautilus/src/nautilus_trading/specs.py`
- Discovery glue (private): `nautilus/src/nautilus_trading/cli/_strategy_specs.py`
- In-repo strategy entry-points: `nautilus/pyproject.toml`
  (`[project.entry-points."nautilus_trading.strategies"]`)
- Canonical examples:
  - `strategies/forex/ema_cross.py` — minimal worked example
  - `strategies/crypto/shock_guard.py` — `MARKET + IOC` pattern
  - `strategies/crypto/hybrid_sma_r10.py` — pandas-port reference
- External-strategy runbook: `docs/runbooks/external-strategies.md`
- Test fixture mirroring an external package:
  `tests/cli/_external_strategy_fixture/`

## See also

- The companion `nt-cli-quickstart` skill covers running the strategy
  once the registry sees it.
- The `strategy-author` agent in this plugin orients on these landmines
  before any strategy code is written.
