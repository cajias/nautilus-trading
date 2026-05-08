"""Multi-strategy paper-trade smoke — would catch the upstream
shared-bar_type dedup bug if it regresses.

This test boots a real Binance Spot Testnet ``TradingNode`` with N
``BarCounter*`` strategies, each subscribed to ``DataType(FanoutBar)``,
plus one :class:`BarFanoutActor` for the shared bar_type. After up to
90s (1m bars need ~60s for the first close), every strategy must have
seen at least one ``FanoutBar`` on its ``on_data``.

Like ``test_smoke_paper.py`` this is opt-in (gated by
``binance_testnet`` and an autouse credentials fixture); default
``make test`` skips it.

Parameterized over a single value N=2 — the smallest N that exercises
the multi-subscriber fan-out path. Higher N values were dropped because
nautilus_trader's global Rust logger is a process-singleton and rebooting
a second TradingNode in the same pytest session aborts the process; the
rationale is documented in detail on the test function's docstring. If
the upstream bug regressed (or this test stopped using FanoutBar), only
the first strategy's counter would be > 0 and the assertion would fail.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
from nautilus_trader.adapters.binance import (
    BINANCE,
    BinanceLiveDataClientFactory,
    BinanceLiveExecClientFactory,
)
from nautilus_trader.config import (
    ImportableActorConfig,
    ImportableStrategyConfig,
    StrategyConfig,
)
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.data import DataType
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.trading.strategy import Strategy

from nautilus_trading.paper_trade.bar_fanout import FanoutBar
from nautilus_trading.paper_trade.multi_strategy import (
    build_multi_strategy_paper_node_config,
)
from nautilus_trading.paper_trade.secrets import load_dotenv_local

# Whole module is opt-in — requires live testnet credentials.
pytestmark = pytest.mark.binance_testnet


# --------------------------------------------------------------------------- #
# Common smoke inputs
# --------------------------------------------------------------------------- #

_INSTRUMENT_ID = "BTCUSDT.BINANCE"
_BAR_TYPE = "BTCUSDT.BINANCE-1-MINUTE-LAST-EXTERNAL"

# 1m bars need ~60s for the first close on Testnet. 90s gives one full bar
# plus slack for boot + auth.
_BOOT_TIMEOUT_SECONDS = 90.0


# --------------------------------------------------------------------------- #
# Credential guard — mirror test_smoke_paper.py
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def _require_testnet_credentials() -> None:
    """Skip every test in this module unless live testnet credentials are set."""
    load_dotenv_local()
    if not os.environ.get("BINANCE_TESTNET_API_KEY"):
        pytest.skip("BINANCE_TESTNET_API_KEY not set; opt-in smoke gated off.")
    if not os.environ.get("BINANCE_TESTNET_API_SECRET"):
        pytest.skip("BINANCE_TESTNET_API_SECRET not set; opt-in smoke gated off.")

    ed25519_path = os.environ.get("BINANCE_TESTNET_ED25519_KEY_PATH")
    if not ed25519_path:
        pytest.skip("BINANCE_TESTNET_ED25519_KEY_PATH not set; user-data WebSocket needs Ed25519.")

    pem = Path(ed25519_path)
    if not pem.is_file() or not os.access(pem, os.R_OK):
        pytest.skip(f"Ed25519 PEM at {ed25519_path} is not readable; opt-in smoke gated off.")


# --------------------------------------------------------------------------- #
# Minimal counter strategies — three distinct classes so each gets a unique
# component_id under the default ImportableStrategyConfig wiring.
# --------------------------------------------------------------------------- #


class _BarCounterConfig(StrategyConfig, frozen=True):
    """Config for the inline BarCounter* test strategies."""


class _BarCounterBase(Strategy):
    """Subscribes to FanoutBar and counts on_data hits.

    Subclassed (not parametrized) so each instance gets a distinct
    Strategy class id; nautilus's strategy_id derivation per
    ``ImportableStrategyConfig`` keys off the class name.
    """

    def __init__(self, config: _BarCounterConfig) -> None:
        super().__init__(config)
        self._on_data_count: int = 0

    def on_start(self) -> None:
        self.subscribe_data(DataType(FanoutBar))

    def on_data(self, data: object) -> None:
        if isinstance(data, FanoutBar):
            self._on_data_count += 1


class BarCounterA(_BarCounterBase):
    pass


class BarCounterB(_BarCounterBase):
    pass


class BarCounterC(_BarCounterBase):
    pass


_COUNTER_CLASSES: list[type[_BarCounterBase]] = [BarCounterA, BarCounterB, BarCounterC]


def _strategy_module_path() -> str:
    """Module dotted path for ImportableStrategyConfig — these classes live
    in this test module itself (pytest imports it under its real module name)."""
    return __name__


# --------------------------------------------------------------------------- #
# Smoke
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("n_strategies", [2], ids=["n=2"])
def test_multi_strategy_fanout_delivers_to_all_consumers(n_strategies: int) -> None:
    """Boot N BarCounter* strategies + 1 BarFanoutActor; assert every
    strategy's per-instance counter > 0 after the boot window.

    N=2 is sufficient as a regression guard for the multi-subscriber fan-out
    path that BarFanoutActor exists to enable: the upstream dedup bug only
    appears at N>=2, so failing at N=2 is the canonical signal.

    Why a single parametrize value rather than [1, 2, 3]: nautilus_trader's
    global Rust logger panics on second init within the same Python process,
    and the local memory note ``nautilus-trader-logger-singleton.md``
    documents the failure mode. Sweeping N would instantiate ``TradingNode``
    three times in one pytest process and crash on the second boot. A full
    N-sweep regression test belongs in a separate subprocess-per-N harness;
    this single-value smoke is the cheapest gate that still catches the
    upstream regression class.
    """
    module_path = _strategy_module_path()
    chosen = _COUNTER_CLASSES[:n_strategies]

    strategy_configs = [
        ImportableStrategyConfig(
            strategy_path=f"{module_path}:{cls.__name__}",
            config_path=f"{module_path}:_BarCounterConfig",
            config={},
        )
        for cls in chosen
    ]

    # Build the node config via the builder under test, then override the
    # actors with a single fixed component_id so we know how to find it later.
    # (The builder already wires this — we just exercise its API here.)
    node_config = build_multi_strategy_paper_node_config(
        strategy_configs=strategy_configs,
        bar_types={_BAR_TYPE},
        instrument_ids={InstrumentId.from_str(_INSTRUMENT_ID)},
    )

    # Smoke that the builder produced exactly one BarFanoutActor for the
    # single bar_type — pre-flight the wiring before booting.
    assert len(node_config.actors) == 1, (
        f"Expected 1 BarFanoutActor for 1 unique bar_type; got {len(node_config.actors)}"
    )
    actor_cfg = node_config.actors[0]
    assert isinstance(actor_cfg, ImportableActorConfig)
    assert "BarFanoutActor" in actor_cfg.actor_path

    node = TradingNode(config=node_config)
    node.add_data_client_factory(BINANCE, BinanceLiveDataClientFactory)
    node.add_exec_client_factory(BINANCE, BinanceLiveExecClientFactory)
    node.build()

    # Resolve live strategy instances from the trader so we can read the
    # per-instance _on_data_count after the boot window.
    trader = node.trader
    strategies_live: list[_BarCounterBase] = [
        s for s in trader.strategies() if isinstance(s, _BarCounterBase)
    ]
    assert len(strategies_live) == n_strategies, (
        f"Expected {n_strategies} live BarCounter* strategies; got {len(strategies_live)}"
    )

    loop = node.kernel.loop

    async def _boot_and_wait() -> None:
        run_task = asyncio.ensure_future(node.run_async())
        try:
            await asyncio.wait_for(
                _wait_until_all_strategies_have_data(strategies_live),
                timeout=_BOOT_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            # Fall through — the assertion below produces the actionable diagnostic.
            pass
        finally:
            run_task.cancel()
            try:
                await run_task
            except asyncio.CancelledError:
                pass

    async def _wait_until_all_strategies_have_data(
        strategies: list[_BarCounterBase],
    ) -> None:
        while True:
            if all(s._on_data_count > 0 for s in strategies):
                return
            await asyncio.sleep(0.5)

    try:
        loop.run_until_complete(_boot_and_wait())

        counts = {type(s).__name__: s._on_data_count for s in strategies_live}
        zero = [name for name, c in counts.items() if c == 0]
        assert not zero, (
            f"n_strategies={n_strategies}: strategies with zero FanoutBar "
            f"deliveries within {_BOOT_TIMEOUT_SECONDS:.0f}s: {zero}. "
            f"All counts: {counts}. "
            "If only the first counter is > 0, the upstream multi-strategy "
            "shared-bar_type dedup bug has regressed and BarFanoutActor's "
            "FanoutBar fan-out is no longer routing to all subscribers."
        )
    finally:
        try:
            node.stop()
        finally:
            node.dispose()
