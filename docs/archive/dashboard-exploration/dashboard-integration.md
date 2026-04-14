# Dashboard Integration Plan — NautilusTrader Streamlit Visualizations

**Status:** DRAFT (planning only; no execution performed)
**Author:** planning subagent
**Date:** 2026-04-08
**Execution model:** Every task below is delegated to a named specialized subagent. The main orchestrator performs zero implementation work.

---

## 1. Summary

We will integrate two open-source Streamlit dashboards as **sibling projects** under `/Users/rc/Projects/workspace/`, NOT inside `nautilus-trading`. The work is strictly **phased**: Phase 1 stands up `nautilus_trader_streamlit` for backtest visualization and must pass a user-visible exit gate before Phase 2 begins. Phase 2 forks `Lynx_rf_quant`, strips its Random Forest ML, and repurposes it for **live paper-trading** visualization against Binance testnet. Both repos are **forked to the user's GitHub account first** and cloned from the fork — never cloned from upstream — as a supply-chain safety measure. All configuration is externalized (env vars / CLI flags); nothing hardcoded.

**Repo verification (completed during planning, 2026-04-08):**
- `Sergey-1221/nautilus_trader_streamlit` — MIT, default branch `main`, last push **2025-07-12**, 39 stars, not archived. OK.
- `LynxXie/Lynx_rf_quant` — MIT, default branch `main`, last push **2026-02-03**, 6 stars, not archived. OK, but very low star count — treat as untrusted upstream and pin a commit SHA.

---

## 2. Prerequisites

All prereqs run as the first delegated task (agent: `Explore`). Ordered; all must pass before Phase 1 Task 1.1 begins.

1. **`gh` CLI authenticated.** Run `gh auth status`. Expected: logged in with `repo` + `workflow` scopes. If missing, STOP and surface to user.
2. **Capture GitHub handle.** Run `gh api user --jq .login` and store as `GH_HANDLE`. Do NOT assume — every subsequent fork URL references this value.
3. **Verify `uv` installed and version.** Run `uv --version`. Require `>= 0.4.0`. If absent, STOP and surface to user (do NOT install it on their behalf).
4. **Confirm Python 3.13 available.** Run `uv python list | grep 3.13`.
5. **Verify backtest catalog has data.** Run `ls /Users/rc/Projects/workspace/nautilus-trading/catalog/` and confirm at least one parquet file or subdirectory under `binance/` or `data/`. If empty, STOP — Phase 1 exit gate cannot be met without real data.
6. **Confirm env vars present (Phase 2 only; check early).** Run `printenv BINANCE_TESTNET_API_KEY BINANCE_TESTNET_API_SECRET` and confirm both are non-empty. Do not print the values to logs.
7. **(Optional) Verify `it2` CLI** if the user wants iTerm2 teams-mode for parallel dashboards: `command -v it2`. Non-blocking.
8. **Verify parent workspace path writable.** `test -w /Users/rc/Projects/workspace/`.
9. **License re-verification.** For both repos run `gh api repos/<owner>/<repo>/license --jq .license.spdx_id`. MUST be `MIT`. If anything else, STOP and flag loudly — this plan's licensing assumption is broken.
10. **Repo liveness re-verification.** `gh api repos/<owner>/<repo> --jq '{archived, pushed_at, default_branch}'`. Neither repo should be archived; if `pushed_at` is >18 months old, flag for review.

---

## 3. Phase 1 — `nautilus_trader_streamlit` (Backtest Dashboard)

### 3.1 Task Table

