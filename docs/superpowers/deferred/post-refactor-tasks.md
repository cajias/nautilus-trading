# Deferred Tasks (resume after sub-project A's refactor PRs)

Tasks dropped or scoped-out during sub-project A execution, to be picked up after
the refactor PRs (5-6+) land and the underlying runtime behavior changes.

---

## Reinstate `test_run_backtest_end_to_end_ema_cross`

**Origin:** Task 1.6 of `docs/superpowers/plans/2026-04-17-subproject-a-implementation.md`.

**Why deferred:** The smoke test was written against the Task 1.2 fixture catalog
(Bar data only: BTCUSDT-1-HOUR-LAST-EXTERNAL), but
`nautilus/src/nautilus_trading/backtest/runner.py:79` hardcodes
`data_cls=QuoteTick`. `BacktestNode` therefore finds no matching data and returns
`[]`, so the `assert len(results) == 1` assertion cannot pass without a
cross-task runtime fix. That fix is PR 5/6's job — the characterization PR
(PR 1) explicitly does not change runtime code.

**Resolution taken:** Dropped the smoke test from Task 1.6 at human direction
on 2026-04-18. The 4 unit-level characterization tests still land in PR 1;
end-to-end coverage is deferred.

**When to reinstate:**
After PR 5 or PR 6 refactors `build_backtest_config` to dispatch on data type
(Bar / QuoteTick / TradeTick) via a registry — whichever PR makes the Bar-data
path runnable against the committed fixture catalog.

**What to reinstate:**
The test function as originally drafted in the plan (see
`docs/superpowers/plans/2026-04-17-subproject-a-implementation.md` Task 1.6,
`test_run_backtest_end_to_end_ema_cross`). After the refactor, the original
assertions — `len(results) == 1` and `results[0].elapsed_time >= 0` — should
pass against the Task 1.2 fixture without modification. If they don't, the
refactor's dispatch logic isn't correct and the smoke test is the right place
to catch that.

**Acceptance:** test passes with `@pytest.mark.integration` marker against the
existing `crypto_catalog_path` fixture.

---

## Clean up `mypy tests/` scope errors

**Origin:** Task 3.1 of `docs/superpowers/plans/2026-04-17-subproject-a-implementation.md`,
Reviewer escalation 2026-04-18T20:22:00Z; Planner amendment 2026-04-18T20:30:00Z
(option b — narrow gate back to `mypy src/`).

**Why deferred:** Task 3.1's Brief initially specified `mypy src/ tests/` as the
gate — broader than prior tasks (1.1–2.1), which ran `mypy src/` only. The wider
scope surfaced 3 pre-existing errors in files outside Task 3.1's whitelist, and
Worker could not fix them without a scope expansion. Planner narrowed the gate
back to `mypy src/` to preserve consistency with established gates; the
`tests/` scope is deferred here.

**Errors to fix:**
- `nautilus/tests/test_data_providers.py:14` —
  `Cannot instantiate abstract class "DataProvider" with abstract attributes
  "ensure_catalog" and "name"  [abstract]`.
  Surfaced after Task 1.4 correctly removed an unused `# type: ignore[abstract]`
  suppressor. Fix: instantiate a concrete test double (subclass that implements
  both abstract members) rather than the ABC itself.
- `nautilus/tests/test_backtest_runner.py:32` —
  `Item "None" of "BacktestEngineConfig | None" has no attribute "strategies"
  [union-attr]`. Pre-existing since Task 1.6.
- `nautilus/tests/test_backtest_runner.py:58` — same error as line 32.
  Fix: narrow the optional with an `assert config.engine is not None` (or
  equivalent) before accessing `.strategies`, or restructure the assertion to
  avoid the union-attr.

**When to reinstate:**
Any time after PR 3 merges. Not gated on later refactor PRs — these are
straightforward test-file type fixes independent of runtime changes. Can be
bundled with one of the upcoming PRs (e.g., PR 4 test-suite hygiene) or as a
standalone chore PR.

**Acceptance:** `cd nautilus && uv run mypy src/ tests/` exits 0. Future Briefs
may then restore the broader `mypy src/ tests/` gate.

---

## Completed and pruned (2026-04-21)

The following three entries were verified against HEAD and removed during the
post-sub-project-A docs cleanup (chore branch `chore/docs-cleanup-post-subproject-a`):

- **Clarify `strategies/crypto/kronos/backtest.py` module docstring** — done.
  `strategies/crypto/kronos/backtest.py:1` now reads
  `"""Runner script that composes kronos/backtest_config.py builders into a
  BacktestEngine invocation."""`.
- **Wrap `engine.run()` in `try/finally engine.dispose()`** — done.
  `strategies/crypto/kronos/backtest.py:128-132` wraps the `run()` / `print_results()`
  block in `try/finally: engine.dispose()`.
- **Rewrite `strategies/crypto/kronos/_fetch_binance.py` module docstring** — done.
  `strategies/crypto/kronos/_fetch_binance.py:1` now reads
  `"""Fetches OHLCV data from the Binance public REST API and converts it to
  NautilusTrader Bar objects."""`.
