---
description: Run a backtest with the nt CLI for a registered strategy.
argument-hint: "<config-path>"
---

Run a backtest using the YAML config in `$ARGUMENTS` (path relative to
`nautilus/`, typically a file under `configs/backtest/<name>.yaml`).

Canonical invocation:

```bash
cd nautilus && uv run nt backtest --config $ARGUMENTS
```

The legacy `--strategy <name>` path still works but emits a
`DeprecationWarning` and is slated for removal in sub-project B.5 PR 4 —
new code should use `--config` so the strategy + parameters travel as
a single committed artifact.

Run `/nt-strategies` first to see the registered names — they must
match `STRATEGY_SPEC.name` byte-for-byte for the dispatcher to find
your config's `strategy:` field.
