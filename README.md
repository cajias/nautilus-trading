# Nautilus Trading

Algorithmic trading strategies built on [NautilusTrader](https://github.com/nautechsystems/nautilus_trader) v1.224.0 — a high-performance trading platform with a Rust/Cython core and Python API.

## Quick Start

```bash
make install                            # install deps via uv
make strategies                         # list registered strategies (entry-point discovery)
cd nautilus && uv run nt backtest --config ../configs/backtest/ema_cross.yaml
cd nautilus && uv run nt paper-trade --config ../configs/paper/ema_cross.yaml
make jupyter                            # explore in Jupyter
```

The `nt` CLI is the canonical entry point. YAML configs under `configs/{backtest,paper}/` carry strategy + params as a single committed artifact. The legacy `nt backtest --strategy <module>` path still works but emits a `DeprecationWarning`.

## Installable as a Claude Code plugin

This repo ships an APM (Agent Package Manager) plugin with curated skills, slash commands, and an agent for the `nt` CLI:

```bash
apm install cajias/nautilus-trading
```

After install: `/nt-strategies`, `/nt-backtest <config>`, `/nt-paper <config>`, `/nt-test`. Plus the `nt-cli-quickstart` skill (workflow guide) and `nautilus-strategy-authoring` skill (how to write a new strategy with the public `nautilus_trading.specs` contract).

## Project Structure

```
nautilus-trading/
├── Makefile                             # Task runner (make help)
├── plugin.json + .claude-plugin/        # APM plugin manifest
├── skills/ commands/ agents/            # APM plugin content (Claude-native)
├── nautilus/                            # Python package (uv-managed)
│   ├── pyproject.toml
│   └── src/nautilus_trading/
│       ├── specs.py                     # PUBLIC: StrategySpec, ActorSpec, ConfigBuilder
│       ├── cli/                         # Typer CLI (nt command + entry-point discovery)
│       ├── backtest/                    # Backtest runner
│       ├── paper_trade/                 # Paper-trade runner + BarFanoutActor for multi-strategy
│       └── data/                        # Data providers & download
├── strategies/                          # In-repo strategies, registered as entry points
│   ├── forex/ema_cross.py
│   └── crypto/{dca_bot,grid_bot,hybrid_sma_r10,kronos,rvs_swing,shock_guard,timesfm_grid,timesfm_swing}.py
├── configs/                             # YAML run configs (backtest + paper)
└── tests/
```

## Requirements

- Python 3.12+ (3.12-3.14)
- [uv](https://docs.astral.sh/uv/) package manager

## Adding a Strategy

1. Create `strategies/<market_type>/<name>.py` with a `Strategy` subclass + `StrategyConfig`.
2. Define a top-level `STRATEGY_SPEC = StrategySpec(...)` constant. Import `StrategySpec` from `nautilus_trading.specs` (public surface).
3. Register the entry point in `nautilus/pyproject.toml` under `[project.entry-points."nautilus_trading.strategies"]`:
   ```toml
   <name> = "strategies.<market_type>.<name>:STRATEGY_SPEC"
   ```
4. Run `cd nautilus && uv pip install -e .` to refresh entry-point metadata.
5. Verify with `make strategies` — your strategy should appear.
6. Add `configs/backtest/<name>.yaml` with `strategy: <name>` and per-strategy `params:`.

External plugin packages can do the same — register their `STRATEGY_SPEC` under the `nautilus_trading.strategies` entry-point group and `nt` discovers them automatically. See `docs/runbooks/external-strategies.md`.

For multi-strategy paper-trade nodes that share a `bar_type`, attach a `BarFanoutActor` (in `nautilus_trading.paper_trade.bar_fanout`) — required workaround for an upstream NautilusTrader dispatch bug. See `nautilus_trading.paper_trade.multi_strategy.build_multi_strategy_paper_node_config`.

## Development

```bash
make test                               # run tests via pytest
make lint                               # ruff + mypy + vulture
make lint-fix                           # auto-fix lint issues
make validate                           # pre-push check (lint + tests)
make clean                              # remove caches and artifacts
```

## Data Providers

Data is fetched via a pluggable provider system. The built-in `test` provider downloads sample EUR/USD tick data. Crypto data uses Binance REST. Add new providers by subclassing `DataProvider` in `nautilus/src/nautilus_trading/data/providers.py`.

## Paper Trading

Binance Spot Testnet via the `nt paper-trade --config <yaml>` CLI. See `docs/runbooks/paper-trade.md` for full setup including Ed25519 key generation and required environment variables (`BINANCE_TESTNET_API_KEY`, `BINANCE_TESTNET_API_SECRET`, `BINANCE_TESTNET_ED25519_KEY_PATH`).

## Live Trading

`nt live --config <yaml>` is currently a scaffold that raises `NotImplementedError` per the 2026-04-21 no-real-money directive. The CLI surface is in place; the implementation will be filled in once the directive is lifted and a real-money safety review (`docs/airtight-checklist.md`) has been completed against the chosen strategy.
