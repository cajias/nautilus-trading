---
description: Run LIVE trading on Binance with the nt CLI. Real money. Read the safety preamble before invoking.
argument-hint: "<config-path>"
---

> ⚠️ **STATUS: STUB** — `nt live` currently raises `NotImplementedError` per
> the 2026-04-21 no-real-money directive. The 8-item safety preamble below
> is forward-looking; this command exists to enforce the gate before real
> funds are wired in. Running `nt live --config <path>` today will exit
> with a stack trace, not place orders. Do not invoke until the directive
> is lifted and the implementation is filled in (see
> `nautilus/src/nautilus_trading/cli/live.py:116`).

# WARNING — LIVE TRADING ON BINANCE

This command places real orders on Binance with real money. Do NOT run
it before satisfying every item below. The same code path runs in
`/nt-paper` against the testnet — verify there first.

## Safety preamble

Before invoking, walk the operator through this checklist explicitly,
and require an unambiguous confirmation in the affirmative for each:

- [ ] **Paper-trade smoke is green.** The strategy at `$ARGUMENTS` ran
      cleanly on Binance Spot Testnet (`/nt-paper`) for at least one
      session within the last 7 days, with logs reviewed.
- [ ] **Environment is LIVE on purpose.** `BINANCE_API_KEY` and
      `BINANCE_API_SECRET` are set to live keys (not testnet keys). The
      operator typed the value or pasted from a password manager —
      not auto-loaded from a stale shell.
- [ ] **Account balance is intentional.** The Binance account holds
      only the capital the operator is willing to lose on this run.
      Excess balance has been withdrawn to a separate wallet.
- [ ] **Position sizing in the YAML is sane.** Open
      `$ARGUMENTS` and read the `params:` block aloud. Confirm
      max-position-size and per-trade risk percentages.
- [ ] **Stop conditions are configured.** The strategy has explicit
      stop-loss / max-drawdown logic, OR the operator commits to
      monitoring continuously and killing the process via Ctrl+C.
- [ ] **Kill switch is at hand.** A second terminal is open with
      `pkill -f "nt live"` ready, AND the operator has tested SIGTERM
      shutdown in the testnet session.
- [ ] **Network and clock.** Machine has stable internet and NTP-synced
      clock (Binance rejects requests with stale timestamps).
- [ ] **Logging destination.** Logs are being written to a durable
      location (not just terminal scrollback).

If ANY checkbox is unconfirmed, refuse to invoke. Tell the operator
which item failed and stop.

## Invocation

After every item above is confirmed:

```bash
cd nautilus && uv run nt live --config $ARGUMENTS
```

The `nautilus_trading.cli.live` module installs SIGINT/SIGTERM handlers
and shuts the `TradingNode` down cleanly on Ctrl+C. See
`docs/runbooks/paper-trade.md` for the operator-facing runbook (the live
runner shares the same code path with environment flipped to live keys).