| ID  | Subject                                                         | Owner (agent type)              | Blocks         | Blocked by | Success criterion |
|-----|-----------------------------------------------------------------|---------------------------------|----------------|------------|-------------------|
| 1.0 | Run prereq checklist (Section 2)                                | `Explore`                       | 1.1            | —          | All 10 prereqs pass; `GH_HANDLE` captured |
| 1.1 | Fork `Sergey-1221/nautilus_trader_streamlit` to user's account  | `Explore`                       | 1.2            | 1.0        | `gh repo view $GH_HANDLE/nautilus_trader_streamlit` succeeds |
| 1.2 | Clone fork to `~/Projects/workspace/nautilus-trader-streamlit/` | `Explore`                       | 1.3            | 1.1        | Directory exists, `git remote -v` shows fork as `origin` |
| 1.3 | Pin upstream commit SHA as `upstream-pinned` tag                | `Explore`                       | 1.4            | 1.2        | Tag pushed to fork; SHA recorded in `USAGE.md` |
| 1.4 | Audit existing repo structure & deps (recon only)               | `feature-dev:code-explorer`     | 1.5, 1.6       | 1.2        | Inventory of: entry point, data loaders, dep declarations, hardcoded paths |
| 1.5 | Create `pyproject.toml` pinning all deps for `uv`               | `everything-claude-code:python-reviewer` | 1.7 | 1.4        | `uv sync` succeeds in a clean venv; lockfile committed |
| 1.6 | Externalize catalog path via env var `NT_CATALOG_DIR` + CLI flag `--catalog` | `feature-dev:code-explorer` | 1.7            | 1.4        | No grep hits for hardcoded `/catalog` or user home in repo; CLI flag overrides env var |
| 1.7 | Smoke-test: launch dashboard against `nautilus-trading/catalog/` | `feature-dev:code-explorer`    | 1.8            | 1.5, 1.6   | `streamlit run` starts; one backtest renders with candles + SMA/EMA + equity curve + trade markers |
| 1.8 | Write `USAGE.md` configuration guide                            | `everything-claude-code:python-reviewer` | 1.9 | 1.7        | File documents env vars, catalog pointing, adding strategies, pinned SHA, fork rationale |
| 1.9 | Python code review of all changes                               | `everything-claude-code:python-reviewer` | 1.10 | 1.8       | No blocking findings; all findings either addressed or accepted with rationale |
| 1.10 | Phase 1 exit gate (user-visible)                               | `feature-dev:code-reviewer`     | Phase 2        | 1.9        | See Section 3.4 below |

### 3.2 Architectural Decisions

- **Config strategy:** Use **both** env var (`NT_CATALOG_DIR`) AND CLI flag (`--catalog PATH`) with CLI taking precedence. Env var is friendlier for `streamlit run` which does not accept custom argv nicely; CLI flag supported via `--` separator. Rationale: matches how the user's existing nautilus tooling is configured, and avoids lock-in to either mechanism.
- **Dep management:** Create a new `pyproject.toml` even if upstream uses `requirements.txt`. Rationale: user's workspace standard is `uv`. Generate it by reading existing `requirements.txt` (if any) and pinning each to the version currently installable. Commit `uv.lock`.
- **Do NOT use `uv workspace`** at the parent `~/Projects/workspace/` level — these are sibling projects, not a monorepo. Each fork has its own isolated venv. (Open question for user below — see Section 7.)
- **Version pinning:** Pin streamlit, pandas, pyarrow, plotly / lightweight-charts bindings, and `nautilus_trader==1.224.0` if upstream imports it. No floating versions.
- **No modifications to `nautilus-trading` repo.** Phase 1 has zero coupling to the active worktree. Dashboard reads parquet from a path; that is the only contract.
- **Branch strategy inside the fork:** all work on `integration/workspace-setup` branch; merge to `main` only after Phase 1 exit gate passes.

### 3.3 Risks and Mitigations

1. **Upstream repo has stale `nautilus_trader` version incompatible with v1.224.0.** Mitigation: Task 1.4 audits the import; if incompatible, Task 1.5 pins against a compatibility shim or forks the loader module. Escalate to user if nontrivial.
2. **Upstream uses a non-standard parquet schema that doesn't match user's `catalog/` layout.** Mitigation: Task 1.7 smoke-test is the canary. If render fails, Task 1.6 expands scope to include a small adapter module `catalog_adapter.py` that normalizes the schema.
3. **Upstream ships hardcoded paths or expects relative `./data/`.** Mitigation: Task 1.6 explicitly grep-sweeps for path literals; any hit becomes a required fix.
4. **Streamlit version drift breaks rendering.** Mitigation: Pin streamlit in `pyproject.toml` to exact version. `uv.lock` committed.
5. **Supply-chain attack on upstream between research and fork.** Mitigation: Task 1.3 pins a tag on the fork at the exact SHA verified during Task 1.4 audit; all subsequent work rebases off that SHA.

