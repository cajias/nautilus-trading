# NautilusTrader Project

Algorithmic trading project using [NautilusTrader](https://github.com/nautechsystems/nautilus_trader) v1.224.0 — a high-performance trading platform with a Rust/Cython core and Python API.

## Environment

- **Python**: 3.13 (required: 3.12-3.14)
- **Package manager**: `uv` (do NOT use pip or conda)
- **Venv**: `nautilus/.venv/` (managed by `uv sync` inside `nautilus/`)
- **Extras**: `dev` (ruff, mypy, vulture, pytest), `viz` (plotly, jupyterlab), `binance`, `interactive_brokers`

## Project Structure

```
nautilus-trading/
├── CLAUDE.md
├── Makefile                          # Task runner (make help for targets)
├── .gitignore
├── .env.example
├── nautilus/                          # Package (uv-managed)
│   ├── pyproject.toml
│   ├── uv.lock
│   └── src/nautilus_trading/
│       ├── cli/                      # Typer CLI (nt command)
│       ├── backtest/                 # Backtest runner
│       └── data/                     # Data providers & download
├── strategies/                       # Strategies by market type
│   ├── forex/
│   │   ├── ema_cross.py             # Strategy + config
│   │   └── ema_cross_backtest.ipynb  # Co-located notebook
│   ├── crypto/                      # (placeholder)
│   └── prediction_markets/          # (placeholder)
├── tests/
├── catalog/                          # Generated parquet data
└── data/                             # Raw data files
```

## Running

All commands use `make` targets. Run `make help` for the full list.

```bash
make install                         # Install deps
make strategies                      # List all available strategies
make backtest                        # Run default (forex.ema_cross)
make backtest STRATEGY=forex.ema_cross  # Run specific strategy
make jupyter                         # Launch Jupyter in strategies/
make test                            # Run tests
make lint                            # Lint (ruff, mypy, vulture)
make lint-fix                        # Auto-fix (ruff, isort)
make validate                        # Pre-push check (lint + format + tests)
make clean                           # Remove test artifacts and caches
```

Or directly via CLI:

```bash
cd nautilus && uv run nt backtest
cd nautilus && uv run nt strategies
```

## Development

### Toolchain

| Tool | Purpose | Config |
|------|---------|--------|
| `ruff` | Linting + formatting | `nautilus/pyproject.toml` [tool.ruff] |
| `mypy` | Type checking | `nautilus/pyproject.toml` [tool.mypy] |
| `vulture` | Dead code detection | min-confidence 80 |
| `isort` | Import sorting | via `make lint-fix` |
| `pytest` | Testing | `nautilus/pyproject.toml` [tool.pytest] |
| `uv` | Package management | `nautilus/pyproject.toml` |

### CLI entry point

The `nt` command is defined in `nautilus/pyproject.toml` under `[project.scripts]` and points to `nautilus_trading.cli:app` (Typer).

## Architecture — Key Concepts

### Strategy is venue-agnostic

The same `Strategy` subclass runs in both backtest (`BacktestEngine`) and live (`TradingNode`) with **zero code changes**. Strategies never import venue adapters — they only use generic types (`Bar`, `QuoteTick`, `OrderSide`). Venue wiring happens in the node config.

### Core classes

| Class | Purpose |
|-------|---------|
| `Strategy` | Base class for all strategies. Override `on_*` lifecycle methods |
| `StrategyConfig` | Immutable config (msgspec frozen). Define params here |
| `BacktestEngine` | Direct engine for backtesting (used in notebooks) |
| `BacktestNode` | High-level backtest runner (used in scripts, takes `BacktestRunConfig`) |
| `TradingNode` | Live/paper trading node (takes `TradingNodeConfig`) |
| `ParquetDataCatalog` | Stores/loads historical data in parquet format |

### Strategy lifecycle methods

Essential methods to override:

| Method | When called |
|--------|-------------|
| `on_start()` | Strategy starts — subscribe to data, register indicators |
| `on_bar(bar)` | New bar received — main signal logic goes here |
| `on_quote_tick(tick)` | New quote tick received |
| `on_trade_tick(tick)` | New trade tick received |
| `on_event(event)` | Order/position events (fills, cancels, etc.) |
| `on_stop()` | Strategy stops — cancel orders, close positions, unsubscribe |
| `on_reset()` | Reset state (indicators, internal vars) |

### Strategy helpers (available via `self.*`)

```python
# Data
self.subscribe_bars(bar_type)
self.subscribe_quote_ticks(instrument_id)
self.register_indicator_for_bars(bar_type, indicator)

# Trading
self.order_factory.market(instrument_id, order_side, quantity)
self.order_factory.limit(instrument_id, order_side, quantity, price)
self.submit_order(order)
self.cancel_all_orders(instrument_id)
self.close_all_positions(instrument_id)

# State
self.portfolio.is_flat(instrument_id)
self.portfolio.is_net_long(instrument_id)
self.portfolio.is_net_short(instrument_id)
self.cache.instrument(instrument_id)
self.indicators_initialized()
```

## Creating a New Strategy

Strategies live in `strategies/<market_type>/` subdirectories (not inside the `nautilus/` package).

### Directory layout

- Strategies go in `strategies/<market_type>/` (e.g., `strategies/forex/`, `strategies/crypto/`)
- Co-locate notebooks alongside strategies: `strategies/<market_type>/<name>_backtest.ipynb`
- Each market type directory needs an `__init__.py`

### Steps

1. Create a config class extending `StrategyConfig` (frozen=True)
2. Create a strategy class extending `Strategy`
3. Register indicators in `on_start()` with `self.register_indicator_for_bars()`
4. Implement signal logic in `on_bar()` (or `on_quote_tick()` for tick-level)
5. Use `self.order_factory.market()` + `self.submit_order()` to trade
6. Run `cd nautilus && uv run nt strategies` to verify discovery

See `strategies/forex/ema_cross.py` for a complete example.

### Strategy import path format

```
strategies.<market_type>.<name>:<ClassName>
```

Example:

```python
ImportableStrategyConfig(
    strategy_path="strategies.forex.ema_cross:EMACrossStrategy",
    config_path="strategies.forex.ema_cross:EMACrossConfig",
    config={"instrument_id": "EUR/USD.SIM", ...},
)
```

### Running

```bash
make backtest STRATEGY=forex.ema_cross   # via Make
cd nautilus && uv run nt backtest --strategy strategies.forex.ema_cross  # via CLI
```

## Data Providers

The `nautilus_trading.data` module provides a pluggable data provider abstraction for fetching and cataloging market data.

### Architecture

- `DataProvider` (ABC in `data/providers.py`) -- base class with two abstract members:
  - `name` property -- provider identifier (e.g., `"test"`)
  - `ensure_catalog(catalog_path)` -- downloads data if needed, returns a `ParquetDataCatalog`
- `TestDataProvider` -- built-in provider that downloads EUR/USD tick data from the `nautechsystems/nautilus_data` GitHub repo
- `PROVIDERS` registry (in `data/download.py`) -- maps provider names to classes

### Adding a new provider

1. Create a new class extending `DataProvider` in `data/providers.py`
2. Implement `name` and `ensure_catalog()`
3. Register it in `PROVIDERS` dict in `data/download.py`
4. Use via CLI: `nt backtest --data-provider <name>`

### Current providers

| Name | Description | Data |
|------|-------------|------|
| `test` | Sample FX data from GitHub | EUR/USD ticks (Jan 2020) |

## Available Indicators

All in `nautilus_trader.indicators`:

**Moving Averages**: EMA, SMA, WMA, HMA, DEMA, VIDMA, AMA, Wilder
**Trend**: MACD, Aroon, ADX (DirectionalMovement), IchimokuCloud, LinearRegression
**Volatility**: ATR, BollingerBands, KeltnerChannel, DonchianChannel, VolatilityRatio
**Momentum**: RSI, Stochastics, CMO, CCI, RateOfChange, PsychologicalLine
**Volume**: OBV, VWAP, KlingerVolumeOscillator, Pressure
**Other**: SpreadAnalyzer, Swings, FuzzyCandlesticks, Bias

## Backtesting

### With BacktestEngine (notebooks, direct control)

```python
engine = BacktestEngine(config=BacktestEngineConfig(logging=LoggingConfig(log_level="ERROR")))
engine.add_venue(venue=..., oms_type=OmsType.NETTING, account_type=AccountType.MARGIN,
                 base_currency=USD, starting_balances=[Money(1_000_000, USD)])
engine.add_instrument(instrument)
engine.add_data(ticks)
engine.add_strategy(MyStrategy(config=my_config))
engine.run()
# ... analyze results ...
engine.dispose()
```

### With BacktestNode (scripts, config-driven)

```python
node = BacktestNode(configs=[BacktestRunConfig(engine=..., venues=[...], data=[...])])
results = node.run()
```

### Sample data

EUR/USD tick data is auto-downloaded from `nautechsystems/nautilus_data` on GitHub.
Use `TestInstrumentProvider.default_fx_ccy("EUR/USD")` for the instrument definition.

### Bar types

Format: `{instrument_id}-{interval}-{aggregation}-{price_type}-{source}`

Examples:
- `EUR/USD.SIM-1-MINUTE-MID-INTERNAL` (1-min bars, mid price, engine-built)
- `BTCUSDT.BINANCE-5-MINUTE-LAST-INTERNAL`
- `EUR/USD.SIM-15-MINUTE-MID-INTERNAL`

## Paper trading

Run any strategy against Binance Spot Testnet via the `nt paper-trade` CLI, backed by committed YAML configs. Shipped in sub-project B.

### With `nt paper-trade --config` (canonical)

```bash
cd nautilus && uv run nt paper-trade --config ../configs/paper/ema_cross.yaml
```

One YAML per strategy under `configs/paper/`. To add a new strategy-config, duplicate an existing YAML and edit the `strategy` key + per-strategy `params`.

### Pre-release smoke (opt-in)

```bash
cd nautilus && uv run python -m pytest ../tests/paper_trade/test_smoke_paper.py -v
```

Gated by the `binance_testnet` pytest marker — normal `make test` skips it. Requires Binance Testnet credentials; see `docs/runbooks/paper-trade.md` for the full setup.

### Core classes

| Class | Purpose |
|-------|---------|
| `PaperTradeRunner` | Base class for paper-trade runners. Override `build_config()` and `main()`. |
| `build_paper_trade_node_config` | Helper that wires Binance Spot Testnet defaults (Ed25519, InstrumentProvider, account/env). |
| `run_paper_trade` | Boots the `TradingNode`, installs SIGINT/SIGTERM handlers, blocks. |
| `PaperRunConfig` | YAML schema (msgspec Struct). |
| `round_to_tick` | Snap a synthetic LIMIT price to the instrument's `price_increment`. |

## Live Trading (Binance)

### Target venue: Binance

Adapter: `nautilus_trader.adapters.binance`

Account types: `SPOT`, `MARGIN`, `ISOLATED_MARGIN`, `USDT_FUTURES`, `COIN_FUTURES`

### Environment variables

```bash
# Production
export BINANCE_API_KEY="your-key"
export BINANCE_API_SECRET="your-secret"

# Testnet (use for development!)
export BINANCE_TESTNET_API_KEY="testnet-key"
export BINANCE_TESTNET_API_SECRET="testnet-secret"
```

### Live node setup

```python
from nautilus_trader.adapters.binance import (
    BINANCE, BinanceDataClientConfig, BinanceExecClientConfig,
    BinanceLiveDataClientFactory, BinanceLiveExecClientFactory,
    BinanceAccountType,
)
from nautilus_trader.adapters.binance.common.enums import BinanceEnvironment
from nautilus_trader.live.node import TradingNode
from nautilus_trader.live.config import TradingNodeConfig

config = TradingNodeConfig(
    data_clients={
        BINANCE: BinanceDataClientConfig(
            account_type=BinanceAccountType.SPOT,
            environment=BinanceEnvironment.TESTNET,  # Use TESTNET for dev!
        ),
    },
    exec_clients={
        BINANCE: BinanceExecClientConfig(
            account_type=BinanceAccountType.SPOT,
            environment=BinanceEnvironment.TESTNET,
        ),
    },
    # strategies=[...] — add via ImportableStrategyConfig or node.add_strategy()
)

node = TradingNode(config=config)
node.add_data_client_factory(BINANCE, BinanceLiveDataClientFactory)
node.add_exec_client_factory(BINANCE, BinanceLiveExecClientFactory)
node.build()
node.run()  # Blocks — runs event loop
```

### Ed25519 keys (recommended by Binance)

```bash
openssl genpkey -algorithm ed25519 -out binance_ed25519_private.pem
openssl pkey -in binance_ed25519_private.pem -pubout -out binance_ed25519_public.pem
export BINANCE_API_SECRET="$(cat binance_ed25519_private.pem)"
```

## Gotchas

- **Report monetary columns are strings**: `realized_pnl` returns `"-9.48 USD"`, not a float. Strip currency with `.str.replace(r"\s+\w+$", "", regex=True).astype(float)` before numeric operations. Same for `commissions`.
- **System Python won't work**: Requires Python 3.12+. Always use the venv.
- **StrategyConfig is frozen**: Use `frozen=True` on all config classes.
- **Indicator warmup**: Check `self.indicators_initialized()` before acting on signals in `on_bar()`.
- **Bar type string format**: Must match exactly — `BarType.from_str()` is strict about the format.
- **Conda not supported**: Only vanilla CPython via `uv` is officially supported.

## Built-in Example Strategies

Located in the installed package at `nautilus_trader.examples.strategies`:

- `ema_cross.py` — EMA crossover (the canonical example)
- `ema_cross_trailing_stop.py` — with trailing stop loss
- `ema_cross_bracket.py` — with bracket orders (TP + SL)
- `ema_cross_long_only.py` — long-only variant
- `bb_mean_reversion.py` — Bollinger Band mean reversion
- `market_maker.py` / `simpler_quoter.py` — market making
- `orderbook_imbalance.py` — order book imbalance
- `volatility_market_maker.py` — volatility-adjusted MM
- `grid_market_maker.py` — grid trading

Read these for patterns: `python -c "import nautilus_trader.examples.strategies; print(nautilus_trader.examples.strategies.__path__)"`
