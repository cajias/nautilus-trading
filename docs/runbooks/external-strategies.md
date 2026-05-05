# External strategies — registering with `nt`

This repo discovers strategies via Python entry-points. To plug an external
strategy into `nt {backtest, paper-trade, live} --config ...` without
modifying this repo, your package needs three things: a `STRATEGY_SPEC`
constant, an entry-point in `pyproject.toml`, and importable
`Strategy` + `StrategyConfig` classes at the paths the spec references.

After `uv pip install --editable .` of your package, `nt strategies` lists
your strategy alongside the 9 in-repo ones.

## Contract

Your package must:

1. Expose a top-level `STRATEGY_SPEC = StrategySpec(...)` constant in some
   module.
2. Register that constant under the `nautilus_trading.strategies`
   entry-point group in your `pyproject.toml`.
3. Provide importable `Strategy` and `StrategyConfig` classes at the paths
   the spec references (`strategy_path` / `config_path`).
4. (Optional) For actor-bearing strategies, populate
   `STRATEGY_SPEC.actor_specs` with one or more `ActorSpec(...)` entries.

The entry-point key MUST match `STRATEGY_SPEC.name` byte-for-byte —
discovery in `cli/_strategy_specs.py` validates this and raises
`RuntimeError` on mismatch.

## Example

`my-strategies/pyproject.toml`:

```toml
[project]
name = "my-strategies"
version = "0.1.0"
requires-python = ">=3.12"

[project.entry-points."nautilus_trading.strategies"]
my_swing = "my_strategies.swing:STRATEGY_SPEC"

[build-system]
requires = ["setuptools>=68.0", "wheel"]
build-backend = "setuptools.build_meta"
```

`my-strategies/my_strategies/swing.py`:

```python
from typing import Any

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import BarType
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.trading.strategy import Strategy

from nautilus_trading.cli._strategy_specs import StrategySpec


class MySwingConfigBuilder:
    """Maps parsed CLI / YAML args -> a StrategyConfig kwargs dict."""

    def build(self, args: dict[str, Any]) -> dict[str, Any]:
        if not args.get("instrument_id") or not args.get("bar_type"):
            raise ValueError("my_swing requires instrument_id and bar_type")
        return {
            "instrument_id": args["instrument_id"],
            "bar_type": args["bar_type"],
        }


class MySwingConfig(StrategyConfig, frozen=True):
    instrument_id: InstrumentId
    bar_type: BarType


class MySwingStrategy(Strategy):
    def __init__(self, config: MySwingConfig) -> None:
        super().__init__(config)
    # ... your trading logic ...


STRATEGY_SPEC = StrategySpec(
    name="my_swing",
    builder=MySwingConfigBuilder(),
    strategy_path="my_strategies.swing:MySwingStrategy",
    config_path="my_strategies.swing:MySwingConfig",
)
```

## Installing

The repo is `uv`-managed; the `.venv` does NOT include `pip`, so use
`uv pip install` (not `python -m pip install`):

```bash
# Inside the nautilus-trading worktree's venv:
cd /path/to/my-strategies
uv pip install --editable .
```

> **Mid-process caveat.** Editable installs activate via a `.pth`-loaded
> meta-path finder that runs at `site.py` init — i.e. only when the
> interpreter starts. If you've already got a long-running `nt` process
> open (a notebook, REPL, etc.), it won't see the new strategy until you
> restart it. Fresh-shell `nt` invocations pick the new finder up
> automatically.

## Verifying

From the nautilus worktree:

```bash
cd /path/to/nautilus-trading/nautilus
uv run nt strategies
# Expected: my_swing listed alongside the 9 in-repo strategies, with
# `(my-strategies)` as the source-package label.
```

Run a backtest / paper-trade against it:

```bash
uv run nt backtest    --config /path/to/configs/backtest/my_swing.yaml
uv run nt paper-trade --config /path/to/configs/paper/my_swing.yaml
```

The YAML's `strategy:` field must equal `STRATEGY_SPEC.name` (here:
`my_swing`).

## Failure modes

| Symptom | Cause | Fix |
|---|---|---|
| `nt strategies` doesn't list your strategy | Editable install missed the entry-point group, or you're inside a stale long-running process | Re-run `uv pip install --editable .`; verify with `python -c "import importlib.metadata; print([ep.name for ep in importlib.metadata.entry_points(group='nautilus_trading.strategies')])"` from a fresh shell |
| `RuntimeError: Duplicate strategy registration: 'X' declared by both 'A' and 'B'` | Two installed packages register the same strategy name | Uninstall one of them, or rename the duplicate's entry-point key + `STRATEGY_SPEC.name` |
| `RuntimeError: Entry-point name mismatch: 'pkg' registered the strategy as 'X' but its STRATEGY_SPEC.name is 'Y'` | Entry-point key in `pyproject.toml` doesn't match `STRATEGY_SPEC.name` | Make them equal byte-for-byte |
| `RuntimeError: Strategy '<name>' not found` at YAML load | YAML's `strategy:` field doesn't match any registered entry-point name | Check `nt strategies` for the expected name |
| Import error during discovery | Your module raises at import time | Fix the import-time error; entry-point loading is fail-fast — discovery errors crash `nt` startup with the failing entry-point named, so you can't miss them |

## See also

- `docs/runbooks/paper-trade.md` — operator-facing guide for `nt paper-trade`.
- `nautilus/src/nautilus_trading/cli/_strategy_specs.py` — `StrategySpec`,
  `ActorSpec`, and `_discover_strategy_specs()` source of truth.
- `tests/cli/_external_strategy_fixture/` — reference minimal external
  strategy package used by the smoke test (mirror this layout to bootstrap
  your own).
