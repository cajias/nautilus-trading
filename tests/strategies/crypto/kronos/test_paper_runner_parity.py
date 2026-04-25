"""Parity gate — kronos paper-trade `build_config()` must still match the
sub-project A quarantined snapshot on the 5 canonical fields (spec §10).

Field list: account_type, environment, venue (BINANCE registered in clients),
strategy+actor class paths, configured instrument_id.

Sub-project B.5 update: the original test drove this parity via the
now-deleted ``KronosPaperTradeRunner`` shim. The quarantined-snapshot anchor
(``_quarantined_config_snapshot.build_quarantined_config``) is independent
of the shim and remains as-is. We now drive the parity through
:class:`PaperTradeStrategyRunner` + :data:`STRATEGY_SPECS["kronos"]`, which
is the only kronos paper-trade path post-B.5. Semantics preserved end-to-end.
"""

from __future__ import annotations

import pytest
from nautilus_trader.adapters.binance import BINANCE
from nautilus_trader.adapters.binance.common.enums import BinanceAccountType, BinanceEnvironment


def test_kronos_paper_runner_matches_quarantined_script(monkeypatch: pytest.MonkeyPatch):
    # Stub env vars so the quarantined config snapshot and the generic runner's
    # build_config() can read Binance testnet secrets without requiring real
    # credentials. The config-only path never hits the wire; these stubs just
    # satisfy the presence check.
    monkeypatch.setenv("BINANCE_TESTNET_API_KEY", "stub_key_for_config_only")
    monkeypatch.setenv("BINANCE_TESTNET_API_SECRET", "stub_secret_for_config_only")

    from nautilus_trading.cli._strategy_specs import STRATEGY_SPECS
    from nautilus_trading.paper_trade.strategy_runner import PaperTradeStrategyRunner

    from tests.strategies.crypto.kronos._quarantined_config_snapshot import (
        build_quarantined_config,
    )

    instrument_id = "BTCUSDT.BINANCE"
    bar_type = "BTCUSDT.BINANCE-1-HOUR-LAST-EXTERNAL"
    trade_size = "0.001"

    old = build_quarantined_config(
        instrument_id=instrument_id, bar_type=bar_type, trade_size=trade_size
    )
    new = PaperTradeStrategyRunner(
        spec=STRATEGY_SPECS["kronos"],
        params={
            "instrument_id": instrument_id,
            "bar_type": bar_type,
            "trade_size": trade_size,
        },
    ).build_config()

    # 1. account_type on both data and exec clients
    assert (
        new.data_clients[BINANCE].account_type
        == old.data_clients[BINANCE].account_type
        == BinanceAccountType.SPOT
    )
    assert (
        new.exec_clients[BINANCE].account_type
        == old.exec_clients[BINANCE].account_type
        == BinanceAccountType.SPOT
    )

    # 2. environment on both
    assert (
        new.data_clients[BINANCE].environment
        == old.data_clients[BINANCE].environment
        == BinanceEnvironment.TESTNET
    )
    assert (
        new.exec_clients[BINANCE].environment
        == old.exec_clients[BINANCE].environment
        == BinanceEnvironment.TESTNET
    )

    # 3. venue wiring — BINANCE key present in both client dicts
    assert BINANCE in new.data_clients and BINANCE in old.data_clients
    assert BINANCE in new.exec_clients and BINANCE in old.exec_clients

    # 4. strategy + actor class identity
    assert (
        new.strategies[0].strategy_path
        == old.strategies[0].strategy_path
        == "strategies.crypto.kronos.strategy:KronosStrategy"
    )
    assert (
        new.actors[0].actor_path
        == old.actors[0].actor_path
        == "strategies.crypto.kronos.actor:KronosActor"
    )

    # 5. configured symbol matches
    assert (
        new.strategies[0].config["instrument_id"]
        == old.strategies[0].config["instrument_id"]
        == instrument_id
    )
    assert (
        new.actors[0].config["instrument_id"]
        == old.actors[0].config["instrument_id"]
        == instrument_id
    )
