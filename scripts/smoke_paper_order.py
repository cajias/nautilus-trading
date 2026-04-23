"""Manual smoke test — submit one off-market LIMIT to Binance Spot Testnet, then cancel.

This script is the **strategy-bypass** manual smoke for Task 8.3 of sub-project B
PR 8. It boots the paper-trade node for a named runner, drives the exec engine
directly (NOT through the runner's strategy), submits one far-off-market LIMIT
BUY, asserts an ``OrderAccepted`` ACK, cancels it, waits for ``OrderCanceled``,
and shuts down.

Usage:

    cd nautilus && uv run python ../scripts/smoke_paper_order.py <strategy-key>

Where <strategy-key> is one of the YAML config names under ``configs/paper/``:

    ema_cross grid_bot dca_bot timesfm_swing hybrid_sma_r10
    timesfm_grid rvs_swing shock_guard kronos

Requires Binance Testnet credentials loaded. `load_dotenv_local()` reads
``.env.local`` from the current working directory; the documented invocation
above uses ``cd nautilus && ...``, so place your file at ``nautilus/.env.local``.
Fails loudly — no silent retries, no masked timeouts. Non-zero exit on any
error path so the shell caller can treat it as a gate.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path

# Make the project root importable so `strategies.*` / `nautilus_trading.*`
# resolve from either cwd (script-level) or nautilus/ (uv run -m) invocations.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
_NAUTILUS_SRC = _PROJECT_ROOT / "nautilus" / "src"
if _NAUTILUS_SRC.is_dir() and str(_NAUTILUS_SRC) not in sys.path:
    sys.path.insert(0, str(_NAUTILUS_SRC))

from nautilus_trader.adapters.binance import (  # noqa: E402
    BINANCE,
    BinanceLiveDataClientFactory,
    BinanceLiveExecClientFactory,
)
from nautilus_trader.common.factories import OrderFactory  # noqa: E402
from nautilus_trader.core.uuid import UUID4  # noqa: E402
from nautilus_trader.execution.messages import CancelOrder, SubmitOrder  # noqa: E402
from nautilus_trader.live.node import TradingNode  # noqa: E402
from nautilus_trader.model.enums import OrderSide  # noqa: E402
from nautilus_trader.model.events import OrderAccepted, OrderCanceled  # noqa: E402
from nautilus_trader.model.identifiers import InstrumentId, StrategyId  # noqa: E402
from nautilus_trader.model.objects import Quantity  # noqa: E402
from nautilus_trading.cli.paper_trade import _RUNNERS, _load_runners  # noqa: E402
from nautilus_trading.paper_trade.node_config import round_to_tick  # noqa: E402
from nautilus_trading.paper_trade.run_config import load_run_config  # noqa: E402
from nautilus_trading.paper_trade.secrets import load_dotenv_local  # noqa: E402

# Hard caps — the testnet WebSocket is typically sub-second; generous headroom.
_BOOT_TIMEOUT = 30.0  # seconds to wait for first bar (proxy for "node ready")
_ACK_TIMEOUT = 10.0  # seconds to wait for OrderAccepted
_CANCEL_TIMEOUT = 5.0  # seconds to wait for OrderCanceled

# Strategy id used by the smoke submission. Must be distinct from the runner's
# real strategy id (which owns its own client-order-id counter). The exec engine
# happily routes external commands that carry a made-up strategy id.
_SMOKE_STRATEGY_ID = StrategyId("SMOKE-001")


def _load_registry() -> dict[str, type]:
    """Return the ``_RUNNERS`` dict after triggering lazy population."""
    _load_runners()
    return dict(_RUNNERS)


def _build_runner(
    strategy_key: str,
    config_path: Path,
    registry: dict[str, type],
) -> tuple[object, object]:
    """Load YAML, build kwargs, instantiate the runner — mirroring cli.paper_trade."""
    run_config = load_run_config(config_path)
    if run_config.strategy != strategy_key:
        raise RuntimeError(
            f"YAML at {config_path} declares strategy={run_config.strategy!r} "
            f"but was requested for {strategy_key!r}",
        )
    runner_cls = registry[strategy_key]

    kwargs: dict[str, object] = {
        "instrument_id": run_config.instrument_id,
        "bar_type": run_config.bar_type,
        "log_level": run_config.log_level,
        **run_config.params,
    }
    if run_config.trade_size is not None:
        kwargs["trade_size"] = run_config.trade_size

    return runner_cls(**kwargs), run_config


async def _wait_first_bar(msgbus: object, deadline_seconds: float) -> None:
    """Block until one bar has arrived on the data topic or timeout elapses."""
    seen = {"bar": False}

    def _on_bar(_event: object) -> None:
        seen["bar"] = True

    msgbus.subscribe(topic="data.bars.*", handler=_on_bar)  # type: ignore[attr-defined]

    loop = asyncio.get_running_loop()
    deadline = loop.time() + deadline_seconds
    while not seen["bar"] and loop.time() < deadline:
        await asyncio.sleep(0.25)

    if not seen["bar"]:
        raise TimeoutError(
            f"no bar arrived within {deadline_seconds:.0f}s; node boot never completed",
        )


async def _wait_for_event(
    msgbus: object,
    topic: str,
    predicate: Callable[[object], bool],
    timeout_seconds: float,
    label: str,
) -> object:
    """Wait for the first event on ``topic`` matching ``predicate`` or raise TimeoutError."""
    received: dict[str, object] = {}

    def _on_event(event: object) -> None:
        if "event" in received:
            return
        if predicate(event):
            received["event"] = event

    msgbus.subscribe(topic=topic, handler=_on_event)  # type: ignore[attr-defined]

    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    while "event" not in received and loop.time() < deadline:
        await asyncio.sleep(0.05)

    if "event" not in received:
        raise TimeoutError(f"no {label} arrived within {timeout_seconds:.0f}s")
    return received["event"]


async def _run_smoke(
    strategy_key: str,
    instrument_id_str: str,
    trade_size_str: str,
    node: TradingNode,
) -> None:
    """Drive the full smoke once the node has been built (but not started)."""
    run_task = asyncio.ensure_future(node.run_async())
    try:
        kernel = node.kernel

        # 1. Boot and wait for at least one bar — proxy for "exec engine + data
        #    WS are connected and the instrument has been loaded".
        await _wait_first_bar(kernel.msgbus, _BOOT_TIMEOUT)

        # 2. Resolve instrument from the cache (populated via InstrumentProvider).
        instrument_id = InstrumentId.from_str(instrument_id_str)
        instrument = kernel.cache.instrument(instrument_id)
        if instrument is None:
            raise RuntimeError(
                f"instrument {instrument_id} not in cache after node boot — "
                "InstrumentProvider failed to load",
            )

        # 3. Derive an off-market BUY price = last-bar-close / 2, floored to tick.
        bars = kernel.cache.bars(instrument_id)  # type: ignore[attr-defined]
        if not bars:
            raise RuntimeError(f"no bars cached for {instrument_id}")
        latest_close = Decimal(str(bars[0].close))
        raw_price = latest_close / Decimal("2")
        limit_price = round_to_tick(raw_price, instrument)
        print(
            f"[smoke] strategy={strategy_key} instrument={instrument_id} "
            f"ref_close={latest_close} limit_price={limit_price}",
        )

        # 4. Build the LIMIT BUY order via our own OrderFactory — strategy-bypass.
        factory = OrderFactory(
            trader_id=kernel.trader_id,
            strategy_id=_SMOKE_STRATEGY_ID,
            clock=kernel.clock,
        )
        qty = Quantity.from_str(trade_size_str)
        order = factory.limit(
            instrument_id=instrument_id,
            order_side=OrderSide.BUY,
            quantity=qty,
            price=limit_price,
        )

        # 5. Submit via exec engine directly (the "strategy-bypass" part).
        submit = SubmitOrder(
            trader_id=kernel.trader_id,
            strategy_id=_SMOKE_STRATEGY_ID,
            order=order,
            command_id=UUID4(),
            ts_init=kernel.clock.timestamp_ns(),
        )
        print(f"[smoke] submitting LIMIT BUY client_order_id={order.client_order_id}")
        kernel.exec_engine.execute(submit)  # type: ignore[attr-defined]

        # 6. Wait for ACK.
        accepted = await _wait_for_event(
            kernel.msgbus,
            topic=f"events.order.{_SMOKE_STRATEGY_ID.value}",
            predicate=lambda e: (
                isinstance(e, OrderAccepted) and e.client_order_id == order.client_order_id
            ),
            timeout_seconds=_ACK_TIMEOUT,
            label="OrderAccepted",
        )
        venue_order_id = accepted.venue_order_id  # type: ignore[attr-defined]
        print(f"[smoke] ACK received — venue_order_id={venue_order_id}")

        # 7. Cancel.
        cancel = CancelOrder(
            trader_id=kernel.trader_id,
            strategy_id=_SMOKE_STRATEGY_ID,
            instrument_id=instrument_id,
            client_order_id=order.client_order_id,
            venue_order_id=venue_order_id,
            command_id=UUID4(),
            ts_init=kernel.clock.timestamp_ns(),
        )
        print(f"[smoke] cancelling venue_order_id={venue_order_id}")
        kernel.exec_engine.execute(cancel)  # type: ignore[attr-defined]

        # 8. Wait for cancel confirmation.
        await _wait_for_event(
            kernel.msgbus,
            topic=f"events.order.{_SMOKE_STRATEGY_ID.value}",
            predicate=lambda e: (
                isinstance(e, OrderCanceled) and e.client_order_id == order.client_order_id
            ),
            timeout_seconds=_CANCEL_TIMEOUT,
            label="OrderCanceled",
        )
        print("[smoke] cancel confirmed — shutdown starting")
    finally:
        run_task.cancel()
        try:
            await run_task
        except (asyncio.CancelledError, Exception):
            pass


def main(argv: list[str]) -> int:
    """CLI entry — argv[0] is the strategy key."""
    if len(argv) != 1:
        print("Usage: smoke_paper_order.py <strategy-key>", file=sys.stderr)
        return 2
    strategy_key = argv[0]

    load_dotenv_local()
    registry = _load_registry()

    if strategy_key not in registry:
        valid = " ".join(sorted(registry))
        print(
            f"ERROR: unknown strategy {strategy_key!r}. Valid: {valid}",
            file=sys.stderr,
        )
        return 2

    config_path = _PROJECT_ROOT / "configs" / "paper" / f"{strategy_key}.yaml"
    if not config_path.is_file():
        print(
            f"ERROR: config not found at {config_path}. "
            "Create it alongside the other configs in configs/paper/.",
            file=sys.stderr,
        )
        return 2

    try:
        runner, run_config = _build_runner(strategy_key, config_path, registry)
        node_config = runner.build_config()
    except Exception as exc:  # noqa: BLE001
        print(
            f"ERROR: failed to build runner for strategy={strategy_key!r}: "
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 1

    node = TradingNode(config=node_config)
    node.add_data_client_factory(BINANCE, BinanceLiveDataClientFactory)
    node.add_exec_client_factory(BINANCE, BinanceLiveExecClientFactory)
    node.build()

    try:
        asyncio.run(
            _run_smoke(
                strategy_key=strategy_key,
                instrument_id_str=run_config.instrument_id,
                trade_size_str=run_config.trade_size or "0.001",
                node=node,
            ),
        )
    except Exception as exc:  # noqa: BLE001
        print(
            f"ERROR: smoke failed for strategy={strategy_key!r} "
            f"instrument={run_config.instrument_id!r}: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        try:
            node.stop()
        finally:
            node.dispose()
        return 1

    try:
        node.stop()
    finally:
        node.dispose()
    print(f"[smoke] DONE strategy={strategy_key}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
