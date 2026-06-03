```
 _   _    _    _   _ _____ ___ _    _   _ ____
| \ | |  / \  | | | |_   _|_ _| |  | | | / ___|
|  \| | / _ \ | | | | | |  | || |  | | | \___ \
| |\  |/ ___ \| |_| | | |  | || |__| |_| |___) |
|_| \_/_/   \_\\___/  |_| |___|_____\___/|____/
                                  T R A D I N G
```

<p align="center">
  <strong>Config-driven algorithmic trading on NautilusTrader — one strategy contract from backtest to paper to live.</strong>
</p>

<p align="center">
  <a href="LICENSE"><img alt="License" src="https://img.shields.io/github/license/cajias/nautilus-trading"></a>
  <img alt="Top language" src="https://img.shields.io/github/languages/top/cajias/nautilus-trading">
  <img alt="Last commit" src="https://img.shields.io/github/last-commit/cajias/nautilus-trading">
  <img alt="Python" src="https://img.shields.io/badge/python-3.12--3.14-3776AB?logo=python&logoColor=white">
  <img alt="uv" src="https://img.shields.io/badge/managed%20by-uv-DE5FE9?logo=astral&logoColor=white">
  <img alt="Typer" src="https://img.shields.io/badge/CLI-Typer-009688">
  <img alt="NautilusTrader" src="https://img.shields.io/badge/engine-NautilusTrader%201.224-0A0A0A">
</p>

