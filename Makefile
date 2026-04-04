# Makefile for NautilusTrader Algorithmic Trading
#
# Package dir: nautilus/ (contains pyproject.toml)
# Source: nautilus/src/
# Strategies: strategies/ (at root)
# Tests: tests/ (at root)
# Notebooks: co-located in strategies/<market_type>/

# Strategy module path (relative to strategies/), e.g. forex.ema_cross
STRATEGY ?= forex.ema_cross

.PHONY: help install install-ml test test-unit lint lint-fix validate backtest backtest-crypto strategies live jupyter clean

# Default target
help:
	@echo "NautilusTrader - Make targets"
	@echo ""
	@echo "Setup:"
	@echo "  make install           - Install all dev dependencies and tools"
	@echo "  make install-ml        - Install ML dependencies (TimesFM, PyTorch)"
	@echo ""
	@echo "Testing:"
	@echo "  make test              - Run all tests (pytest)"
	@echo "  make test-unit         - Run pytest unit tests only"
	@echo ""
	@echo "Linting:"
	@echo "  make lint              - Run all linters (ruff, mypy, vulture)"
	@echo "  make lint-fix          - Auto-fix linting issues (ruff, isort)"
	@echo "  make validate          - Pre-push validation (lint + format + tests)"
	@echo ""
	@echo "Running:"
	@echo "  make backtest          - Run backtest (default: STRATEGY=$(STRATEGY))"
	@echo "  make backtest-crypto   - Run crypto backtest with Binance data"
	@echo "  make strategies        - List all available strategies"
	@echo "  make live              - Paper trade on Binance testnet"
	@echo "  make jupyter           - Launch Jupyter Lab with strategy notebooks"
	@echo ""
	@echo "Variables:"
	@echo "  STRATEGY=forex.ema_cross   Module path under strategies/ (default: forex.ema_cross)"
	@echo ""
	@echo "Examples:"
	@echo "  make backtest                              # backtest default strategy"
	@echo "  make backtest STRATEGY=forex.ema_cross     # backtest specific strategy"
	@echo "  make backtest-crypto STRATEGY=crypto.grid_bot  # crypto with Binance data"
	@echo "  make backtest-crypto STRATEGY=crypto.dca_bot   # DCA bot backtest"
	@echo "  make backtest-crypto STRATEGY=crypto.timesfm_swing  # TimesFM strategy"
	@echo ""
	@echo "Cleanup:"
	@echo "  make clean             - Remove test artifacts and caches"

# Install all dependencies and tools
install:
	@echo "Installing NautilusTrader development environment..."
	@echo ""
	@echo "-> Checking for uv..."
	@command -v uv >/dev/null 2>&1 || { \
		echo "x uv not found. Installing..."; \
		curl -LsSf https://astral.sh/uv/install.sh | sh; \
		echo "v uv installed"; \
	}
	@echo "-> Installing package with dev and viz dependencies..."
	@cd nautilus && uv sync --extra dev --extra viz
	@echo ""
	@echo "v Installation complete!"
	@echo ""
	@echo "Tools available via 'uv run':"
	@echo "  - ruff (linter)"
	@echo "  - mypy (type checker)"
	@echo "  - vulture (dead code detector)"
	@echo "  - isort (import sorter)"
	@echo "  - pytest (test runner)"
	@echo "  - jupyter (notebook server)"

# Install ML dependencies (TimesFM, PyTorch)
install-ml:
	@echo "Installing ML dependencies (TimesFM, PyTorch)..."
	@cd nautilus && uv sync --extra ml --extra dev
	@echo "v ML installation complete!"
	@echo "  - timesfm (time series foundation model)"
	@echo "  - torch (PyTorch backend)"

# Run all tests
test: test-unit

# Run pytest unit tests
test-unit:
	cd nautilus && uv run pytest ../tests/ -v

# Linting targets

lint:
	@echo "Running linters..."
	@echo "-> Ruff (linting)..."
	@cd nautilus && uv run ruff check src/ ../strategies/
	@echo "-> Mypy (type checking)..."
	@cd nautilus && uv run mypy src/
	@echo "-> Vulture (dead code detection)..."
	@cd nautilus && uv run vulture src/ ../strategies/ ../vulture_whitelist.py --min-confidence 80
	@echo "v All linting checks passed!"

lint-fix:
	@echo "Auto-fixing linting issues..."
	@echo "-> Ruff (auto-fix)..."
	@cd nautilus && uv run ruff check src/ ../strategies/ --fix
	@echo "-> Isort (import sorting)..."
	@cd nautilus && uv run isort src/ ../strategies/
	@echo "v Auto-fixes complete!"

# Pre-push validation
validate:
	@echo "Running pre-push validation..."
	@echo "-> Ruff check..."
	@cd nautilus && uv run ruff check src/ ../strategies/
	@echo "-> Ruff format check..."
	@cd nautilus && uv run ruff format --check src/
	@echo "-> Running tests..."
	@cd nautilus && uv run pytest ../tests/ -q --tb=line -x
	@echo "v Validation passed!"

# Run backtest with parameterized strategy
backtest:
	cd nautilus && uv run nt backtest --strategy strategies.$(STRATEGY)

# Run crypto backtest with Binance data provider
backtest-crypto:
	cd nautilus && uv run nt backtest \
		--strategy strategies.$(STRATEGY) \
		--data-provider binance \
		--venue BINANCE \
		--currency USDT \
		--balance "500 USDT"

# List available strategies
strategies:
	cd nautilus && uv run nt strategies

# Live paper trading on Binance testnet (requires BINANCE_TESTNET_API_KEY/SECRET)
# Usage: make live STRATEGY=crypto.grid_bot INSTRUMENT=SOLUSDT.BINANCE BAR_TYPE=SOLUSDT.BINANCE-1-HOUR-LAST-EXTERNAL TRADE_SIZE=0.10
INSTRUMENT ?= BTCUSDT.BINANCE
BAR_TYPE ?= BTCUSDT.BINANCE-1-HOUR-LAST-EXTERNAL
TRADE_SIZE ?= 0.001
live:
	cd nautilus && uv run nt live \
		--strategy strategies.$(STRATEGY) \
		--instrument $(INSTRUMENT) \
		--bar-type $(BAR_TYPE) \
		--trade-size $(TRADE_SIZE)

# Launch Jupyter Lab with strategy notebooks
jupyter:
	cd nautilus && uv run jupyter lab ../strategies/

# Clean up test artifacts
clean:
	@echo "Cleaning up test artifacts..."
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	find . -type f -name "*.pyo" -delete
	find . -type f -name ".coverage" -delete
	rm -rf catalog/
	@echo "Clean complete."