### 3.4 Phase 1 Exit Gate (user-visible verification)

The gate is PASSED only when ALL of the following are true, verified by the user (not an agent):

1. User runs the documented launch command from `USAGE.md` in a fresh terminal.
2. Browser opens to the dashboard (default `localhost:8501`).
3. At least one real backtest from `~/Projects/workspace/nautilus-trading/catalog/` is selectable.
4. Candlestick chart + at least one indicator overlay + equity curve + trade markers all render without console errors.
5. User can point to a different catalog by exporting `NT_CATALOG_DIR=/some/other/path` and the dashboard respects it.
6. User signs off in writing ("Phase 1 gate: PASS") before Phase 2 Task 2.0 begins.

**If the gate fails**, the responsible agent opens a remediation sub-plan and cycles back to the failing task. Phase 2 does NOT start.

---

## 4. Phase 2 — `Lynx_rf_quant` (Live Paper-Trading Dashboard)

### 4.1 Task Table

| ID  | Subject                                                         | Owner (agent type)              | Blocks         | Blocked by | Success criterion |
|-----|-----------------------------------------------------------------|---------------------------------|----------------|------------|-------------------|
| 2.0 | Confirm Phase 1 exit gate passed (human sign-off)               | `Explore`                       | 2.1            | 1.10       | User confirmation recorded |
| 2.1 | Fork `LynxXie/Lynx_rf_quant` to user's account                  | `Explore`                       | 2.2            | 2.0        | `gh repo view $GH_HANDLE/Lynx_rf_quant` succeeds |
| 2.2 | Clone fork to `~/Projects/workspace/lynx-live-dashboard/`       | `Explore`                       | 2.3            | 2.1        | Directory exists |
| 2.3 | Pin upstream SHA as `upstream-pinned` tag                       | `Explore`                       | 2.4            | 2.2        | Tag pushed to fork |
| 2.4 | **Tree-of-concerns audit**: separate ML / UI / data-fetch / trading logic | `feature-dev:code-explorer` | 2.5, 2.6 | 2.3        | Written inventory with file-by-file classification |
| 2.5 | Decide and document the IPC approach (see 4.2)                  | `Plan`                          | 2.6            | 2.4        | Decision doc in `ARCHITECTURE.md` in fork |
| 2.6 | Strip ML code paths (stub, do not delete)                       | `feature-dev:code-explorer`     | 2.7            | 2.4, 2.5   | `rg -i 'sklearn\|RandomForest\|joblib'` returns only stubs; `streamlit run` still boots |
| 2.7 | Implement data-source adapter per IPC decision (Section 4.2)    | `feature-dev:code-explorer`     | 2.8            | 2.6        | Adapter module passes unit smoke test against Binance testnet |
| 2.8 | `pyproject.toml` + `uv sync` for the fork                       | `everything-claude-code:python-reviewer` | 2.9 | 2.7        | Clean venv install works |
| 2.9 | Wire env vars for `BINANCE_TESTNET_API_KEY`/`SECRET` (read-only) | `feature-dev:code-explorer`    | 2.10           | 2.7        | No secrets in repo; dashboard reads from env only |
| 2.10 | Smoke-test: launch live dashboard against Binance testnet      | `feature-dev:code-explorer`     | 2.11           | 2.8, 2.9   | Dashboard renders live candles, open positions, open orders, PnL |
| 2.11 | Write `USAGE.md` for Lynx fork                                  | `everything-claude-code:python-reviewer` | 2.12 | 2.10      | Includes IPC architecture diagram, launch cmd, troubleshooting |
| 2.12 | Python code review of all changes                               | `everything-claude-code:python-reviewer` | 2.13 | 2.11      | No blocking findings |
| 2.13 | Phase 2 exit gate (user-visible)                                | `feature-dev:code-reviewer`     | —              | 2.12       | See Section 4.5 |

