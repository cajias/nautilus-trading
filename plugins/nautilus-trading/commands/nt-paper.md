---
description: Run paper-trade against Binance Spot Testnet from a YAML config.
argument-hint: "<config-path>"
---

Run `nt paper-trade` with the YAML at `$ARGUMENTS`. The config's
`strategy:` field must match a registered name (run `/nt-strategies`
first to confirm).

```bash
cd nautilus && uv run nt paper-trade --config $ARGUMENTS
```

Required environment (Ed25519 keys, recommended by Binance):

```bash
export BINANCE_TESTNET_API_KEY="..."
export BINANCE_TESTNET_API_SECRET="$(cat binance_ed25519_private.pem)"
```

Full setup, including how to generate Ed25519 keys and seed the testnet
balance, is in `docs/runbooks/paper-trade.md`.

Press Ctrl+C to stop — the runner installs SIGINT/SIGTERM handlers and
shuts the `TradingNode` down cleanly.
