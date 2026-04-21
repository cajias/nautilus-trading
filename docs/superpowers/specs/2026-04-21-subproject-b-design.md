# Sub-project B — Paper-trade testbed (Design)

**Status:** Approved 2026-04-21.
**Supersedes:** Original sub-project B scope in `docs/superpowers/roadmap.md` (real-money Binance live trading — deferred indefinitely; not tracked in any GH issue).
**Tracked in:** [#24](https://github.com/cajias/nautilus-trading/issues/24).
**Follow-on issues:** [#25](https://github.com/cajias/nautilus-trading/issues/25) (sub-project C), [#26](https://github.com/cajias/nautilus-trading/issues/26) (Futures Testnet).

---

## §1 Goal

Run each of the 8 registered `Strategy` subclasses against:
1. **Historical data** via `nt backtest` (already shipped in sub-project A, unchanged).
2. **Binance Spot Testnet paper trade** via `nt paper-trade` (new).

Same strategy class, same `StrategyConfigBuilder`, two execution paths. No real money, ever.

**Primary use case:** evaluate competition `Strategy` subclasses (future sub-project C) against both historical and live-ish conditions with identical code.

## §2 Non-goals

- **Real-money Binance trading.** Never in this sub-project. Would require a separate brainstorm if ever revisited.
- **Binance USDT Futures Testnet.** Deferred to #26; B ships Spot-only.
- **Production hardening:** reconnect/backoff policies beyond Nautilus defaults, external alerting (PagerDuty/Slack), PnL persistence across restarts, health-check endpoints.
- **Catalog replay driving a live node.** Paper trade uses live Binance Testnet market data; deterministic replay is out of scope.
- **Competition submission contract / leaderboard.** Deferred to sub-project C (#25).

## §3 Architecture

Parallel runner hierarchy mirroring sub-project A:

```
strategies/crypto/*.py                 (Strategy subclasses — unchanged from A)
         │
         ├─ STRATEGY_BUILDERS          (A: Protocol-based config-dict builder registry)
         └─ _STRATEGY_CLASSES          (A: module-name → (StrategyClass, ConfigClass) map)
                │
      ┌─────────┴──────────┐
      │                    │
BacktestRunner        PaperTradeRunner
  (A, existing)         (B, new ABC)
      │                    │
BacktestEngine        TradingNode
SimulatedVenue        Binance Spot Testnet
      │                    │
  nt backtest          nt paper-trade
                       [--duration 30m optional]
```

Key invariant: the `strategy_config` dict emitted by `STRATEGY_BUILDERS[name].build(args)` is identical for backtest and paper-trade. Only the outer runner composes differently.

## §4 Module layout (new)

All paths relative to the worktree root.

```
nautilus/src/nautilus_trading/paper_trade/
    __init__.py
    runner_base.py        # PaperTradeRunner ABC (abstract main — no default body)
    node_config.py        # build_paper_trade_node_config(...) — the 3 blocker fixes land here, ONCE
    secrets.py            # .env.local loader via python-dotenv

nautilus/src/nautilus_trading/cli/
    paper_trade.py        # Typer command wiring; shared arg surface with backtest.py

strategies/crypto/
    ema_cross_paper.py
    grid_bot_paper.py
    dca_bot_paper.py
    timesfm_swing_paper.py
    hybrid_sma_r10_paper.py
    timesfm_grid_paper.py
    rvs_swing_paper.py
    shock_guard_paper.py

strategies/crypto/kronos/
    paper_runner.py       # KronosPaperTradeRunner — replaces quarantined paper_trade.py

tests/paper_trade/
    test_node_config.py   # blocker-fix regression tests
    test_secrets.py       # .env.local loader
    test_runner_base.py   # ABC behavior
    test_smoke_paper.py   # @pytest.mark.binance_testnet — node-boot smoke for all 8

docs/runbooks/
    paper-trade.md        # start / stop / panic-close / key setup
```

**File placement convention:** flat (`strategies/crypto/<name>_paper.py`) for the 7 single-file strategies. Kronos keeps its existing subdir because `actor.py`, `strategy.py`, `backtest.py`, `backtest_config.py` already live there.

## §5 CLI surface

```
nt paper-trade --strategy ema_cross \
               --instrument-id BTCUSDT.BINANCE \
               --bar-type BTCUSDT.BINANCE-1-MINUTE-LAST-EXTERNAL \
               --trade-size 0.001 \
               [--duration 30m]
```

- Strategy-specific args (e.g. `--upper-price`, `--fast-ema`) come from the existing `STRATEGY_BUILDERS[name]` contract — no new builders.
- `--duration` is optional. If omitted, the node runs until SIGINT/SIGTERM. If provided, a background timer triggers `node.stop()` after the interval; the same graceful-shutdown path handles both.

## §6 Secrets flow

`.env.local` (gitignored; `.env.example` template committed) provides:

```
BINANCE_TESTNET_API_KEY=...
BINANCE_TESTNET_API_SECRET=...
BINANCE_TESTNET_ED25519_KEY_PATH=/abs/path/to/ed25519_private.pem
```

- Typer root callback calls `paper_trade.secrets.load_dotenv_local()` **before** any Binance config construction.
- Missing `BINANCE_TESTNET_ED25519_KEY_PATH` or an unreadable PEM → CLI exits with an actionable error that names the variable and the expected path format.
- Production-scale key management (rotation, vault) is out of scope — Testnet secrets only.

## §7 The three Testnet blocker fixes

All three land in `paper_trade/node_config.py`, shared by every `PaperTradeRunner` subclass.

### §7.1 Ed25519 signing for user-data WebSocket

Binance Spot's user-data stream uses Ed25519 signing; HMAC does not work for that channel (documented failure mode, 2026-04-08 incident). `build_paper_trade_node_config()` reads `BINANCE_TESTNET_ED25519_KEY_PATH`, loads the PEM, and passes it to `BinanceDataClientConfig` / `BinanceExecClientConfig` via the adapter's key-loading hook.

**Regression test:** `test_node_config_loads_ed25519_key` — construct config with a fixture PEM, assert no exception.

### §7.2 InstrumentProvider must explicitly load target symbols

Nautilus's default `InstrumentProviderConfig()` is empty — no instruments loaded, so submitted orders fail with "unknown instrument" (2026-04-08 incident). Fix: `build_paper_trade_node_config()` constructs an `InstrumentProviderConfig` explicitly configured to load the target symbol(s) for the run. Verify the exact attribute name against `nautilus_trader.adapters.binance` during implementation — the API surface has changed in recent Nautilus versions.

**Regression test:** `test_node_config_loads_instrument` — construct a config with `instrument_id="BTCUSDT.BINANCE"`, then assert the produced provider config declares that instrument for loading (exact assertion form chosen during implementation based on current API).

### §7.3 Tick-size rounding for LIMIT orders

Binance rejects orders with prices not on the instrument's tick grid (2026-04-08 incident with grid_bot). Fix: `paper_trade.node_config.round_to_tick(price: Decimal, instrument: Instrument) -> Price` helper. Strategies submitting LIMIT orders must call it before `submit_order()`.

**Regression test:** `test_round_to_tick_preserves_grid` — round-trip assertion across several tick sizes (0.01, 0.001, 0.00001).

**Strategy-side change:** `grid_bot` is the known case that failed on 2026-04-08; its strategy file needs the fix. Audit the other 7 strategies during implementation — any that construct LIMIT order prices arithmetically should defensively call `round_to_tick()` before submit. In backtest mode the call is a no-op (SimulatedVenue tolerates any price); in paper-trade it's required. Market orders are unaffected.

## §8 Runner ABC

```python
# nautilus/src/nautilus_trading/paper_trade/runner_base.py

from abc import ABC, abstractmethod

class PaperTradeRunner(ABC):
    """Base class for Binance Spot Testnet paper-trade runners.

    Parallel to BacktestRunner (sub-project A). Every concrete subclass wires
    its own TradingNode composition — no default main() body (see PR #16 / sub-project A).
    """

    @abstractmethod
    def main(self) -> None:
        """Compose TradingNode, subscribe data, add strategy, run."""
```

No default `main()` body. Each subclass wires its own `TradingNode` + venue + strategy — same discipline we applied to `BacktestRunner` in PR #16.

## §9 Testing

### §9.1 CI (opt-in marker)

`pytest -m binance_testnet` runs node-boot smoke for all 8 strategies. Not run by default since Testnet occasionally flaps. When run, each strategy boots a `TradingNode` against Binance Spot Testnet, subscribes to its target bar type, lets the event loop spin for ~30 seconds, asserts no exception raised and at least one market-data event (bar or tick) received, and shuts down cleanly. No order-path assertion — that's §9.2.

### §9.2 Manual forced-order smoke

`make smoke-paper-order STRATEGY=<name>` target. Boots the runner's node, then **bypasses the strategy** and submits one small LIMIT order directly via the node's exec client (using the runner's `instrument_id` + a deliberately-off-market price so it won't fill). Asserts: order-submit ACK received from Binance, order visible in the exec client's open-orders view. Cancels the order and shuts down cleanly.

This deliberately doesn't exercise strategy-specific signal logic — its job is "does the runner's order path work?" Strategy-correctness is covered by each strategy's backtest unit tests.

Not in CI; run before tagging a release or onboarding a new strategy.

### §9.3 Unit tests

- `test_secrets.py` — `.env.local` loader (missing file, missing var, happy path).
- `test_node_config.py` — the 3 blocker-fix regression tests from §7.
- `test_runner_base.py` — ABC behavior (can't instantiate without `main()` override).

## §10 Kronos migration

`strategies/crypto/kronos/paper_trade.py` (quarantined in sub-project A) is replaced by `strategies/crypto/kronos/paper_runner.py` implementing `KronosPaperTradeRunner(PaperTradeRunner)`. `KronosStrategy` and `KronosActor` are unchanged.

**Parity gate:** a dedicated test asserts the new runner's `TradingNodeConfig` matches the old `paper_trade.py` on these fields:
- `account_type` (must be `BinanceAccountType.SPOT`)
- `environment` (must be `BinanceEnvironment.TESTNET`)
- `venue` name (must be `"BINANCE"`)
- strategy + actor classes attached (`KronosStrategy`, `KronosActor`)
- configured symbol (from CLI args)

After the parity test passes, the old `strategies/crypto/kronos/paper_trade.py` is deleted in the same PR.

## §11 Documentation

- `docs/runbooks/paper-trade.md` — operator-focused:
  - One-time Testnet account setup + Ed25519 key generation commands
  - `.env.local` template
  - Start / stop / panic-close (cancel all open orders on Binance Testnet)
  - Troubleshooting (401s, empty instrument provider, tick-grid rejections)
- `docs/superpowers/roadmap.md` — update sub-project B scope paragraph to reflect the paper-trade-only pivot; reference this spec.
- `CLAUDE.md` — brief "Paper trading" section mirroring the "Backtesting" section.

## §12 PR slicing

1. **Foundation:** `PaperTradeRunner` ABC + `secrets.py` loader + `.env.example` + `.gitignore` comment. Small, reviewable, zero runtime risk.
2. **`node_config.py` + 3 blocker fixes** with unit tests. Most important PR — this is where the 2026-04-08 pain goes to die.
3. **`nt paper-trade` CLI + `EMACrossPaperTradeRunner`.** First concrete runner; validates the end-to-end pattern on the simplest strategy.
4. **Simple directional runners:** `grid_bot_paper.py`, `dca_bot_paper.py`, `timesfm_swing_paper.py`. All share the "subscribe bars → strategy emits orders" shape. Batched because the wiring is near-identical per strategy.
5. **Composite / ML runners:** `hybrid_sma_r10_paper.py`, `timesfm_grid_paper.py`, `rvs_swing_paper.py`, `shock_guard_paper.py`. Likely the trickiest per-runner wiring (Actor + Strategy pairs, ML model loading).
6. **Kronos migration + parity test**; delete quarantined `paper_trade.py`.
7. **CI smoke marker + `docs/runbooks/paper-trade.md` + roadmap update.**

(7 PRs — one more than my verbal estimate; moving CI + docs out of PR 6 to keep commits focused.)

## §13 Open follow-ons (not in B)

- Binance USDT Futures Testnet — #26.
- Competition submission contract + leaderboard — sub-project C, #25.
- Unattended operation hardening — no issue yet; open if/when needed.
- Real-money live trading — no issue and no plan.

## §14 Decisions log (per brainstorm 2026-04-21)

| # | Question | Decision |
|---|----------|----------|
| 1 | Sub-project B shipping target | Paper-trade testbed only, no real money |
| 2 | CLI shape | Two commands (`nt backtest`, `nt paper-trade`), shared registries |
| 3 | Strategy scope | All 8 registered strategies must pass Testnet smoke |
| 4 | Venue coverage | Spot Testnet only; Futures follow-on (#26) |
| 5 | Runner architecture | Parallel `PaperTradeRunner` ABC next to `BacktestRunner` |
| 6 | Secrets handling | `.env.local` loaded via `python-dotenv` |
| 7 | Smoke test bar | A (node-boot) in CI for all 8; B (forced-order) manual per strategy |
| 8 | Run lifecycle | Continuous by default; `--duration` optional time-box |
| 9 | File placement | Flat `strategies/crypto/<name>_paper.py`; Kronos stays in its subdir |
