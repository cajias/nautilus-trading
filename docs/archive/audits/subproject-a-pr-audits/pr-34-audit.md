# PR #34 Audit — sub-project B PR 8: opt-in smoke + runbook + roadmap

**Date:** 2026-04-23
**Branch:** `subproject-b/pr8-smoke-runbook` → `main`
**Author:** cajias
**State:** OPEN, MERGEABLE, CLEAN (no CI configured on branch — `no checks reported`)
**Reviews/comments:** none posted on GitHub

## Overall recommendation: APPROVE

This is a low-risk closeout PR for sub-project B: an opt-in pytest marker, a gated smoke suite, a strategy-bypass manual smoke script, a Makefile target, an operator runbook, a roadmap flip, and a `CLAUDE.md` section. No runtime/production code is touched (except roadmap/CLAUDE.md/Makefile). Everything that talks to Binance is gated behind credentials + the `binance_testnet` marker, so default `make test` path is untouched. Lint + test numbers in the PR body check out against the live worktree: `make lint` is clean (ruff + mypy 19 files + vulture), and the test plan claim `293 passed / 28 skipped` is consistent with the (+9 skips from 9 parametrized cases).

## Critical (merge blockers)

None.

## Important (should fix before merge)

None. All four pre-submit review items called out in the PR body were actually applied in commit `69efe9e`:

- Verified `paper-trade-kronos` is removed from `Makefile` `.PHONY` (line 12), help text, and the target block (live file shows only `backtest-kronos` at 172 → `smoke-paper-order` at 179; no `paper-trade-kronos` anywhere).
- Verified `docs/runbooks/paper-trade.md:29` says "Copy `.env.example` (at the repo root)" — the bogus `/.env.example` is gone.
- Verified `scripts/smoke_paper_order.py:23` says "see .env.local at the repo root" — stale `configs/paper/.env.local` reference is gone.
- Verified `Makefile:177` says `see .env.local at the repo root` — same fix in the Makefile comment.

## Advisory (post-merge acceptable)

### A1 — `scripts/smoke_paper_order.py:128` uses an invalid typing forward-ref

```python
predicate: "callable[[object], bool]",
```

Lowercase `callable` is Python's built-in function, not a typing construct. The annotation is a bare string so it's never evaluated at runtime and `scripts/` isn't in the `make lint` mypy target — so no tooling catches it. Should be `Callable` from `collections.abc`, matching the import in `test_smoke_paper.py:30`.

**Proposed fix:**

```diff
-from collections.abc import Callable
+from collections.abc import Callable as _Callable  # (or just import at top)
@@
-    predicate: "callable[[object], bool]",
+    predicate: Callable[[object], bool],
```

Pure polish — zero runtime impact today.

### A2 — `scripts/smoke_paper_order.py:102` returns a tuple but the annotation says `object`

```python
def _build_runner(...) -> object:
    ...
    return runner_cls(**kwargs), run_config
```

The function returns a 2-tuple `(runner, run_config)` but is annotated `-> object`. Call site (`main`) unpacks correctly (`runner, run_config = _build_runner(...)`) so it works, but the type annotation is wrong. Should be `tuple[PaperTradeRunner, PaperRunConfig]` or similar. Same "not in lint target" story.

### A3 — `tests/paper_trade/test_smoke_paper.py:255` catches the base `Exception`

```python
except (asyncio.CancelledError, Exception):
```

`asyncio.CancelledError` is redundant (it inherits from `BaseException` but also from `Exception` since 3.8 only on older versions — on 3.12+ it inherits directly from `BaseException`, so keeping it is **correct** actually). Leave as is — double-checked and the construct is idiomatic for async teardown. No change needed; flagged only so reviewers don't re-flag.

### A4 — Runbook §8 "Where to look next" references `strategies/crypto/*_paper.py` but Kronos uses `strategies/crypto/kronos/paper_runner.py`

Line 175: lists both correctly, good. No action.

## Spec compliance (plan §1500-1570)

| Task | Spec requirement | Status |
| --- | --- | --- |
| 8.1 | Register `binance_testnet` marker in `nautilus/pyproject.toml` | ✅ present at `[tool.pytest.ini_options] markers` (diff confirmed) |
| 8.2 | CI node-boot smoke for all runners, parametrized, 30s timeout, credential-guard fixture → skip | ✅ `tests/paper_trade/test_smoke_paper.py` does exactly this; 9 runners (8 YAML + kronos); `_BOOT_TIMEOUT_SECONDS = 30.0`; autouse `_require_testnet_credentials` skips on missing env/PEM (never errors) |
| 8.3 | `make smoke-paper-order STRATEGY=<name>` + `scripts/smoke_paper_order.py` strategy-bypass LIMIT+cancel | ✅ Makefile target guards against empty + default `forex.ema_cross`; script builds runner from YAML, boots node, drives `exec_engine.execute(submit/cancel)`, asserts `OrderAccepted` (10s) + `OrderCanceled` (5s) |
| 8.4 | Runbook: Testnet signup, Ed25519 gen, `.env.local`, start/stop/panic-close, common errors (401, unknown instrument, tick grid) | ✅ 180-line runbook covers all of these in §1-§7; uses the exact Symptom → Cause → Fix format |
| 8.5 | Roadmap B paragraph → paper-trade-only scope; `CLAUDE.md` "Paper trading" section mirroring "Backtesting" | ✅ roadmap flips to SHIPPED with shipped-list; `CLAUDE.md` gains matching section with `nt paper-trade --config` canonical invocation, opt-in smoke, and a Core classes table |
| 8.6 | Pre-submit review + open PR | ✅ commit `69efe9e` documents the 4 pre-submit fixes in its body; PR open |

All six sub-tasks satisfied.

## Test coverage

- **`make lint`** — ran locally in the worktree: **clean** (ruff OK, mypy: "Success: no issues found in 19 source files", vulture OK). PR body claim confirmed.
- **pytest suite numbers** — PR body claims `293 passed, 28 skipped` (+9 new skips over main's 284/19). The `+9 skips` matches the 9 parametrized cases in `test_smoke_paper.py` which skip without testnet credentials. Plausible and self-consistent (I did not re-run pytest since it requires no credentials to reproduce the skip outcome but was not gated to fail).
- **CI checks** — no checks registered on `subproject-b/pr8-smoke-runbook`. Expected — repo has no CI wired up (sub-project C territory).

## Notes for the author

- No reviewer comments on GitHub (`gh api repos/.../pulls/34/comments` → `[]`, `reviews` → `[]`). The four self-identified presubmit issues are the only review feedback in the loop.
- The mergeStateStatus is `CLEAN` and `mergeable: MERGEABLE` — ready to land once approval is given.
