"""``BacktestRunConfig`` — strict YAML schema for ``nt backtest --config``.

Mirrors :class:`~nautilus_trading.paper_trade.run_config.PaperRunConfig`
shape (same ``strategy`` / ``instrument_id`` / ``bar_type`` / ``params``
fields with the same precedence) and adds the four backtest-only
fields: ``venue``, ``account_type``, ``starting_balances``, and the
discriminated ``data_source`` block. Optional ``date_range`` (ISO
``start`` / ``end`` strings) is required for ``binance_rest`` data
sources and ignored by ``catalog`` / ``test`` adapters.

Strict (``forbid_unknown_fields=True``): typos at the top level or
inside ``DateRange`` raise :class:`msgspec.ValidationError`, which the
``nt backtest`` CLI (Task D) maps to ``typer.BadParameter`` for
friendly error output.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import msgspec
import msgspec.yaml


class DateRange(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    """ISO date window for a backtest run.

    Required for the ``binance_rest`` data source (no synthetic-data
    fallback in runtime code). Optional for ``catalog`` / ``test``
    adapters — they read whatever's in their store.
    """

    start: str
    end: str


class BacktestRunConfig(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    """One backtest run, declared in a YAML file under ``configs/backtest/``.

    Top-level fields are validated by ``msgspec`` (unknown keys
    rejected). The ``params`` bucket is forwarded to the registered
    ``StrategyConfigBuilder`` exactly like ``PaperRunConfig.params`` —
    the same per-strategy validation applies, so most builder errors
    (missing required field, wrong type) raise ``ValueError`` and the
    CLI re-maps to ``BadParameter``.
    """

    # Shared with PaperRunConfig — same field names, same semantics.
    strategy: str
    instrument_id: str
    bar_type: str

    # Backtest-only required fields.
    venue: str
    account_type: str
    starting_balances: list[str]
    data_source: dict[str, Any]

    # Optional — present in some YAMLs.
    trade_size: str | None = None
    log_level: str = "INFO"
    params: dict[str, Any] = msgspec.field(default_factory=dict)
    date_range: DateRange | None = None


def load_run_config(path: Path) -> BacktestRunConfig:
    """Read YAML at ``path`` and decode into :class:`BacktestRunConfig`.

    Raises
    ------
    FileNotFoundError
        Path does not exist.
    msgspec.ValidationError
        Unknown field, wrong type, missing required field, or malformed
        YAML (``msgspec.yaml.decode`` routes ``DecodeError`` through
        ``ValidationError`` when a target type is provided).
    """
    data = path.read_bytes()
    try:
        return msgspec.yaml.decode(data, type=BacktestRunConfig)
    except msgspec.DecodeError as exc:
        raise msgspec.ValidationError(f"Malformed YAML in {path}: {exc}") from exc
