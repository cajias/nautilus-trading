# Archive: historical per-round evaluators (R1-R10)

Pandas-only, self-contained scripts from before the R11+ contract. Each is a frozen
historical artifact — do not modify them. They remain in-tree so the `agent-*/round{N}/`
submission directories can still be replayed ad-hoc.

R11+ uses the parameterized `competition/evaluate.py`, which dispatches by `--round N`
to `competition/round_configs/round{N}.py`.

## Replaying a historical round

```bash
cd nautilus && uv run python ../competition/archive/evaluate_round7.py
```

Each archived evaluator prints its own leaderboard. Results for R1-R10 are frozen under
`competition/archive/results/round{N}_results.txt`.
