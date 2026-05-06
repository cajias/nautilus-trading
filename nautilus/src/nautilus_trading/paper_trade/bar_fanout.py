"""BarFanoutActor — workaround for nautilus_trader's multi-strategy
shared-bar_type dedup bug.

When multiple strategies in a single TradingNode call
`self.subscribe_bars(SAME_BAR_TYPE)`, only the first strategy's
`on_bar` fires. The DataEngine dedups the upstream subscription but
only routes bar messages to the first subscriber's component_id.
Verified empirically 2026-05-06.

This actor is a workaround:

1. The actor subscribes once to the real `BarType`. As the only
   subscriber, no dedup bug applies.
2. On every `on_bar`, the actor wraps the bar in a custom
   `FanoutBar` Data type and publishes via `publish_data`.
3. Each consumer strategy subscribes via
   `subscribe_data(DataType(FanoutBar))` instead of
   `subscribe_bars(...)`.
4. Custom data types support multi-subscriber fan-out (per nautilus's
   kronos actor → strategy pattern in
   `strategies/crypto/kronos/`).

Usage:

```python
# Wire the actor via ImportableActorConfig.
ImportableActorConfig(
    actor_path="nautilus_trading.paper_trade.bar_fanout:BarFanoutActor",
    config_path="nautilus_trading.paper_trade.bar_fanout:BarFanoutActorConfig",
    config={"bar_type": "BTCUSDT.BINANCE-1-HOUR-LAST-EXTERNAL"},
)

# In each consumer Strategy.on_start, replace:
#     self.subscribe_bars(self.config.bar_type)
# with:
#     self.subscribe_data(DataType(FanoutBar))
# and override on_data to unwrap → call on_bar.
```

See also: ``build_multi_strategy_paper_node_config`` in
``nautilus_trading.paper_trade.multi_strategy`` which auto-wires one
``BarFanoutActor`` per unique bar_type across a multi-strategy node.
"""

from __future__ import annotations

from nautilus_trader.common.actor import Actor
from nautilus_trader.config import ActorConfig
from nautilus_trader.core.data import Data
from nautilus_trader.model.data import Bar, BarType, DataType


class FanoutBar(Data):
    """Custom Data type wrapping a Bar for multi-subscriber fan-out.

    Subclasses nautilus's Cython `Data` so it can be used with
    `publish_data` / `subscribe_data` / `DataType(FanoutBar)`.

    The `Data` base class exposes `ts_event` / `ts_init` as read-only
    Cython properties backed by `_ts_event` / `_ts_init` — subclasses
    must assign those private names directly.
    """

    def __init__(self, bar: Bar) -> None:
        super().__init__()
        self.bar = bar
        self._ts_event = bar.ts_event
        self._ts_init = bar.ts_init

    def __repr__(self) -> str:  # pragma: no cover — diagnostic only
        return f"FanoutBar(bar={self.bar!r})"


class BarFanoutActorConfig(ActorConfig, frozen=True):
    """Config for the bar-fanout actor.

    Parameters
    ----------
    bar_type : str
        The bar_type the actor subscribes to (e.g.
        "BTCUSDT.BINANCE-1-HOUR-LAST-EXTERNAL"). Strategies in the same
        node subscribe to FanoutBar via subscribe_data, NOT to this
        bar_type directly.
    """

    bar_type: str


class BarFanoutActor(Actor):
    """Subscribes once to a real BarType, republishes each Bar as a
    `FanoutBar` so multi-strategy nodes can have N consumers without
    hitting nautilus_trader's shared-subscription dedup bug.
    """

    def __init__(self, config: BarFanoutActorConfig) -> None:
        super().__init__(config)
        self._bar_type: BarType = BarType.from_str(config.bar_type)
        self._published: int = 0

    def on_start(self) -> None:
        self.subscribe_bars(self._bar_type)
        self.log.info(f"BarFanoutActor: subscribed to {self._bar_type}")

    def on_bar(self, bar: Bar) -> None:
        wrapped = FanoutBar(bar)
        self.publish_data(DataType(FanoutBar), wrapped)
        self._published += 1
        if self._published <= 3 or self._published % 30 == 0:
            self.log.info(
                f"BarFanoutActor: published bar #{self._published} (close={bar.close}, ts={bar.ts_event})",
            )

    def on_stop(self) -> None:
        self.log.info(
            f"BarFanoutActor: stopping after publishing {self._published} bars",
        )
