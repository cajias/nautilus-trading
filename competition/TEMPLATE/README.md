# Competition submission template (R11+)

This directory is the canonical reference submission for the R11+
competition contract. It is a trivial always-flat strategy that subscribes
to bars and logs them — enough to exercise the validator and evaluator
without placing any orders. Copy this folder as the starting point for a
real round submission and replace each file with your own content.

## File layout

```
competition/agent-N-<persona>/round11/
├── strategy.py          # Strategy + Config + MANIFEST
├── tests/
│   ├── __init__.py
│   └── test_strategy.py # pytest-runnable
├── research/
│   ├── notes.md         # rationale + research summary
│   ├── explore.ipynb    # optional
│   └── backtest.py      # optional pandas/custom harness
└── README.md            # 1-paragraph summary + MANIFEST values
```

## Manifest

| Key | Value |
|-----|-------|
| `strategy_class_name` | `TemplateStrategy` |
| `config_class_name` | `TemplateConfig` |
| `instrument_id` | `BNBUSDT.BINANCE` |
| `bar_type` | `BNBUSDT.BINANCE-1-HOUR-LAST-EXTERNAL` |
| `default_config` | `{}` |
| `description` | Trivial always-flat template showing the minimum submission shape |

## Submission checklist

- [ ] `strategy.py` exists with your `Strategy` + `StrategyConfig`
      subclasses
- [ ] Module-level `MANIFEST` dict is present and all six required keys
      are populated
- [ ] `MANIFEST["strategy_class_name"]` and `config_class_name` resolve to
      classes in `strategy.py`
- [ ] Config class is declared with `frozen=True`
- [ ] All monetary values use `Decimal`, never `float`
- [ ] All prices go through `instrument.make_price(...)`
- [ ] All quantities go through `instrument.make_qty(...)`
- [ ] Logging uses `self.log.*`, not `print`
- [ ] `tests/test_strategy.py` passes under `pytest`
- [ ] `research/notes.md` documents the rationale
- [ ] Spot-only, long-only (or short requirement flagged in notes)
- [ ] `ruff check` is clean for the submission directory

## Verifying locally

```bash
# From the repo root
uv --project nautilus run ruff check competition/TEMPLATE/
uv --project nautilus run pytest competition/TEMPLATE/tests/ -xvs
```

See `competition/COMPETITION.md` for the full contract and
`strategies/crypto/hybrid_sma_r10.py` for a non-trivial reference port of
a pandas backtest into a NautilusTrader-pluggable strategy.
