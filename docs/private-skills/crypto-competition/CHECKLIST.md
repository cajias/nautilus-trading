# Crypto Competition Submission Checklist

Run through every item below before calling your submission done. If any box is unchecked, fix it — the validator will catch most of these automatically and mark the submission INVALID.

## Structural

- [ ] `strategy.py` exists with a module-level `MANIFEST` dict
- [ ] `MANIFEST` has all 6 required keys: `strategy_class_name`, `config_class_name`, `instrument_id`, `bar_type`, `default_config`, `description`
- [ ] `tests/test_strategy.py` exists and is pytest-runnable
- [ ] `research/` directory exists with at least `notes.md` documenting the rationale
- [ ] `README.md` exists with a 1-paragraph summary

## Types & inheritance

- [ ] Strategy class inherits from `nautilus_trader.trading.strategy.Strategy`
- [ ] Config class inherits from `nautilus_trader.config.StrategyConfig` with `frozen=True`
- [ ] Type hints on all public methods
- [ ] Line length stays within 100-120 characters

## Hard constraints

- [ ] No references to `leverage`, `margin`, `futures`, `short_sell`, `isolated_margin`, or `cross_margin`
- [ ] All prices go through `instrument.make_price()`, never `round(price, ...)`
- [ ] All quantities go through `instrument.make_qty()`, never manual division
- [ ] All logging uses `self.log.info/warning/error`, NOT `print(...)`
- [ ] All monetary values are `Decimal`, never `float`
- [ ] Config fields that hold money or percentages use `Decimal`, not `float`

## Validation

- [ ] `uv run pytest round<N>/tests/test_strategy.py -q` exits 0
- [ ] `uv run python competition/validate_submission.py round<N>/` exits 0
- [ ] `uv run ruff check round<N>/strategy.py round<N>/tests/test_strategy.py` exits 0
- [ ] The strategy runs without error in a minimal BacktestEngine smoke (optional but recommended)
