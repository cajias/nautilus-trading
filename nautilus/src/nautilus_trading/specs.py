"""Public surface for plugin authors.

Strategy modules (in-repo and third-party) import :class:`StrategySpec` and
:class:`ActorSpec` from THIS module — never from
``nautilus_trading.cli._strategy_specs``. The latter is private discovery
glue (concrete builder classes, registry plumbing, sys.path bootstrap) that
new code should not depend on.

Why a dedicated public module:

- Importing :mod:`nautilus_trading.cli._strategy_specs` no longer triggers
  discovery — discovery is deferred until first access of ``STRATEGY_SPECS``
  (via PEP 562 module ``__getattr__``) or first call to
  ``get_strategy_specs()``. Even so, plugin authors should depend only on
  the dataclass shapes exposed here, not on private discovery glue: a
  third-party plugin importing from ``_strategy_specs`` can race its own
  ``STRATEGY_SPEC`` construction against the first cache miss, and the
  ``_``-prefix is a deliberate "private" signal.
- Public surface lives here under the package root, not in a
  ``_``-prefixed CLI submodule.

The two concrete dataclasses (``StrategySpec``, ``ActorSpec``) and a single
:class:`ConfigBuilder` Protocol are the entire public contract. Concrete
builder classes / functions used by in-repo strategies stay in
``nautilus_trading.cli._strategy_specs``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class ConfigBuilder(Protocol):
    """Builds a config-kwargs dict from parsed CLI / YAML args.

    Used uniformly for both strategy and actor config builders — both have
    the same shape (``args -> dict``), and the previous two Protocols
    (``StrategyConfigBuilder`` / ``ActorConfigBuilder``) were duplicates.

    Implementations should raise :class:`ValueError` on missing or invalid
    fields so the CLI dispatcher can map any builder failure to a
    :class:`typer.BadParameter` uniformly.
    """

    def build(self, args: dict[str, Any]) -> dict[str, Any]: ...


# Backwards-compat aliases — both names are still imported in places that
# this refactor is not touching, and downstream plugin authors may depend on
# either spelling. They are exact aliases (not subclasses) so structural
# checks treat them as equivalent.
StrategyConfigBuilder = ConfigBuilder
ActorConfigBuilder = ConfigBuilder


@dataclass(frozen=True)
class ActorSpec:
    """Wire-description for an actor attached to a strategy.

    Attributes
    ----------
    actor_path
        Import path for the Actor class, e.g.
        ``"strategies.crypto.kronos.actor:KronosActor"``. Consumed by
        ``ImportableActorConfig``.
    config_path
        Import path for the Actor's config class, e.g.
        ``"strategies.crypto.kronos.actor:KronosActorConfig"``.
    builder
        Maps parsed CLI / YAML args → the actor_config dict passed to
        ``ImportableActorConfig.config``.
    """

    actor_path: str
    config_path: str
    builder: ConfigBuilder


@dataclass(frozen=True)
class StrategySpec:
    """Wire-description for a strategy the generic runners can attach.

    Attributes
    ----------
    name
        CLI / YAML key (``"grid_bot"``, ``"kronos"``, ...).
    builder
        Maps parsed args → strategy_config dict for ``ImportableStrategyConfig``.
    strategy_path
        Import path for the Strategy class.
    config_path
        Import path for the Strategy's config class.
    actor_specs
        Zero or more ``ActorSpec``s to attach before the strategy. Empty tuple
        for strategies that don't need a sibling actor; kronos is the only
        in-repo example with a non-empty tuple. A tuple (not a list) keeps the
        frozen instance hashable without a custom ``__hash__``.
    """

    name: str
    builder: ConfigBuilder
    strategy_path: str
    config_path: str
    actor_specs: tuple[ActorSpec, ...] = ()
