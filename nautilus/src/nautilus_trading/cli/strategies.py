"""``nt strategies`` — list discovered strategy specs and their source packages.

Diagnostic surface for the entry-point discovery introduced in sub-project C
(PR 1 / #47). Helps debug "why isn't my external strategy loading?" by showing
exactly which specs are visible to ``nt`` and which installed package each one
came from.

Pre-PR-2 this command walked ``strategies/`` on the filesystem and printed
classes via ``inspect``. Post-#47 the single source of truth is
:data:`nautilus_trading.cli._strategy_specs.STRATEGY_SPECS`, populated at
module import from the ``nautilus_trading.strategies`` entry-point group, so
external (``pip install -e``-ed) strategies appear here automatically.
"""

from __future__ import annotations

import importlib.metadata

import typer

from nautilus_trading.cli._strategy_specs import STRATEGY_SPECS


def strategies() -> None:
    """List all discovered strategy specs with their source packages.

    Output is one row per spec, sorted by strategy name:

        <name>  (<source-package>)  → <strategy_path>

    Source-package labels come from ``EntryPoint.dist.name``; in-repo specs
    show as ``nautilus-trading`` and external plugins show as their own
    distribution name.
    """
    sources: dict[str, str] = {
        ep.name: (ep.dist.name if ep.dist is not None else "<unknown>")
        for ep in importlib.metadata.entry_points(group="nautilus_trading.strategies")
    }

    if not STRATEGY_SPECS:
        typer.echo("No strategies discovered.")
        return

    name_width = max(len(name) for name in STRATEGY_SPECS)
    package_width = max(len(pkg) for pkg in sources.values()) if sources else 0

    for name in sorted(STRATEGY_SPECS):
        spec = STRATEGY_SPECS[name]
        pkg = sources.get(name, "?")
        typer.echo(f"{name:<{name_width}}  ({pkg:<{package_width}})  → {spec.strategy_path}")