A working algorithmic-trading workbench built on [NautilusTrader](https://github.com/nautechsystems/nautilus_trader) — the high-performance event-driven platform with a Rust/Cython core and Python API. The same venue-agnostic `Strategy` subclass runs unchanged across backtest, paper, and (scaffolded) live engines; what changes is a committed YAML run-config, not code. Strategies are discovered through Python entry points, so the in-repo book and any external plugin package register the same way. The repo also ships as a [Claude Code / APM](https://github.com/microsoft/apm) plugin, exposing the `nt` workflow through curated skills, slash commands, and an agent.

## ✨ Features

- **One contract, three engines.** Strategies use only generic NautilusTrader types (`Bar`, `QuoteTick`, `OrderSide`) and never import venue adapters — venue wiring lives in the node config, so backtest / paper / live share the identical strategy class.
- **YAML run-configs as the source of truth.** Each strategy ships a committed `configs/backtest/<name>.yaml` and `configs/paper/<name>.yaml` carrying strategy + params as a single reviewable artifact. The legacy `--strategy <module>` flag path still works but emits a `DeprecationWarning`.
- **Entry-point strategy discovery.** Strategies register under the `nautilus_trading.strategies` entry-point group; `nt strategies` lists every discovered spec and its source distribution — in-repo and `pip install -e`-ed external plugins alike.
- **A real strategy book.** Forex EMA-cross plus a crypto suite: DCA bot, grid bot, hybrid SMA, risk/shock guards, RVS swing, TimesFM swing/grid, and a Kronos foundation-model strategy — with co-located backtest notebooks.
- **Pluggable market data.** A `DataProvider` abstraction fetches and catalogs data into Parquet; a built-in `test` provider pulls sample EUR/USD ticks, with Binance REST for crypto.
- **Paper trading on Binance Spot Testnet.** `nt paper-trade --config` boots a `TradingNode` against the testnet, including a `BarFanoutActor` workaround for running multiple strategies off one shared `bar_type`.
- **Ships as a Claude Code plugin.** `apm install` adds `/nt-strategies`, `/nt-backtest`, `/nt-paper`, `/nt-test`, plus quickstart and strategy-authoring skills and a strategy-author agent.

## 📦 Installation

Requires **Python 3.12–3.14** and the [`uv`](https://docs.astral.sh/uv/) package manager.

```bash
git clone https://github.com/cajias/nautilus-trading.git
cd nautilus-trading
make install          # uv sync --extra dev inside nautilus/
```

> The full ML runtime (PyTorch, TimesFM, LightGBM, Kronos deps) is a base dependency — if a strategy is in the tree, its deps are in the lockfile, so `make install` always yields a working `nt`.

Optionally, install as a Claude Code / APM plugin:

```bash
apm install cajias/nautilus-trading
```

## 🚀 Usage

The `nt` CLI is the canonical entry point. Discover strategies, then run one from its YAML config:

```bash
make strategies                                 # list discovered strategy specs + source package

cd nautilus
uv run nt strategies                            # same, directly via the CLI
uv run nt backtest --config ../configs/backtest/ema_cross.yaml
uv run nt paper-trade --config ../configs/paper/ema_cross.yaml
```

Every strategy ships a matching YAML under `configs/backtest/` and `configs/paper/` — to try another, just point `--config` at it (e.g. `../configs/backtest/grid_bot.yaml`). Add a new strategy-config by duplicating an existing YAML and editing the `strategy` key and `params`.

> `nt live --config <yaml>` is a registered scaffold that raises `NotImplementedError` by design, per the no-real-money directive. The CLI surface is in place; the implementation is gated behind a real-money safety review.

Paper trading targets Binance Spot Testnet and needs `BINANCE_TESTNET_API_KEY`, `BINANCE_TESTNET_API_SECRET`, and `BINANCE_TESTNET_ED25519_KEY_PATH`. See [`docs/runbooks/paper-trade.md`](docs/runbooks/paper-trade.md) for the full Ed25519 setup.

### Adding a strategy

1. Create `strategies/<market_type>/<name>.py` with a `Strategy` subclass + `StrategyConfig`.
2. Add a top-level `STRATEGY_SPEC = StrategySpec(...)`, importing `StrategySpec` from the public `nautilus_trading.specs` surface.
3. Register it in `nautilus/pyproject.toml` under `[project.entry-points."nautilus_trading.strategies"]`.
4. `cd nautilus && uv pip install -e .` to refresh entry-point metadata, then `make strategies` to confirm discovery.
5. Add `configs/backtest/<name>.yaml`. See [`docs/runbooks/external-strategies.md`](docs/runbooks/external-strategies.md) for shipping strategies from an external package.

## 🗂️ Project Structure

```
nautilus-trading/
├── Makefile                          # Task runner (make help for targets)
├── apm.yml / .claude-plugin/         # APM / Claude Code plugin manifest
├── agents/  commands/  skills/       # Claude-native plugin content (nt-* commands, skills, agent)
├── nautilus/                         # Python package (uv-managed, src layout)
│   ├── pyproject.toml                # deps, nt script, strategy entry points, tooling config
│   └── src/nautilus_trading/
│       ├── specs.py                  # PUBLIC contract: StrategySpec, ActorSpec, ConfigBuilder
│       ├── cli/                      # Typer CLI: backtest, paper-trade, live, strategies
│       ├── backtest/                 # Backtest runner + data-source adapters
│       ├── paper_trade/              # Paper-trade runner + BarFanoutActor (multi-strategy)
│       ├── live/                     # Live node config (scaffolded)
│       └── data/                     # Pluggable data providers + download
├── strategies/                       # Strategy book, registered as entry points
│   ├── forex/ema_cross.py            # canonical example (+ co-located notebook)
│   └── crypto/                       # dca_bot, grid_bot, hybrid_sma_r10, kronos/,
│                                     #   rvs_swing, shock_guard, timesfm_{grid,swing}
├── configs/                          # YAML run-configs: backtest/ + paper/
├── docs/                             # runbooks, specs, archived audits
└── tests/                            # pytest suite
```

## 🛠️ Development

```bash
make test            # pytest (cd nautilus && uv run pytest ../tests/)
make lint            # ruff check + ruff format --check + mypy + vulture
make lint-fix        # ruff auto-fix + format
make validate        # pre-push gate: lint + tests
make jupyter         # JupyterLab in strategies/
make clean           # remove caches and test artifacts
```

Run `make help` for the full target list. An opt-in Binance Testnet smoke test is gated behind the `binance_testnet` pytest marker and skipped by default.

## 🤝 Contributing

Issues and pull requests are welcome. Before opening a PR, run `make validate` (lint + tests) and keep new strategies behind the `nautilus_trading.specs` public contract with a committed YAML config. Conventional-commit style messages are appreciated.

## 📄 License

[MIT](LICENSE) © 2026 Raul Cajias.
