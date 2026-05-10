# PR #28 Audit — sub-project B PR 2: blocker-fix regression tests + `round_to_tick()`

Branch: `subproject-b/pr2-blocker-fixes` → `main` · 6 commits · +225 / -17 · state: OPEN, MERGEABLE.

## 1. Spec / plan compliance (Tasks 2.1–2.5)

| Plan task | Expected | Delivered | Status |
|---|---|---|---|
| 2.1 | Regression test: Ed25519 key type on data + exec clients | `test_ed25519_*` in `tests/paper_trade/test_node_config.py` | Done |
| 2.2 | Regression test: `InstrumentProviderConfig.load_ids` non-empty and contains target | `test_instrument_provider_loads_target_symbol`, `test_instrument_provider_populated_on_exec_client`; plus `test_account_type_is_spot`, `test_environment_is_testnet` | Done (extras intentional) |
| 2.3 | `round_to_tick(price, instrument) -> Price` helper + parametrized tests (tick 0.01, 0.001, 0.00001) | Implemented in `paper_trade/node_config.py`, parametrized test covers 0.01/0.001/0.00001 + exact-grid and sub-tick edge cases | Done |
| 2.4 | Audit all 8 crypto strategies; route at-risk sites through `round_to_tick()` | Only `timesfm_grid.py:528` migrated; the PR body claims grid_bot already uses `make_price()` and the other 6 are market-only | **Partial — see §4** |
| 2.5 | Open PR with lint/unit tests green | PR open, body lists `make lint` + 267 tests passing | Done |

Beyond-spec scope in this PR: (a) the Ed25519 **preflight path-exists + `os.access` R_OK** check with 5 tests (`test_preflight.py`) — this was PR #27 review feedback, not Task 2.x, but a reasonable bundle; (b) `.env.example` cleanup removing dead `BINANCE_API_KEY/SECRET/ACCOUNT_TYPE`; (c) the CLI stub `typer.echo` to silence vulture. None violate the PR 2 boundary.

## 2. Test quality — do they pin the three 2026-04-08 blockers?

- **Ed25519:** Asserted on both `data_clients[BINANCE]` and `exec_clients[BINANCE]` — catches the real 2026-04-08 failure mode (exec client silently HMAC).
- **InstrumentProvider:** `set(provider_cfg.load_ids)` + `InstrumentId.from_str("BTCUSDT.BINANCE") in loaded` for **both** clients. Robust to `load_ids` being a list/tuple/frozenset. Good.
- **Tick alignment:** Parametrized `test_round_to_tick_grid` + `test_round_to_tick_floors_positive_prices` (100.019 → 100.01) + `test_round_to_tick_preserves_exact_grid`. The floor-not-round-half-even expectation is explicitly pinned. Good.
- **Preflight (5 tests):** `test_missing_api_key_exits`, `test_missing_api_secret_exits`, `test_missing_ed25519_path_exits`, `test_unreadable_ed25519_path_exits` (nonexistent file), `test_all_present_passes`. Meaningful.

The `_FakeInstrument` shim in `test_node_config.py` exposes only `price_increment` and `price_precision` — this is fine for a unit test but **couples the helper signature** to those attributes. If Nautilus renames either field the test passes while prod breaks. Minor.

Style nit: the `from decimal import Decimal` / `from nautilus_trader.model.objects import Price` imports are placed mid-file (after the first group of tests) rather than at top — ruff-isort will re-stack them on the next `lint-fix`.

## 3. `round_to_tick()` correctness

Implementation:
```python
tick = Decimal(str(instrument.price_increment))
floored = (price / tick).quantize(Decimal("1"), rounding=ROUND_FLOOR) * tick
return Price(floored, precision=instrument.price_precision)
```

- `tick=0.01`, `price=100.237` → `10023.7` quantize floor → `10023` × `0.01` = `100.23`. Correct.
- `tick=0.00000001` (1e-8), `price=0.123456789` → `12345678.9` floor → `12345678` × `1e-8` = `0.12345678`. Correct; `Decimal(str(...))` sidesteps float binary artefacts that `Decimal(0.00000001)` would introduce.
- `price < tick`: e.g. `price=0.003`, `tick=0.01` → `(0.3).quantize(floor)` = `0` × `0.01` = `0`. Returns `Price(0, precision=...)` — **silently produces a zero price**, which Binance will reject but does not raise. Not in spec, probably fine for paper-trade's "fail at venue, log it" contract, but worth noting. No test covers this.
- Negative prices / zero: not covered. `ROUND_FLOOR` on negative numbers rounds toward −∞, which on a LIMIT price is the wrong direction, but negative prices are not a realistic input for LIMITs on Binance Spot. Acceptable.
- `ROUND_FLOOR` vs `ROUND_DOWN`: for strictly positive inputs they are equivalent; fine.

