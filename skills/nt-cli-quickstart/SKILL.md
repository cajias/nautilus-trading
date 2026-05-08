---
name: nt-cli-quickstart
description: Quickstart for the `nt` CLI shipped by the cajias/nautilus-trading repo. Use when the user asks "how do I run nautilus", mentions the "nt CLI", says "list strategies", "run a backtest", "paper trade with nautilus", "what does nt strategies do", "nt backtest", "nt paper-trade", "nt live", or is bootstrapping a fresh clone of nautilus-trading and needs the canonical workflow. Covers `make install`, `nt strategies` discovery via the `nautilus_trading.strategies` entry-point group, running backtests, paper-trading on Binance Spot Testnet, and a "discovery is empty" troubleshooting path. The public spec types live in `nautilus_trading.specs` (StrategySpec, ActorSpec, ConfigBuilder).
---

# nt CLI Quickstart

The `nt` command is a Typer CLI shipped by the
[`cajias/nautilus-trading`](https://github.com/cajias/nautilus-trading) repo.
It exposes four subcommands — `strategies`, `backtest`, `paper-trade`, and
`live` — over a registry of in-repo strategies plus any external
strategies installed via Python entry-points.

This skill covers the canonical bootstrap-to-paper-trade workflow plus the
"my strategy didn't show up in `nt strategies`" troubleshooting path.

## Bootstrap

The repo is `uv`-managed. The venv lives in `nautilus/.venv/`.

```bash
git clone https://github.com/cajias/nautilus-trading.git
cd nautilus-trading
make install      # uv sync inside nautilus/, plus `pre-commit install`
```

`make install` is idempotent. After it succeeds, every other `make` target
and every `uv run nt ...` invocation is ready.

## The four subcommands

### 1. `nt strategies` — list registered strategies

```bash
cd nautilus
uv run nt strategies
```

Prints the table of strategies discovered via entry-points (in-repo +
installed external packages). Each row shows the registered name, the
source-package label, and the import paths.

This is the **first command to run** when bootstrapping. If the table is
empty or missing your strategy, see "Discovery is empty" below.

### 2. `nt backtest` — run a backtest

```bash
# Canonical: YAML config carries strategy + params as one artifact.
cd nautilus
uv run nt backtest --config ../configs/backtest/ema_cross.yaml
```

The legacy `--strategy <module>` path still works but emits a
`DeprecationWarning` and is slated for removal in sub-project B.5 PR 4.
Prefer `--config` so backtest setups are committed and reproducible.

Loads sample EUR/USD tick data (auto-downloaded from
`nautechsystems/nautilus_data`) and runs the backtest. Prints fills, P&L,
and the venue/account reports.

### 3. `nt paper-trade` — Binance Spot Testnet

```bash
cd nautilus
uv run nt paper-trade --config ../configs/paper/ema_cross.yaml
```

One YAML per strategy under `configs/paper/`. The YAML's `strategy:` field
must equal the registered strategy name (use `nt strategies` to confirm).

To add a new strategy-config, duplicate an existing YAML and edit the
`strategy:` key + per-strategy `params:`.

Required env vars (Ed25519 keys, recommended by Binance):

```bash
export BINANCE_TESTNET_API_KEY="<api-key-id-from-binance>"
export BINANCE_TESTNET_API_SECRET="<placeholder>"   # presence-checked only
export BINANCE_TESTNET_ED25519_KEY_PATH="/absolute/path/to/binance_testnet_ed25519_private.pem"
```

The PEM contents are NOT placed in `BINANCE_TESTNET_API_SECRET` — that
variable is presence-checked only. The actual Ed25519 signing key is
loaded from the file at `BINANCE_TESTNET_ED25519_KEY_PATH`.

See `docs/runbooks/paper-trade.md` in the repo for the full setup
(generating Ed25519 keys, seeding testnet balance, troubleshooting).

### 4. `nt live` — Binance live trading

```bash
cd nautilus
uv run nt live --config ../configs/live/<your_strategy>.yaml
```

Real money. Read `docs/runbooks/paper-trade.md` end-to-end first; the live
runner is the same code path with environment flipped to live keys. Run a
paper-trade smoke first.

## Discovery is empty (or your strategy is missing)

`nt strategies` discovers strategies via the
`nautilus_trading.strategies` entry-point group, populated by
`importlib.metadata.entry_points()`. Discovery happens lazily on first
access.

If your strategy isn't listed:

1. **Stale long-running process.** Editable installs activate via a
   `.pth`-loaded meta-path finder that runs at `site.py` init — only when
   the interpreter starts. A long-running `nt` process / notebook / REPL
   won't see a freshly-installed external strategy until you restart the
   interpreter. Open a fresh shell and re-run `uv run nt strategies`.

2. **Editable install missed the entry-point group.** Re-run
   `uv pip install --editable .` from the external package's directory.
   Verify the entry-point landed:

   ```bash
   cd nautilus
   uv run python -c "import importlib.metadata; print([ep.name for ep in importlib.metadata.entry_points(group='nautilus_trading.strategies')])"
   ```

3. **Lenient discovery skipped your entry-point on import error.**
   Discovery is lenient — entry-point load failures emit a WARNING to
   stderr naming the broken plugin and its source distribution. Check the
   warning text first; fix the broken plugin or uninstall it. The public
   spec types live in `nautilus_trading.specs` (StrategySpec, ActorSpec,
   ConfigBuilder). Common causes: import-time side effects that fail
   outside a backtest context, mistyped imports, missing runtime deps in
   the venv.

4. **Entry-point name vs `STRATEGY_SPEC.name` mismatch.** Discovery raises
   `RuntimeError: Entry-point name mismatch` when the key in
   `pyproject.toml` differs from `STRATEGY_SPEC.name`. Make them equal
   byte-for-byte.

5. **YAML `strategy:` field doesn't match.** When invoking `nt backtest
   --config ...` or `nt paper-trade --config ...`, the YAML's `strategy:`
   key must equal the registered name from `nt strategies`. The registry
   is the source of truth, not the file path.

For the full "register an external strategy" workflow see
`docs/runbooks/external-strategies.md` in the repo.

## Reference paths

- Entry-point group: `nautilus_trading.strategies`
- Public spec types: `nautilus/src/nautilus_trading/specs.py`
  (import `StrategySpec`, `ActorSpec`, and `ConfigBuilder` from here)
- Repo CLAUDE.md: `CLAUDE.md` (repo root)
- Paper-trade runbook: `docs/runbooks/paper-trade.md`
- External-strategies runbook: `docs/runbooks/external-strategies.md`

## See also

- The companion `nautilus-strategy-authoring` skill in this plugin covers
  the contract for authoring a new Strategy that the registry will pick
  up.
- The `/nt-strategies`, `/nt-backtest`, `/nt-paper`, `/nt-live`, and
  `/nt-test` slash commands wrap the above invocations.
