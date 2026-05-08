---
description: List strategies registered with the nt CLI (in-repo + entry-point external).
---

Run `nt strategies` to enumerate every strategy the registry sees,
sourced from the `nautilus_trading.strategies` entry-point group.

```bash
cd nautilus && uv run nt strategies
```

If the table is empty or your strategy is missing, consult the
`nt-cli-quickstart` skill ("Discovery is empty" section). Most common
cause: a stale long-running `nt` / notebook / REPL process — open a
fresh shell and re-run.
