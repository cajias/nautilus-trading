# Runbook: Spawn the SWE Iterative Team

**When to use:** You are the main session and need to stand up the 4-role team to execute sub-project A's plan. Prerequisites: scaffold validated (`scripts/team-check.sh` exits 0), scratchpad initialized (`scripts/team-init.sh`).

## 1. Pre-flight

```bash
scripts/team-check.sh    # must exit 0
scripts/team-init.sh     # creates .agent/team-state.md from template (idempotent)
```

If `team-init.sh` refuses because `.agent/team-state.md` already exists and you intend a fresh start, archive it first:

```bash
mv .agent/team-state.md .agent/history/pre-respawn-$(date -u +%Y%m%dT%H%M%SZ).md
scripts/team-init.sh
```

## 2. Spawn via `TeamCreate`

From the main Claude session, call `TeamCreate` with **`backend: "tmux"`** (user preference — not iterm2) and the four teammates below. For each, the `prompt` is the contents of the corresponding `.agent/roles/<role>.md` file plus the two lines below prepended (which orient the teammate to its environment):

```
You are running in tmux team mode. The authoritative state is `.agent/team-state.md` in the repo root. You communicate with teammates only via `SendMessage(to=<role>, body="...")`. Start by reading `.agent/team-state.md` and waiting for your first ping, unless you are Planner — in which case, begin immediately.
```

| Teammate `name` | Model | Brief source |
|---|---|---|
| `planner`    | opus    | `.agent/roles/planner.md` |
| `worker`     | sonnet  | `.agent/roles/worker.md` |
| `reviewer`   | sonnet  | `.agent/roles/reviewer.md` |
| `integrator` | haiku   | `.agent/roles/integrator.md` |

**Integrator validation mode.** For the initial dry-run, set `INTEGRATOR_DRY_RUN=1` in Integrator's prompt (append a line: `Environment: INTEGRATOR_DRY_RUN=1`). After validation passes (see §4), tear down and re-spawn Integrator without that line, or update its scratchpad-addressable state to indicate live mode.

**Prior dry-run failure (2026-04-18).** The first validation attempt failed because Worker self-committed three times. See `docs/superpowers/audits/2026-04-18-team-dryrun-FAILED.md`. Planner and Worker briefs have since been hardened (commit `222b446`); verify both `.agent/roles/planner.md` and `.agent/roles/worker.md` carry the `git commit` / `Commit-directive` language before respawning.

## 3. Kick off the loop

From main:
```
SendMessage(to=planner, body="begin — pick the next unchecked task in docs/superpowers/plans/2026-04-17-subproject-a-implementation.md")
```

From now on, main's job is to: (a) observe `.agent/team-state.md`, (b) respond to `blocker: human-needed` entries, (c) handle PR-merge hand-offs.

## 4. Dry-run validation gate (REQUIRED before flipping Integrator to live)

The team must complete Task 1.1 of sub-project A end-to-end in dry-run mode before Integrator is allowed to commit.

Human validates:

- [ ] Planner wrote a Brief matching Task 1.1's file list and step checkboxes.
- [ ] Worker wrote an Attempt 1 entry with files changed and test command summary.
- [ ] Reviewer wrote a Verdict — either ACCEPT or REJECT with a coherent reason.
- [ ] If REJECT: Worker retry happened and produced Attempt 2 within 2 rounds.
- [ ] On ACCEPT: Integrator printed a would-be commit message and `git diff --stat --cached` into the scratchpad (NOT an actual commit).
- [ ] Integrator rotated the scratchpad into `.agent/history/task-1.1.md`.
- [ ] No writes occurred to files outside Task 1.1's authorized file list (verify with `git status --short`).

If any check fails: tear down the team (`/team delete`), capture the failure in `docs/superpowers/audits/`, revise spec or role briefs, retry.

## 5. Flip Integrator to live

1. `SendMessage(to=integrator, body="pause")`.
2. Edit `.agent/roles/integrator.md` if you changed the dry-run contract during validation; commit the edit.
3. Re-spawn Integrator (via `/team` or `TeamCreate` again) without the `INTEGRATOR_DRY_RUN=1` environment line.
4. `SendMessage(to=integrator, body="resume — Task 1.1 is ACCEPTed; please commit")`.
5. Observe scratchpad for the commit SHA. If it shows up in `.agent/history/task-1.1.md` and `git log --oneline -1` matches the expected conventional-commit message, live mode is working.

## 6. PR-merge hand-off (once per PR boundary)

When Integrator opens PR N and pauses:
1. Human reviews and merges PR N in GitHub.
2. Back in this repo: `git checkout design/subproject-a-v2 && git pull --rebase origin main` (or whichever base the next PR rebases on).
3. `SendMessage(to=planner, body="PR <N> merged, begin PR <N+1>")`.

## 7. Teardown

`/team delete` (via main Claude session). `.agent/team-state.md` is gitignored so it can stay or be archived into `.agent/history/`; role briefs are committed and survive.
