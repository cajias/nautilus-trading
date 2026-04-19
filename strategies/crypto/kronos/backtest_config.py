"""Pure builders for Kronos BacktestEngine configuration.

Split from kronos/backtest.py (SRP fix). No I/O except data-catalog loading
belongs here — keep it testable.
"""

from __future__ import annotations

from decimal import Decimal

from nautilus_trader.backtest.engine import BacktestEngineConfig
from nautilus_trader.backtest.node import BacktestVenueConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.currencies import BNB, BTC, ETH, SOL, USDT, Currency
from nautilus_trader.model.data import BarSpecification, BarType
from nautilus_trader.model.enums import (
    AccountType,
    AggregationSource,
    BarAggregation,
    OmsType,
    PriceType,
)
from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
from nautilus_trader.model.instruments import CurrencyPair
from nautilus_trader.model.objects import Money, Price, Quantity

_BASE_MAP = {"BTC": BTC, "ETH": ETH, "BNB": BNB, "SOL": SOL}


def build_engine_config(*, log_level: str = "ERROR") -> BacktestEngineConfig:
    """Build the BacktestEngineConfig used by Kronos backtests."""
    return BacktestEngineConfig(logging=LoggingConfig(log_level=log_level))


def build_venue_spec(*, initial_capital: Decimal = Decimal("500")) -> BacktestVenueConfig:
    """Build the BINANCE SPOT venue with USDT cash balance."""
    return BacktestVenueConfig(
        name="BINANCE",
        oms_type=OmsType.NETTING,
        account_type=AccountType.CASH,
        base_currency=None,  # multi-currency for SPOT
        starting_balances=[str(Money(initial_capital, USDT))],
    )


def build_instrument(*, symbol: str = "BTCUSDT") -> CurrencyPair:
    """Build a Binance CurrencyPair instrument for the given spot symbol."""
    base_str = symbol.replace("USDT", "").replace("BUSD", "")
    base = _BASE_MAP.get(base_str) or Currency.from_str(base_str)
    instrument_id = InstrumentId(Symbol(symbol), Venue("BINANCE"))
    return CurrencyPair(
        instrument_id=instrument_id,
        raw_symbol=Symbol(symbol),
        base_currency=base,
        quote_currency=USDT,
        price_precision=2,
        size_precision=6,
        price_increment=Price.from_str("0.01"),
        size_increment=Quantity.from_str("0.000001"),
        lot_size=None,
        max_quantity=None,
        min_quantity=Quantity.from_str("0.000001"),
        max_notional=None,
        min_notional=None,
        max_price=None,
        min_price=None,
        margin_init=Decimal("0"),
        margin_maint=Decimal("0"),
        maker_fee=Decimal("0.001"),
        taker_fee=Decimal("0.001"),
        ts_event=0,
        ts_init=0,
    )


_INTERVAL_TO_SPEC = {
    "1h": (1, BarAggregation.HOUR),
    "4h": (4, BarAggregation.HOUR),
    "1d": (1, BarAggregation.DAY),
}


def build_bar_type(instrument: CurrencyPair, *, interval: str = "1h") -> BarType:
    """Build a BarType for the given instrument + interval."""
    if interval not in _INTERVAL_TO_SPEC:
        raise ValueError(f"unsupported interval: {interval}")
    step, aggregation = _INTERVAL_TO_SPEC[interval]
    return BarType(
        instrument_id=instrument.id,
        bar_spec=BarSpecification(step=step, aggregation=aggregation, price_type=PriceType.LAST),
        aggregation_source=AggregationSource.EXTERNAL,
    )
