"""Regression tests for the three Binance Spot Testnet blocker fixes."""

from __future__ import annotations

from decimal import Decimal

import pytest
from nautilus_trader.adapters.binance import BINANCE
from nautilus_trader.adapters.binance.common.enums import BinanceAccountType, BinanceEnvironment
from nautilus_trader.adapters.binance.config import BinanceKeyType
from nautilus_trader.model.objects import Price
from nautilus_trading.paper_trade.node_config import (
    build_paper_trade_node_config,
    round_to_tick,
)


@pytest.fixture
def sample_config():
    return build_paper_trade_node_config(
        strategy_path="strategies.crypto.ema_cross:EMACrossStrategy",
        config_path="strategies.crypto.ema_cross:EMACrossConfig",
        strategy_config={
            "instrument_id": "BTCUSDT.BINANCE",
            "bar_type": "BTCUSDT.BINANCE-1-MINUTE-LAST-EXTERNAL",
            "trade_size": "0.001",
        },
        instrument_id="BTCUSDT.BINANCE",
    )


def test_key_type_is_ed25519_on_data_client(sample_config):
    """Regression: Ed25519 signing required for user-data WebSocket (2026-04-08)."""
    assert sample_config.data_clients[BINANCE].key_type == BinanceKeyType.ED25519


def test_key_type_is_ed25519_on_exec_client(sample_config):
    """Regression: exec client must also use Ed25519 for consistency."""
    assert sample_config.exec_clients[BINANCE].key_type == BinanceKeyType.ED25519


def test_instrument_provider_loads_target_symbol(sample_config):
    """Regression: default InstrumentProviderConfig() is empty → unknown-instrument errors."""
    from nautilus_trader.model.identifiers import InstrumentId

    provider_cfg = sample_config.data_clients[BINANCE].instrument_provider
    loaded = set(provider_cfg.load_ids)
    assert InstrumentId.from_str("BTCUSDT.BINANCE") in loaded


def test_instrument_provider_populated_on_exec_client(sample_config):
    """Exec client must load the same instrument (parallel cache in Nautilus)."""
    from nautilus_trader.model.identifiers import InstrumentId

    provider_cfg = sample_config.exec_clients[BINANCE].instrument_provider
    assert InstrumentId.from_str("BTCUSDT.BINANCE") in set(provider_cfg.load_ids)


def test_account_type_is_spot(sample_config):
    assert sample_config.data_clients[BINANCE].account_type == BinanceAccountType.SPOT
    assert sample_config.exec_clients[BINANCE].account_type == BinanceAccountType.SPOT


def test_environment_is_testnet(sample_config):
    assert sample_config.data_clients[BINANCE].environment == BinanceEnvironment.TESTNET
    assert sample_config.exec_clients[BINANCE].environment == BinanceEnvironment.TESTNET


class _FakeInstrument:
    """Minimal Instrument shim exposing just what round_to_tick() needs."""

    def __init__(self, tick_size: str, price_precision: int):
        self.price_increment = Price.from_str(tick_size)
        self.price_precision = price_precision


@pytest.mark.parametrize(
    "tick, precision, raw, expected",
    [
        ("0.01", 2, Decimal("100.237"), "100.23"),
        ("0.01", 2, Decimal("100.000"), "100.00"),
        ("0.001", 3, Decimal("0.123456"), "0.123"),
        ("0.00001", 5, Decimal("65432.123456"), "65432.12345"),
    ],
)
def test_round_to_tick_grid(tick, precision, raw, expected):
    inst = _FakeInstrument(tick_size=tick, price_precision=precision)
    price = round_to_tick(raw, inst)
    assert isinstance(price, Price)
    assert str(price) == expected


def test_round_to_tick_truncates_between_tick_grid_points():
    """Prices between two tick boundaries floor down, not round half-even."""
    inst = _FakeInstrument(tick_size="0.01", price_precision=2)
    assert str(round_to_tick(Decimal("100.019"), inst)) == "100.01"


def test_round_to_tick_preserves_exact_grid():
    inst = _FakeInstrument(tick_size="0.01", price_precision=2)
    assert str(round_to_tick(Decimal("100.02"), inst)) == "100.02"


def test_round_to_tick_price_smaller_than_tick_floors_to_zero():
    """Pin current behavior: sub-tick prices floor to 0.

    Strategies upstream are responsible for filtering these out before
    order submission. If this ever changes to raise ValueError instead,
    update the docstring + callers accordingly.
    """
    inst = _FakeInstrument(tick_size="0.01", price_precision=2)
    assert str(round_to_tick(Decimal("0.003"), inst)) == "0.00"
