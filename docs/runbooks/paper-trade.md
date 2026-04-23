# Paper-trade runbook

Operator-facing guide for running strategies against Binance Spot Testnet via
`nt paper-trade`. Sub-project B ships the runners; this document covers how to
boot them, stop them, and recognise the failure modes that crop up during
pre-release smoke.

## 1. Prerequisites

- **Python 3.12+** and `uv` (see root `CLAUDE.md` for the venv layout).
- **Binance Testnet account** — sign up at <https://testnet.binance.vision/>.
  Testnet funds are free and reset periodically.
- **Ed25519 key pair** — required for the user-data WebSocket (see PR 2 §7.1).
  Generate locally:

  ```bash
  openssl genpkey -algorithm ed25519 -out binance_testnet_ed25519_private.pem
  openssl pkey -in binance_testnet_ed25519_private.pem -pubout -out binance_testnet_ed25519_public.pem
  ```

- **Register the public key** on the Binance Testnet UI under
  *API Management → Create API → Ed25519*. Paste the contents of
  `binance_testnet_ed25519_public.pem`. The returned "API key ID" is what goes
  into `BINANCE_TESTNET_API_KEY`.

## 2. `.env.local` template

The app auto-loads `.env.local` at the repo root via `python-dotenv`. Copy
`/.env.example` and fill in the Binance Testnet section:

```
BINANCE_TESTNET_API_KEY=<api-key-id-from-binance>
BINANCE_TESTNET_API_SECRET=<unused-for-ed25519-but-preflight-wants-it>
BINANCE_TESTNET_ED25519_KEY_PATH=/absolute/path/to/binance_testnet_ed25519_private.pem
```

Notes:

- `BINANCE_TESTNET_API_SECRET` is checked for presence by
  `_check_testnet_api_keys()` in `paper_trade/node_config.py` even though
  Ed25519 does not actually use it. A placeholder string is fine; do not commit
  real secrets.
- `.env.local` is git-ignored. `.env.example` is the canonical field list — if
  in doubt, diff against that file.
- The PEM path **must be absolute** and readable by the user running `uv`.

## 3. Starting a paper-trade run

Pre-baked configs live under `configs/paper/`:

```
$ ls configs/paper/
dca_bot.yaml       hybrid_sma_r10.yaml  shock_guard.yaml    timesfm_grid.yaml
ema_cross.yaml     rvs_swing.yaml       timesfm_swing.yaml  grid_bot.yaml
```

Eight runners are wired through the CLI today. Kronos runs through the legacy
`make paper-trade-kronos` target until task #42 lands
`configs/paper/kronos.yaml`.

Start a run:

```bash
cd nautilus && uv run nt paper-trade --config ../configs/paper/ema_cross.yaml
```

The `TradingNode` boots, the Binance Spot Testnet clients authenticate,
instruments load via `BinanceInstrumentProviderConfig.load_ids`, the strategy
subscribes to its configured bar type, and bars start flowing. Typical boot
latency is 5–15 seconds; first bar typically arrives within 30 seconds.

## 4. Stopping a run

`run_paper_trade()` installs handlers for both signals:

| Signal                       | Behaviour                                                 |
| ---------------------------- | --------------------------------------------------------- |
| `Ctrl-C` (SIGINT)            | Graceful stop — node stops + disposes cleanly             |
| `kill -TERM <pid>` (SIGTERM) | Same graceful path as SIGINT                              |
| `kill -KILL <pid>`           | Hard kill — Binance session dangles up to ~60s their side |

Prefer SIGINT/SIGTERM. Hard-killing leaves an open session that can block the
next boot on the same API key until Binance times it out.

## 5. Panic-close

There is no operator-invoked panic endpoint in sub-project B — that lands in
sub-project C. If you need to flatten immediately:

1. Stop the node (SIGINT).
2. Cancel open orders and close positions manually via the Binance Testnet UI.

Do **not** rely on the node's `on_stop()` to flatten for you — the crypto
runners shipped in B are intentionally minimal and leave exit logic to the
strategy's own `on_stop()` where it exists.

## 6. Pre-release smoke (opt-in)

Before a Binance deploy, run the node-boot smoke for all 9 runners:

```bash
cd nautilus && uv run python -m pytest ../tests/paper_trade/test_smoke_paper.py -v
```

Each runner boots a real `TradingNode` against Binance Spot Testnet and must
receive at least one `Bar` within 30 seconds. The suite is gated behind the
`binance_testnet` pytest marker (registered in PR 8.2) so normal `make test`
runs skip it.

Required env vars (auto-loaded from `.env.local`, or exported manually):

- `BINANCE_TESTNET_API_KEY`
- `BINANCE_TESTNET_API_SECRET`
- `BINANCE_TESTNET_ED25519_KEY_PATH`

For the order-path smoke (submits + cancels a single off-market LIMIT so no
fill is possible):

```bash
make smoke-paper-order STRATEGY=ema_cross
```

This is the order-submission canary — exercise it before promoting a runner to
a new instrument.

## 7. Common errors

Each entry: **Symptom → Cause → Fix**.

### `BINANCE_TESTNET_ED25519_KEY_PATH not found` / not readable

- **Symptom:** Boot aborts with `FileNotFoundError` or `PermissionError` before
  the TradingNode starts.
- **Cause:** `_check_testnet_api_keys()` in `paper_trade/node_config.py`
  validates the PEM path up front.
- **Fix:** Confirm the path is absolute, the file exists, and the user running
  `uv` can read it. Regenerate the pair if the PEM is corrupted.

### HTTP 401 on boot

- **Symptom:** `BinanceClientError: status 401` during InstrumentProvider load
  or first WebSocket auth.
- **Cause:** API key is not registered, or the public Ed25519 key stored on
  Binance does not match the private key at `BINANCE_TESTNET_ED25519_KEY_PATH`.
- **Fix:** Regenerate the pair, re-upload the public PEM on the Binance
  Testnet UI, update the env vars.

### "Unknown instrument" in logs

- **Symptom:** Strategy starts but never receives bars; log line
  `Unknown instrument <ID>`.
- **Cause:** `BinanceInstrumentProviderConfig.load_ids` does not include the
  exact `instrument_id` the strategy subscribes to. Case matters —
  `btcusdt.BINANCE` will not resolve; `BTCUSDT.BINANCE` will.
- **Fix:** Copy the `instrument_id` verbatim from a committed YAML in
  `configs/paper/`.

### "Price not on tick grid" order rejection

- **Symptom:** `submit_order` fails with a tick/price-filter rejection from the
  venue.
- **Cause:** LIMIT price is not a multiple of the instrument's
  `price_increment`. Strategies that construct limit prices arithmetically
  must snap to the grid.
- **Fix:** Route every synthetic LIMIT price through
  `round_to_tick()` exported from `nautilus_trading.paper_trade.node_config`.
  See the PR 2 incident log in `docs/superpowers/audits/` for the original
  blocker.

## 8. Where to look next

- **Strategy configs:** `configs/paper/*.yaml`
- **Runner implementations:**
  `strategies/crypto/*_paper.py` plus `strategies/crypto/kronos/paper_runner.py`
- **Core wiring:** `nautilus/src/nautilus_trading/paper_trade/`
- **CLI command:** `nautilus/src/nautilus_trading/cli/paper_trade.py`
- **Smoke suite:** `tests/paper_trade/test_smoke_paper.py`
- **Related doc:** root `CLAUDE.md` → *Paper trading* and *Live Trading
  (Binance)* sections.
