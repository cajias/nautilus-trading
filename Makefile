# Makefile for NautilusTrader Algorithmic Trading
#
# Package dir: nautilus/ (contains pyproject.toml)
# Source: nautilus/src/
# Strategies: strategies/ (at root)
# Tests: tests/ (at root)
# Notebooks: co-located in strategies/<market_type>/

# Strategy module path (relative to strategies/), e.g. forex.ema_cross
STRATEGY ?= forex.ema_cross

.PHONY: help install install-ml install-kronos test test-unit test-kronos lint lint-fix validate backtest backtest-crypto backtest-kronos paper-trade-kronos smoke-paper-order strategies jupyter clean

# Default target
help:
	@echo "NautilusTrader - Make targets"
	@echo ""
	@echo "Setup:"
	@echo "  make install           - Install all dev dependencies and tools"
	@echo "  make install-ml        - Install ML dependencies (TimesFM, PyTorch)"
	@echo "  make install-kronos    - Install Kronos deps + clone model repo"
	@echo ""
	@echo "Testing:"
	@echo "  make test              - Run all tests (pytest)"
	@echo "  make test-unit         - Run pytest unit tests only"
	@echo "  make test-kronos       - Run Kronos-specific unit tests"
	@echo ""
	@echo "Linting:"
	@echo "  make lint              - Run all linters (ruff, mypy, vulture)"
	@echo "  make lint-fix          - Auto-fix linting issues (ruff, isort)"
	@echo "  make validate          - Pre-push validation (lint + format + tests)"
	@echo ""
	@echo "Running:"
	@echo "  make backtest          - Run backtest (default: STRATEGY=$(STRATEGY))"
	@echo "  make backtest-crypto   - Run crypto backtest with Binance data"
	@echo "  make backtest-kronos   - Run Kronos foundation model backtest (fetches Binance data)"
	@echo "  make paper-trade-kronos - Run Kronos paper trading on Binance Testnet"
	@echo "  make smoke-paper-order - Submit one off-market LIMIT to Binance Testnet (opt-in)"
	@echo "  make strategies        - List all available strategies"
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
	@echo "  make backtest-kronos                               # Kronos backtest (mini model)"
	@echo "  make paper-trade-kronos                            # Kronos paper trading (Binance Testnet)"
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

# Install Kronos foundation model (financial OHLCV forecasting)
# Kronos is not a PyPI package — its Python dependencies are declared in
# pyproject.toml [kronos] and installed via uv sync. The repo itself is
# cloned for its model/ source code, then pointed to via KRONOS_REPO_PATH.
# Override clone dir: KRONOS_DIR=/other/path make install-kronos
KRONOS_DIR ?= $(HOME)/kronos
install-kronos:
	@echo "Installing Kronos foundation model..."
	@echo "-> Installing Python dependencies via uv..."
	@cd nautilus && uv sync --extra kronos --extra dev
	@if [ ! -d "$(KRONOS_DIR)" ]; then \
		echo "-> Cloning Kronos model code to $(KRONOS_DIR)..."; \
		git clone https://github.com/shiyu-coder/Kronos.git $(KRONOS_DIR); \
	else \
		echo "-> Kronos repo already at $(KRONOS_DIR), skipping clone"; \
	fi
	@echo ""
	@echo "v Kronos installation complete!"
	@echo "  Repo : $(KRONOS_DIR)"
	@echo "  Usage: export KRONOS_REPO_PATH=$(KRONOS_DIR) && make backtest-kronos"
	@echo "  HuggingFace model weights download on first run (~few GB)"

# Run all tests
test: test-unit

# Run pytest unit tests
test-unit:
	cd nautilus && uv run pytest ../tests/ -v

# Run Kronos-specific unit tests only (no BacktestEngine or catalog required)
test-kronos:
	cd nautilus && uv run pytest ../tests/test_kronos_strategy.py -v

# Linting targets

lint:
	@echo "Running linters..."
	@echo "-> Ruff (linting)..."
	@cd nautilus && uv run ruff check src/ ../strategies/
	@echo "-> Mypy (type checking)..."
	@cd nautilus && uv run mypy src/
	@echo "-> Vulture (dead code detection)..."
	@cd nautilus && uv run vulture src/ ../strategies/ --min-confidence 80
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

# Run Kronos foundation model backtest
# Model: KRONOS_MODEL_SIZE=mini|base  (default: mini, 4.1M params)
# Symbol: KRONOS_SYMBOL=BTCUSDT       (default: BTCUSDT)
# Interval: KRONOS_INTERVAL=1h        (default: 1h)
# Capital: KRONOS_INITIAL_CAPITAL=500 (default: 500 USDT)
backtest-kronos:
	cd nautilus && uv run python ../strategies/crypto/kronos/backtest.py

# Run Kronos paper trading on Binance Testnet
# Requires: BINANCE_TESTNET_API_KEY, BINANCE_TESTNET_API_SECRET, KRONOS_REPO_PATH
# Model: KRONOS_MODEL_SIZE=mini|base  (default: mini)
# Symbol: KRONOS_SYMBOL=BTCUSDT       (default: BTCUSDT)
paper-trade-kronos:
	cd nautilus && uv run python ../strategies/crypto/kronos/paper_trade.py

# Submit ONE off-market LIMIT order to Binance Spot Testnet, assert ACK, cancel.
# Opt-in manual smoke — requires BINANCE_TESTNET_API_KEY, BINANCE_TESTNET_API_SECRET,
# and BINANCE_TESTNET_ED25519_KEY_PATH to be set (see configs/paper/.env.local).
# Usage: make smoke-paper-order STRATEGY=ema_cross
smoke-paper-order:
	@if [ -z "$(STRATEGY)" ] || [ "$(STRATEGY)" = "forex.ema_cross" ]; then \
	  echo "Usage: make smoke-paper-order STRATEGY=<name>"; \
	  echo "Available: ema_cross grid_bot dca_bot timesfm_swing hybrid_sma_r10 timesfm_grid rvs_swing shock_guard"; \
	  echo "(STRATEGY must be an explicit paper-trade key, not the backtest-module default.)"; \
	  exit 1; \
	fi
	cd nautilus && uv run python ../scripts/smoke_paper_order.py $(STRATEGY)

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
