"""LiveRunConfig — strict YAML schema for ``nt live --config``.

Mirrors :class:`~nautilus_trading.paper_trade.run_config.PaperRunConfig`
with one critical addition: ``i_understand_real_money`` is required (no
default) and must be ``True``. Any YAML missing the field — or setting it
to ``false`` — fails validation in :func:`load_run_config`. This is the
deliberate friction point so a stray paste-error from a paper-trade YAML
can't accidentally route through the live path.

Note on the enforcement mechanism: msgspec's ``Literal`` does not accept
booleans (msgspec/yaml internals reject ``Literal[True]`` at decode time
with a ``TypeError``). The field is therefore typed as ``bool`` with no
default — making it required at the schema level — and
:func:`load_run_config` performs the ``is True`` check post-decode and
raises :class:`msgspec.ValidationError` to keep the failure mode uniform
with every other schema violation.

Real-money execution is out of scope per the 2026-04-21 no-real-money
directive — see
:class:`~nautilus_trading.live.strategy_runner.LiveStrategyRunner` for the
``main()`` gate. The schema is loaded today only because the ``nt live``
CLI dispatches a YAML through the runner, where ``main()`` then refuses
to boot.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import msgspec
import msgspec.yaml


class LiveRunConfig(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    """One live-trade run, declared in a YAML file.

    Attributes
    ----------
    strategy
        Strategy name — must resolve in
        :data:`~nautilus_trading.cli._strategy_specs.STRATEGY_SPECS`.
    instrument_id, bar_type
        Same shape as ``PaperRunConfig``. Top-level YAML is the canonical
        source of truth — the CLI overrides any duplicate inside ``params``.
    trade_size
        Optional. ``hybrid_sma_r10`` sizes from equity and ships YAML with
        ``trade_size: null``.
    log_level
        Forwarded to the node's :class:`LoggingConfig` at boot. Defaults
        match ``PaperRunConfig``.
    params
        Per-strategy bucket — handed to the spec builder verbatim.
    i_understand_real_money
        Required, typed :class:`bool`. **No default — must appear in the
        YAML, must be ``true``.** :func:`load_run_config` enforces the
        ``is True`` check after msgspec decode (msgspec doesn't support
        ``Literal[True]``).
    """

    strategy: str
    instrument_id: str
    bar_type: str
    i_understand_real_money: bool
    trade_size: str | None = None
    log_level: str = "INFO"
    params: dict[str, Any] = msgspec.field(default_factory=dict)


def load_run_config(path: Path) -> LiveRunConfig:
    """Read YAML at ``path`` and decode into :class:`LiveRunConfig`.

    Raises
    ------
    FileNotFoundError
        ``path`` does not exist.
    msgspec.ValidationError
        Unknown field, wrong type, missing required field (notably
        ``i_understand_real_money``), explicit ``i_understand_real_money:
        false``, or malformed YAML. Both the schema-level and the
        ``is True`` post-decode failure are raised through
        ``ValidationError`` to keep the funnel-through-one-except-clause
        contract uniform with
        :func:`nautilus_trading.paper_trade.run_config.load_run_config`.
    """
    data = path.read_bytes()
    try:
        cfg = msgspec.yaml.decode(data, type=LiveRunConfig)
    except msgspec.DecodeError as exc:
        raise msgspec.ValidationError(f"Malformed YAML in {path}: {exc}") from exc

    # Post-decode friction guard — msgspec can't express "must be True"
    # via Literal[True] (boolean Literals aren't supported), so we enforce
    # it here. The branch covers both YAML ``false`` and any non-bool the
    # decode somehow let through. Routing the failure through
    # ValidationError preserves the single-except-clause contract.
    if cfg.i_understand_real_money is not True:
        raise msgspec.ValidationError(
            "i_understand_real_money must be true to run a real-money "
            f"strategy (got {cfg.i_understand_real_money!r}). This field "
            "exists as a deliberate friction guard against paste-errors "
            "from paper-trade YAMLs — set it explicitly to true if you "
            "really mean to route through the live path."
        )

    return cfg
