"""PaperRunConfig — strict YAML schema for `nt paper-trade --config`.

Top-level fields are validated by `msgspec` (unknown keys rejected). The
`params` bucket holds strategy-specific values and is handed as **kwargs to
the StrategyConfigBuilder at dispatch time; that builder already raises
ValueError for missing/bad fields, which the CLI remaps to BadParameter.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import msgspec
import msgspec.yaml


class PaperRunConfig(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    """One paper-trade run, declared in a YAML file."""

    strategy: str
    instrument_id: str
    bar_type: str
    trade_size: str | None = None
    log_level: str = "INFO"
    params: dict[str, Any] = msgspec.field(default_factory=dict)


def load_run_config(path: Path) -> PaperRunConfig:
    """Read YAML at `path` and decode into PaperRunConfig.

    Raises:
        FileNotFoundError: path does not exist.
        msgspec.ValidationError: unknown field, wrong type, missing required field,
            or malformed YAML (msgspec.yaml.decode routes DecodeError through
            ValidationError when a target type is provided).
    """
    data = path.read_bytes()
    try:
        return msgspec.yaml.decode(data, type=PaperRunConfig)
    except msgspec.DecodeError as exc:
        raise msgspec.ValidationError(f"Malformed YAML in {path}: {exc}") from exc