### 4.2 IPC / Data-Flow Decision

The central Phase 2 question: **how does the live dashboard read positions/orders/fills?**

**Options evaluated:**

| Option | Description | Pros | Cons |
|---|---|---|---|
| **A. Direct Binance REST/WS** | Dashboard talks to Binance testnet directly; ignores NautilusTrader entirely | Zero coupling to `TradingNode`. Dashboard runs even when strategy is off. Minimal code. | Truth-source is the exchange, not the strategy's view. Shows ALL account activity, not just this strategy's. Double API key usage. |
| **B. Shared snapshot (parquet/sqlite)** | Strategy periodically writes state snapshots to disk; dashboard reads them | Simple. Crash-safe. Dashboard is a pure reader. Works even when strategy is briefly down. | Staleness window (seconds-minutes). Requires modifying strategy code. Schema evolution pain. |
| **C. FastAPI/ZMQ bridge from `TradingNode`** | In-process HTTP or ZMQ publisher inside the strategy; dashboard subscribes | Real-time. Authoritative (strategy's own view). Can push fills as events. | Requires modifying strategy. Extra dep. Failure modes (dashboard vs strategy process lifecycle). Port allocation. |

**Recommendation: Option A — Direct Binance REST/WS.**

**Rationale:**
- The user's account is the only user of that testnet key, so "all account activity" equals "this strategy's activity" — the main con of Option A is void.
- Zero modification to `nautilus-trading` repo. The user's main worktree stays hermetic. Phase 1 already proved the pattern of "dashboard as a pure read-only observer of an external source."
- No IPC lifecycle pain. Dashboard runs whether or not the strategy process is alive — useful for post-mortem inspection.
- Read-only API keys (Binance supports scoped keys) mean the dashboard cannot accidentally trade.
- Upstream `Lynx_rf_quant` already uses CCXT — keeping that code path is the minimal-delta integration.

**Consequences accepted:**
- If the user later runs multiple strategies on the same testnet account, this dashboard shows the aggregate — we will revisit via Option C then.
- PnL attribution is account-level, not per-strategy. Document this in `USAGE.md`.

**Explicitly rejected alternatives** and why:
- Option B rejected: staleness + requires coupling into `nautilus-trading` repo.
- Option C rejected: premature complexity; couples dashboard lifecycle to strategy lifecycle; adds a dep the user has not asked for.

### 4.3 ML Strip Strategy — Tree of Concerns

Task 2.4 produces a file-by-file classification:

```
ML (strip):
  - Any module importing sklearn, xgboost, lightgbm, joblib, pickle of models
  - Feature-engineering pipelines feeding the RF
  - Model training entry points
  - Prediction/inference calls inside the UI loop

Data-fetching (KEEP and re-point):
  - CCXT client init
  - Candle/orderbook fetchers
  - Account / positions / open-orders fetchers

UI (KEEP):
  - Streamlit layout, components, charts, sidebars
  - Plotly / Lightweight Charts rendering

Trading logic (REMOVE entirely, never stub):
  - Any `place_order` / `create_order` call paths
  - RF-signal-to-trade bridges
  - Anything that could mutate the account
```

**Stripping rules:**
1. Do not `rm` files. Rename to `legacy_<name>.py.disabled` and remove imports.
2. For each disabled module, add a top-of-file comment explaining why and linking to this plan.
3. If a UI component references ML output (e.g., "predicted signal"), replace with a placeholder message ("ML module removed in fork") — do not spoof signals.
4. Run `rg -i 'RandomForest|sklearn|joblib|\.predict\('` after stripping; any residual hit is a bug.
5. **Safety check:** ensure no `create_order`, `new_order`, `post`-to-exchange code path is reachable from UI buttons. Read-only dashboard. This is non-negotiable.

### 4.4 Risks and Mitigations

1. **Accidental live order placement.** Mitigation: use read-only Binance API keys if possible (scoped key creation docs linked in `USAGE.md`); Task 2.6 removes all order-placing code paths; Task 2.12 code review explicitly checks for reachable order calls.
2. **Upstream has deep ML coupling (UI tightly bound to RF output).** Mitigation: Task 2.4 audit surfaces this early; if coupling is too deep, escalate to user for decision (rewrite UI vs abandon fork vs pick different upstream).
3. **Upstream license is actually not MIT on close read.** Mitigation: Prereq step 9 re-verifies via `gh api`. Already confirmed MIT during planning, but re-verify at execution.
4. **Binance testnet rate limits tripped by dashboard polling.** Mitigation: cache layer in adapter; document poll interval in `USAGE.md`; default to conservative 5s.
5. **Only 6 stars on upstream — possibly abandoned or low-quality.** Mitigation: pin SHA immediately on fork (Task 2.3); treat code as reference-only; be willing to rewrite significant portions.
6. **Env vars `BINANCE_TESTNET_API_KEY/SECRET` leak into Streamlit error pages.** Mitigation: wrap all CCXT calls in try/except that redacts; never `st.write()` the client object; code review checks.

### 4.5 Phase 2 Exit Gate (user-visible verification)

1. User exports the read-only testnet keys and runs documented launch command.
2. Dashboard renders live candlesticks for at least one symbol (e.g., BTCUSDT).
3. Dashboard shows current open positions (or "none" if flat).
4. Dashboard shows open orders (or "none").
5. Dashboard shows account PnL sourced from the exchange.
6. User verifies `rg -i 'RandomForest|sklearn'` returns only stub/legacy files.
7. User verifies there is NO reachable UI path that can place an order.
8. User signs off: "Phase 2 gate: PASS".

---

## 5. Task Dependency Graph

```
[Prereqs 1.0]
     |
     v
[1.1 fork] -> [1.2 clone] -> [1.3 pin SHA] -> [1.4 audit]
                                                  |
                                    +-------------+-------------+
                                    v                           v
                              [1.5 pyproject]            [1.6 config]
                                    \                           /
                                     +------> [1.7 smoke] -----+
                                                  |
                                                  v
                                            [1.8 USAGE.md]
                                                  |
                                                  v
                                          [1.9 py review]
                                                  |
                                                  v
                                       [1.10 PHASE 1 EXIT GATE]
                                                  |
                                   ===== HUMAN SIGN-OFF REQUIRED =====
                                                  |
                                                  v
[2.0 confirm gate] -> [2.1 fork] -> [2.2 clone] -> [2.3 pin SHA] -> [2.4 audit]
                                                                        |
                                                            +-----------+----------+
                                                            v                      v
                                                      [2.5 IPC decision]   (feeds 2.6)
                                                            |
                                                            v
                                                      [2.6 strip ML]
                                                            |
                                                            v
                                                      [2.7 data adapter]
                                                            |
                                                +-----------+-----------+
                                                v                       v
                                          [2.8 pyproject]         [2.9 env vars]
                                                \                       /
                                                 +---> [2.10 smoke] ---+
                                                          |
                                                          v
                                                   [2.11 USAGE.md]
                                                          |
                                                          v
                                                  [2.12 py review]
                                                          |
                                                          v
                                             [2.13 PHASE 2 EXIT GATE]
```

---

## 6. Suggested Agent Assignments (with justification)

| Task class | Agent | Why |
|---|---|---|
| Prereqs, forking, cloning, tag-pinning | `Explore` | Mechanical gh/git ops; no architectural thinking; fast and cheap. |
| Repo audits / tree-of-concerns / grep sweeps (1.4, 2.4, 2.6) | `feature-dev:code-explorer` | Purpose-built for reading unfamiliar codebases and producing structured inventories. |
| IPC architecture decision sub-plan (2.5) | `Plan` | Requires weighing tradeoffs and producing a durable decision doc. This is literally what `Plan` is for. |
| Config externalization / adapter coding (1.6, 2.7) | `feature-dev:code-explorer` | These are small, localized code changes inside the fork that require reading the surrounding code first. |
| `pyproject.toml` creation + `uv sync` (1.5, 2.8) | `everything-claude-code:python-reviewer` | Python packaging expertise; knows version pinning best practices. |
| Smoke tests / dashboard launches (1.7, 2.10) | `feature-dev:code-explorer` | Needs to read logs, diagnose render failures, and iterate. |
| `USAGE.md` authoring (1.8, 2.11) | `everything-claude-code:python-reviewer` | Python context + documentation discipline. |
| Python code review (1.9, 2.12) | `everything-claude-code:python-reviewer` | Explicit Python review agent. |
| Phase exit gate facilitation (1.10, 2.13) | `feature-dev:code-reviewer` | Independent second opinion before user sign-off. |

**Note:** Every task brief delivered to these agents MUST be self-contained — the agent will not have access to this conversation. Include: goal, inputs, exact commands, success criterion, and escalation path.

---

## 7. Open Questions for the User

1. **Fork directory naming.** Proposed: `~/Projects/workspace/nautilus-trader-streamlit/` and `~/Projects/workspace/lynx-live-dashboard/`. OK? (The second is renamed to drop the "rf" since we're stripping the Random Forest.)
2. **`uv workspace` at parent level?** Current plan says NO — each fork is standalone. Confirm you don't want a top-level `uv` workspace tying the sibling projects together.
3. **Read-only Binance API keys.** Do you want us to generate a new scoped key for the Lynx dashboard, or reuse the existing `BINANCE_TESTNET_API_KEY`? The plan assumes reuse but recommends generating a read-only key.
4. **Dashboard polling interval for Phase 2.** Default 5s. Acceptable, or do you want faster/slower?
5. **Branch strategy in the forks.** Plan says work on `integration/workspace-setup` and merge to `main` only after exit gate. OK, or prefer all-on-main?
6. **Legacy module handling.** Plan says rename to `legacy_*.py.disabled`, don't delete. OK?
7. **Where should `USAGE.md` live** — top of fork, or `docs/USAGE.md`? Plan says top-of-fork for discoverability.
8. **Do you want Phase 2's dashboard to ever include Phase 1's backtest view**, or are they permanently separate apps? (Current plan: permanently separate.)

---

## 8. Out of Scope

- **Real-money trading.** Both dashboards are read-only / testnet-only. Zero order-placement UI.
- **Rewriting user strategies.** No touching `nautilus-trading/strategies/`.
- **Replacing with Grafana, Dash, or a custom React UI.** Streamlit only, per user direction.
- **ML re-implementation.** The Random Forest is stripped, not replaced.
- **Multi-account / multi-strategy attribution.** Phase 2 is account-level only.
- **CI/CD for the forks.** No GitHub Actions workflows added in this pass.
- **Publishing the forks back to upstream as PRs.** Forks remain private to the user.
- **Modifying `nautilus-trading` repo or its worktrees.** Both dashboards are strict siblings.
- **Authentication on the Streamlit dashboards.** Both are localhost-only.
- **Containerization (Docker).** Direct `uv run` launch only.
- **Alert/notification systems.** View-only, no alerting.

---

## 9. Planner Red Flags (discovered during planning)

1. **`LynxXie/Lynx_rf_quant` has only 6 stars** and was last pushed 2026-02-03. Low community validation. Pin a SHA immediately and treat as reference code.
2. **Plans directory did not exist** at `/Users/rc/Projects/workspace/nautilus-trading/.claude/worktrees/whimsical-churning-avalanche/plans/` — the executing agent will need to create it (done by planner via `mkdir -p` to write this plan).
3. **No prior `pyproject.toml`/`requirements.txt` inspection was performed** on the upstream repos per the instruction to not read upstream source unless necessary. Task 1.4 / 2.4 MUST do this before pinning.
4. **The `nautilus-trading/catalog/` directory contains `binance/` and `data/` subdirs** but the parquet layout was not inspected — Task 1.4 must verify that `Sergey-1221/nautilus_trader_streamlit`'s loader can read this schema, or Task 1.6 must add an adapter.
