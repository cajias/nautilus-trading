# Makefile for NautilusTrader Algorithmic Trading
#
# Package dir: nautilus/ (contains pyproject.toml)
# Source: nautilus/src/
# Strategies: strategies/ (at root)
# Tests: tests/ (at root)
# Notebooks: co-located in strategies/<market_type>/

# Strategy module path (relative to strategies/), e.g. forex.ema_cross
STRATEGY ?= forex.ema_cross

.PHONY: help install test test-unit lint lint-fix validate backtest strategies live jupyter clean

# Default target
help:
	@echo "NautilusTrader - Make targets"
	@echo ""
	@echo "Setup:"
	@echo "  make install           - Install all dev dependencies and tools"
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
	@echo "  make strategies        - List all available strategies"
	@echo "  make live              - Live trading placeholder"
	@echo "  make jupyter           - Launch Jupyter Lab with strategy notebooks"
	@echo ""
	@echo "Variables:"
	@echo "  STRATEGY=forex.ema_cross   Module path under strategies/ (default: forex.ema_cross)"
	@echo ""
	@echo "Examples:"
	@echo "  make backtest                           # backtest default strategy"
	@echo "  make backtest STRATEGY=forex.ema_cross  # backtest specific strategy"
	@echo "  make live STRATEGY=crypto.btc_momentum  # live placeholder"
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

# List available strategies
strategies:
	cd nautilus && uv run nt strategies

# Live trading placeholder
live:
	@echo "Live trading with strategy: strategies.$(STRATEGY)"
	@echo "Not yet implemented — use TradingNode directly"

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
