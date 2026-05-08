---
description: Run a backtest with the nt CLI for a registered strategy.
argument-hint: "<strategy-name>"
---

Run a backtest for the strategy named in `$ARGUMENTS`. The name must
match a registered entry under `nt strategies` (e.g. `forex.ema_cross`,
`crypto.shock_guard`).

Use the project Makefile so the venv and dependency paths are right:

```bash
make backtest STRATEGY=$ARGUMENTS
```

Equivalent to:

```bash
cd nautilus && uv run nt backtest --strategy strategies.$ARGUMENTS
```

If the run fails with `Strategy '<name>' not found`, run `/nt-strategies`
first to see the registered names — the YAML / CLI key must match
`STRATEGY_SPEC.name` byte-for-byte.