The helper's chosen home is `paper_trade.node_config` — this creates an import-direction smell (strategies import from `paper_trade`). The PR body acknowledges this ("will lift to `paper_trade.utils` in PR 4"). Acceptable as a temporary location.

## 4. Is `timesfm_grid._place_order` really the only at-risk site?

Audit of `grep -n 'order_factory\.limit' strategies/crypto/*.py`:

| File | Call site | How `price=` is built | At risk? |
|---|---|---|---|
| `timesfm_grid.py:527` | `_place_order(level, grid_price: Decimal, side)` | `round_to_tick(grid_price, self.instrument)` **after PR** | Fixed |
| `grid_bot.py:196` | `_place_order(level, grid_price: Price, side)` | Caller passes `grid_price: Price` — constructed upstream | **Unverified in this PR** |

The PR body claims `grid_bot.py` uses `instrument.make_price()` (already tick-aligned) and that is why it was skipped. I could not confirm from the indexed diff that the `grid_price` reaching `grid_bot._place_order` is actually produced via `instrument.make_price()` — the snippet shown only goes as far as the `_place_order` signature (`grid_price: Price`). **This is the one claim most worth verifying before merge**, because grid_bot is the *confirmed* 2026-04-08 offender and spec §7.3 Task 2.4 explicitly says "`grid_bot.py` is the confirmed case from 2026-04-08. Fix it for sure." If `grid_bot` constructs the price from a float / `Price(raw_level, precision=...)` anywhere, the fix is incomplete.

`dca_bot.py`, `timesfm_swing.py`, `hybrid_sma_r10.py`, `rvs_swing.py`, `shock_guard.py` did not show `order_factory.limit(` hits in the grep — consistent with the PR claim that they are market-only. No action needed for those in this PR.

## 5. GitHub review comments & CI

- `gh api repos/cajias/nautilus-trading/pulls/28/comments` → `[]`
- `gh api repos/cajias/nautilus-trading/pulls/28/reviews` → `[]`
- `gh pr view 28 --comments` → empty
- `gh pr checks 28` → **"no checks reported on the 'subproject-b/pr2-blocker-fixes' branch"**

There is no CI configured on this branch — the "tests green" signal is author-attested only. Consistent with this repo's current state (no GH Actions wired per spec §9.1, which is deferred to PR 7), but worth flagging so nobody assumes a pipeline ran.

---

## Punch list

### Must-fix before merge
1. **Verify grid_bot.py is actually tick-safe.** Read `strategies/crypto/grid_bot.py` end-to-end and confirm every path that constructs `grid_price: Price` goes through `instrument.make_price()` (or equivalent already-aligned source). Spec §7.3 and plan Task 2.4 both name `grid_bot` as the confirmed offender; if the PR author verified this out-of-band, record the evidence in the PR body before merge. If any arithmetic path remains, route it through `round_to_tick()` in this PR (don't punt to PR 4).

### Should-fix soon
2. **Move the mid-file `from decimal import Decimal` / `Price` imports to the top of `test_node_config.py`** — ruff-isort will rewrite them on the next `lint-fix` otherwise, producing a noisy follow-up commit. Tiny; could be a fixup commit on this branch.
3. **Add one negative test for `round_to_tick` with `price < tick`** — pin the current "returns Price(0)" behavior (or change it to raise `ValueError`) so a future refactor doesn't silently regress. Spec did not require it; still cheap insurance.

### Nice-to-have
4. The `round_to_tick` helper living in `paper_trade.node_config` means a pure strategy file (`timesfm_grid.py`) now imports from `paper_trade.*`. Author already flagged this for PR 4 lift-out to `paper_trade.utils`; fine to leave.
5. The `_FakeInstrument` shim in tests duplicates two attribute names (`price_increment`, `price_precision`). Consider a single session-scoped fixture to reduce drift risk if Nautilus renames those.
6. CI is unreported — when PR 7 lands (`@pytest.mark.binance_testnet` opt-in marker), backfill a `pytest -m 'not binance_testnet'` check on push so future PRs aren't author-attested.

Overall: the PR cleanly delivers Tasks 2.1–2.3 and 2.5, the preflight extension is a bonus, and the tests are meaningful. The one open question is Task 2.4's grid_bot audit — answerable with a five-minute re-read of `grid_bot.py` before merge.
