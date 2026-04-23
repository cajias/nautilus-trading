"""Opt-in Binance Spot Testnet node-boot smoke for all 9 paper-trade strategies.

This suite is the canonical **pre-release gate** for the paper-trade layer.
It is INTENTIONALLY excluded from default CI because:

  (a) it requires live Binance Spot Testnet credentials
      (``BINANCE_TESTNET_API_KEY`` + ``BINANCE_TESTNET_API_SECRET``) and a
      readable Ed25519 PEM at ``BINANCE_TESTNET_ED25519_KEY_PATH``;
  (b) it boots a real ``TradingNode`` per strategy, opens WebSocket connections
      to ``testnet.binance.vision``, and subscribes to live bar data — none of
      which is appropriate for unattended CI runs;
  (c) it is the final hand-crank before cutting a paper-trade release:
      every strategy must boot, authenticate, receive at least one Bar inside a
      30-second window, and shut down cleanly.

Default ``make test-unit`` / CI never runs these tests — the module-level
``pytest.mark.binance_testnet`` tag + autouse credential-probe fixture ensure
collection in any credential-less environment resolves to SKIP (never ERROR,
never fail).

Run locally with credentials loaded:

    cd nautilus && uv run python -m pytest -m binance_testnet ../tests/

Sub-project B.5: the 9 per-strategy ``*_paper.py`` shims are gone. Factories
now construct :class:`PaperTradeStrategyRunner` directly from the unified
:data:`STRATEGY_SPECS` registry. Test logic and assertions are unchanged
from the pre-B.5 version — only the runner-construction surface changed.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from pathlib import Path

import pytest
from nautilus_trader.adapters.binance import (
    BINANCE,
    BinanceLiveDataClientFactory,
    BinanceLiveExecClientFactory,
)
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.data import Bar
from nautilus_trading.cli._strategy_specs import STRATEGY_SPECS
from nautilus_trading.paper_trade.strategy_runner import PaperTradeStrategyRunner

from nautilus_trading.paper_trade.secrets import load_dotenv_local

# Whole module is opt-in — requires live testnet credentials.
pytestmark = pytest.mark.binance_testnet


# --------------------------------------------------------------------------- #
# Common smoke inputs
# --------------------------------------------------------------------------- #

# Short-horizon BTCUSDT testnet defaults. Grid/ML strategies take plausible
# placeholder prices; actual numeric fit is irrelevant — we only assert that
# the node boots, authenticates, and receives at least one Bar.
_INSTRUMENT_ID = "BTCUSDT.BINANCE"
_BAR_TYPE = "BTCUSDT.BINANCE-1-MINUTE-LAST-EXTERNAL"
_TRADE_SIZE = "0.001"

# Hard cap for per-strategy boot + bar-receipt. Keeps the suite bounded when
# Binance Testnet is sluggish; failure modes (no bars in 30 s) are treated
# as a smoke failure, not a timeout.
_BOOT_TIMEOUT_SECONDS = 30.0


# --------------------------------------------------------------------------- #
# Credential guard — skip if env isn't prepped
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def _require_testnet_credentials() -> None:
    """Skip every test in this module unless live testnet credentials are set.

    Mirrors the real preflight in ``paper_trade.node_config._check_testnet_api_keys``:
    we require the API key/secret AND a readable Ed25519 PEM. Any missing or
    unreadable credential results in a ``pytest.skip`` — never a collection error.

    ``load_dotenv_local()`` is called first so operators can simply drop
    ``nautilus/.env.local`` (cwd-relative, matching ``nt paper-trade``) and
    invoke the suite via ``cd nautilus && uv run python -m pytest -m binance_testnet ...``
    without exporting every variable by hand.
    """
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
# Runner factories — one per strategy in STRATEGY_SPECS
# --------------------------------------------------------------------------- #
#
# Each factory returns a PaperTradeStrategyRunner parameterized by the spec
# + a strategy-appropriate params dict. Mirrors the per-strategy placeholder
# values the pre-B.5 shim constructors took — we're verifying boot-time
# wiring, not numeric fit.


def _runner(spec_name: str, **extra_params: object) -> PaperTradeStrategyRunner:
    params: dict[str, object] = {
        "instrument_id": _INSTRUMENT_ID,
        "bar_type": _BAR_TYPE,
        "trade_size": _TRADE_SIZE,
        **extra_params,
    }
    return PaperTradeStrategyRunner(spec=STRATEGY_SPECS[spec_name], params=params)


def _ema_cross_runner() -> PaperTradeStrategyRunner:
    return _runner("ema_cross", fast_ema=10, slow_ema=20)


def _grid_bot_runner() -> PaperTradeStrategyRunner:
    return _runner(
        "grid_bot",
        upper_price="72000",
        lower_price="60000",
        grid_levels=8,
    )


def _dca_bot_runner() -> PaperTradeStrategyRunner:
    return _runner("dca_bot", buy_interval_bars=60, buy_amount="10")


def _timesfm_swing_runner() -> PaperTradeStrategyRunner:
    return _runner("timesfm_swing", fast_ema=10, slow_ema=20)


def _hybrid_sma_r10_runner() -> PaperTradeStrategyRunner:
    # HybridSMA sizes from equity — trade_size is passed but ignored by the
    # builder; it's present here for uniform params-dict construction.
    return _runner(
        "hybrid_sma_r10",
        sma_fast=10,
        sma_slow=30,
        stop_fast="0.05",
        stop_slow="0.10",
    )


def _timesfm_grid_runner() -> PaperTradeStrategyRunner:
    return _runner("timesfm_grid")


def _rvs_swing_runner() -> PaperTradeStrategyRunner:
    return _runner("rvs_swing")


def _shock_guard_runner() -> PaperTradeStrategyRunner:
    return _runner("shock_guard")


def _kronos_runner() -> PaperTradeStrategyRunner:
    return _runner("kronos")


RUNNER_CASES: list[tuple[str, Callable[[], PaperTradeStrategyRunner]]] = [
    ("ema_cross", _ema_cross_runner),
    ("grid_bot", _grid_bot_runner),
    ("dca_bot", _dca_bot_runner),
    ("timesfm_swing", _timesfm_swing_runner),
    ("hybrid_sma_r10", _hybrid_sma_r10_runner),
    ("timesfm_grid", _timesfm_grid_runner),
    ("rvs_swing", _rvs_swing_runner),
    ("shock_guard", _shock_guard_runner),
    ("kronos", _kronos_runner),
]


# --------------------------------------------------------------------------- #
# Node-boot smoke
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("runner_name", "factory"),
    RUNNER_CASES,
    ids=[name for name, _ in RUNNER_CASES],
)
def test_node_boots_and_receives_data(
    runner_name: str,
    factory: Callable[[], PaperTradeStrategyRunner],
) -> None:
    """Boot each strategy's TradingNode, assert at least one Bar inside 30 s.

    Mirrors the node-wiring bits of ``paper_trade.node_config.run_paper_trade``
    (client factories + build) but replaces ``node.run()`` — which blocks on
    SIGINT — with a bounded ``asyncio.wait_for(node.run_async(), timeout)``.
    We subscribe a Bar counter to the message bus BEFORE the node starts so
    no early events are missed, then cancel the run task once we've either
    seen a bar or exhausted the timeout.
    """
    runner = factory()
    config = runner.build_config()

    node = TradingNode(config=config)
    node.add_data_client_factory(BINANCE, BinanceLiveDataClientFactory)
    node.add_exec_client_factory(BINANCE, BinanceLiveExecClientFactory)
    node.build()

    bar_count = 0

    def _count_bar(event: object) -> None:
        nonlocal bar_count
        if isinstance(event, Bar):
            bar_count += 1

    # Subscribe before boot — any bar published during subscribe setup counts.
    node.kernel.msgbus.subscribe(topic="data.bars.*", handler=_count_bar)

    loop = node.kernel.loop

    async def _boot_and_wait() -> None:
        run_task = asyncio.ensure_future(node.run_async())
        try:
            # Poll for the first bar with a hard cap. Short poll interval keeps
            # the test responsive without busy-waiting.
            deadline = loop.time() + _BOOT_TIMEOUT_SECONDS
            while bar_count == 0 and loop.time() < deadline:
                await asyncio.sleep(0.5)
        finally:
            run_task.cancel()
            try:
                await run_task
            except asyncio.CancelledError:
                # Cancellation during bounded shutdown is expected.
                pass
            # Any other exception from run_async() — including an auth failure
            # that caused the boot wait to time out — must surface so the
            # operator sees the real cause, not a generic "no bars" assertion.

    try:
        loop.run_until_complete(_boot_and_wait())
        assert bar_count > 0, (
            f"runner={runner_name!r} booted but no Bar arrived within {_BOOT_TIMEOUT_SECONDS:.0f}s"
        )
    finally:
        # Best-effort teardown; assertions above already bubbled up if relevant.
        try:
            node.stop()
        finally:
            node.dispose()
