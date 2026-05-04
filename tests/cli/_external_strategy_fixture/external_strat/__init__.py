"""Synthetic external strategy fixture for the entry-point smoke test.

Mirrors the shape of an in-repo strategy module's STRATEGY_SPEC export so the
discovery + dispatch round-trip can be exercised without depending on any real
external package.

The package is pip-installed (editable) at test setup, then uninstalled at
teardown. The only purpose is to prove that the
``nautilus_trading.strategies`` entry-point group works for third-party code
exactly the same as it works for in-repo strategies.
"""

from __future__ import annotations

from typing import Any

from nautilus_trading.cli._strategy_specs import StrategySpec


class ExternalStratConfigBuilder:
    """Pass-through builder for the synthetic external strategy.

    Mirrors the in-repo ``StrategyConfigBuilder`` Protocol used by
    :mod:`nautilus_trading.cli._strategy_specs` — the test only needs the
    ``build`` method to exist, not to do anything sophisticated.
    """

    def build(self, args: dict[str, Any]) -> dict[str, Any]:
        if not args.get("instrument_id") or not args.get("bar_type"):
            raise ValueError("external_strat requires instrument_id and bar_type")
        return {
            "instrument_id": args["instrument_id"],
            "bar_type": args["bar_type"],
        }


# Entry-point key in ``pyproject.toml`` MUST equal ``STRATEGY_SPEC.name``;
# the discovery in ``cli/_strategy_specs.py`` validates this and raises
# ``RuntimeError`` on mismatch.
STRATEGY_SPEC = StrategySpec(
    name="external_strat",
    builder=ExternalStratConfigBuilder(),
    strategy_path="external_strat.strategy:ExternalStratStrategy",
    config_path="external_strat.strategy:ExternalStratConfig",
)
