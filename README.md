# Nautilus Trading

Algorithmic trading strategies built on [NautilusTrader](https://github.com/nautechsystems/nautilus_trader) v1.224.0 — a high-performance trading platform with a Rust/Cython core and Python API.

## Quick Start

```bash
make install                            # install deps via uv
make strategies                         # list available strategies
make backtest                           # run default backtest (forex.ema_cross)
make backtest STRATEGY=forex.ema_cross  # run a specific strategy
make jupyter                            # explore in Jupyter
```

## Project Structure

```
nautilus-trading/
├── Makefile                             # Task runner (make help)
├── nautilus/                            # Package (uv-managed)
│   ├── pyproject.toml
│   └── src/nautilus_trading/
│       ├── cli/                         # Typer CLI (nt command)
│       ├── backtest/                    # Backtest runner
│       └── data/                        # Data providers & download
├── strategies/                          # Strategies by market type
│   ├── forex/
│   │   ├── ema_cross.py                # Strategy + config
│   │   └── ema_cross_backtest.ipynb    # Co-located notebook
│   ├── crypto/
│   └── prediction_markets/
└─��� tests/
```

Strategies are organized by market type. Each strategy can have a co-located Jupyter notebook for exploration and visualization.

## Requirements

- Python 3.12+ (3.12-3.14)
- [uv](https://docs.astral.sh/uv/) package manager

## Adding a Strategy

1. Create `strategies/<market_type>/<name>.py` with a `Strategy` subclass and `StrategyConfig`
2. Optionally add `strategies/<market_type>/<name>.ipynb` for interactive exploration
3. Run `make strategies` to verify discovery
4. Run `make backtest STRATEGY=<market_type>.<name>` to test

See `strategies/forex/ema_cross.py` for a complete example.

## Development

```bash
make test                               # run tests
make lint                               # ruff + mypy + vulture
make lint-fix                           # auto-fix lint issues
make validate                           # pre-push check (lint + tests)
make clean                              # remove caches and artifacts
```

## Data Providers

Data is fetched via a pluggable provider system. The built-in `test` provider downloads sample EUR/USD tick data. Add new providers by subclassing `DataProvider` in `nautilus/src/nautilus_trading/data/providers.py`.

```bash
make backtest STRATEGY=forex.ema_cross  # uses default "test" provider
cd nautilus && uv run nt backtest --data-provider test
```

## Live Trading

Binance integration is supported via NautilusTrader adapters. Copy `.env.example` to `.env` and configure your API keys. See `CLAUDE.md` for detailed setup instructions.
