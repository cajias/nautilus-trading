"""Tests for strategies.crypto.kronos.backtest_config builders."""

from __future__ import annotations

from decimal import Decimal


def test_build_engine_config_returns_engine_config():
    from nautilus_trader.backtest.engine import BacktestEngineConfig
    from strategies.crypto.kronos.backtest_config import build_engine_config

    cfg = build_engine_config(log_level="ERROR")
    assert isinstance(cfg, BacktestEngineConfig)


def test_build_venue_spec_binance_spot_usdt():
    from nautilus_trader.model.objects import Money
    from strategies.crypto.kronos.backtest_config import build_venue_spec

    spec = build_venue_spec(initial_capital=Decimal("500"))
    assert spec.name == "BINANCE"
    assert spec.oms_type.name == "NETTING"
    assert spec.account_type.name == "CASH"
    balance = Money.from_str(spec.starting_balances[0])
    assert balance.as_double() == 500.0


def test_build_instrument_btcusdt_returns_currency_pair():
    from nautilus_trader.model.instruments import CurrencyPair
    from strategies.crypto.kronos.backtest_config import build_instrument

    inst = build_instrument(symbol="BTCUSDT")
    assert isinstance(inst, CurrencyPair)
    assert str(inst.id) == "BTCUSDT.BINANCE"


def test_build_bar_type_hourly():
    from strategies.crypto.kronos.backtest_config import build_bar_type, build_instrument

    inst = build_instrument(symbol="BTCUSDT")
    bar_type = build_bar_type(inst, interval="1h")
    assert str(bar_type).endswith("-1-HOUR-LAST-EXTERNAL")


def test_build_instrument_accepts_non_registered_symbol():
    from strategies.crypto.kronos.backtest_config import build_instrument

    inst = build_instrument(symbol="DOGEUSDT")
    assert str(inst.id) == "DOGEUSDT.BINANCE"
    assert inst.base_currency.code == "DOGE"


def test_build_instrument_min_quantity_is_1e_minus_6():
    from strategies.crypto.kronos.backtest_config import build_instrument

    inst = build_instrument(symbol="BTCUSDT")
    assert str(inst.min_quantity) == "0.000001"


def test_build_bar_type_rejects_unsupported_interval():
    import pytest
    from strategies.crypto.kronos.backtest_config import build_bar_type, build_instrument

    inst = build_instrument(symbol="BTCUSDT")
    with pytest.raises(ValueError):
        build_bar_type(inst, interval="2h")
