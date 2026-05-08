---
description: Run the nautilus-trading test suite via Make.
---

Run the full pytest suite for the repo:

```bash
make test
```

Resolves to `cd nautilus && uv run pytest ../tests/ -v` (per the
Makefile). Use `make test` rather than calling `uv run pytest` directly
— bare `uv run pytest` can resolve Homebrew's Python 3.9 pytest instead
of the venv on macOS (memory `pytest invocation`), and the explicit
`../tests/` path is required because the Makefile cd's into `nautilus/`
before running.

For lint + format + tests in one shot before pushing:

```bash
make validate
```
