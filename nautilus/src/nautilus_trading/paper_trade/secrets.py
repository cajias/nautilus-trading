"""`.env.local` loader for Binance Testnet credentials.

Design: existing environment variables win. We load from `.env.local` only
to populate what is *missing*, so a CI run with secrets in the environment
is never overwritten by a stale committed-adjacent file.
"""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv


def load_dotenv_local(path: Path | str | None = None) -> bool:
    """Load `.env.local` into os.environ if present.

    Parameters
    ----------
    path
        Optional explicit path. Defaults to `./.env.local` in the current
        working directory.

    Returns
    -------
    bool
        True if a file was found and loaded; False if absent.
    """
    target = Path(path) if path is not None else Path.cwd() / ".env.local"
    if not target.exists():
        return False
    # override=False preserves pre-set environment variables (e.g. CI secrets).
    return bool(load_dotenv(dotenv_path=target, override=False))
