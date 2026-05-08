---
description: Run the nautilus-trading test suite via Make.
---

Run the full pytest suite for the repo:

```bash
make test
```

Equivalent to `cd nautilus && uv run python -m pytest`. Use `make test`
rather than bare `uv run pytest` — the latter can resolve Homebrew's
Python 3.9 pytest instead of the venv on macOS, which is a known
pitfall in this repo.

For lint + format + tests in one shot before pushing:

```bash
make validate
```
