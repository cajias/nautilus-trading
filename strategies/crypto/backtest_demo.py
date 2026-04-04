"""Demo backtest for crypto strategies using real Binance data.

Downloads historical kline (OHLCV) data from the Binance public REST API
for BTCUSDT, ETHUSDT, and SOLUSDT, then backtests Grid Bot, DCA Bot,
and TimesFM Swing strategies on each pair.

Usage:
    cd nautilus && uv run python ../strategies/crypto/backtest_demo.py
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import AccountType, OmsType
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.objects import Money
from nautilus_trader.persistence.catalog import ParquetDataCatalog

# Ensure strategies/ and nautilus/src are importable
_project_root = str(Path(__file__).resolve().parents[2])
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

_nautilus_src = str(Path(__file__).resolve().parents[2] / "nautilus" / "src")
if _nautilus_src not in sys.path:
    sys.path.insert(0, _nautilus_src)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PAIRS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT",
    "DOGEUSDT", "LINKUSDT", "PEPEUSDT", "NEARUSDT", "SUIUSDT",
]
VENUE = Venue("BINANCE")
CATALOG_DIR = Path(_project_root) / "catalog" / "binance"
STARTING_CAPITAL = Decimal("500")
INTERVAL = "1h"  # kline interval used by BinanceDataProvider

# Trade sizes appropriate for each pair at current price levels
TRADE_SIZES: dict[str, Decimal] = {
    "BTCUSDT": Decimal("0.00100"),   # ~$0.07 worth at ~$67k
    "ETHUSDT": Decimal("0.0100"),    # ~$0.21 worth at ~$2050
    "SOLUSDT": Decimal("0.10"),      # ~$0.08 worth at ~$81
    "DOGEUSDT": Decimal("100"),      # ~$9.15 worth at ~$0.09
    "LINKUSDT": Decimal("1.00"),     # ~$8.68 worth at ~$8.68
    "PEPEUSDT": Decimal("1000000"),  # ~$3.40 worth at ~$0.0000034
    "NEARUSDT": Decimal("5.0"),      # ~$6.30 worth at ~$1.26
    "SUIUSDT": Decimal("5.0"),       # ~$4.35 worth at ~$0.87
}

# DCA budget per buy cycle (USDT) -- split across pairs
DCA_BUDGET_SPLIT: dict[str, Decimal] = {
    "BTCUSDT": Decimal("10.0"),
    "ETHUSDT": Decimal("8.0"),
    "SOLUSDT": Decimal("5.0"),
    "DOGEUSDT": Decimal("5.0"),
    "LINKUSDT": Decimal("5.0"),
    "PEPEUSDT": Decimal("3.0"),
    "NEARUSDT": Decimal("4.0"),
    "SUIUSDT": Decimal("4.0"),
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_usdt_currency():
    """Return USDT currency, creating it if not already registered."""
    from nautilus_trader.model.enums import CurrencyType
    from nautilus_trader.model.objects import Currency

    usdt = Currency.from_internal_map("USDT")
    if usdt is not None:
        return usdt
    return Currency(
        code="USDT",
        precision=2,
        iso4217=0,
        name="USDT",
        currency_type=CurrencyType.CRYPTO,
    )


def _load_catalog() -> ParquetDataCatalog:
    """Download Binance kline data (if needed) and return the catalog."""
    from nautilus_trading.data.providers import BinanceDataProvider

    now = datetime.now(timezone.utc)
    provider = BinanceDataProvider(
        pairs=PAIRS,
        interval=INTERVAL,
        start_date=now - timedelta(days=365),
    )
    return provider.ensure_catalog(CATALOG_DIR)


def _get_bars_for_pair(
    catalog: ParquetDataCatalog,
    pair: str,
) -> list[Bar]:
    """Load bars from the catalog for a given pair."""
    instrument_id_str = f"{pair}.BINANCE"
    return catalog.bars(instrument_ids=[instrument_id_str])


def _derive_grid_bounds(
    bars: list[Bar],
    buffer_pct: float = 0.05,
    precision: int = 2,
) -> tuple[Decimal, Decimal]:
    """Derive upper/lower grid bounds from bar price range with a buffer."""
    prices = [float(bar.close) for bar in bars]
    min_price = min(prices)
    max_price = max(prices)
    spread = max_price - min_price
    lower = min_price - spread * buffer_pct
    upper = max_price + spread * buffer_pct
    return Decimal(str(round(lower, precision))), Decimal(str(round(upper, precision)))


def _create_engine(starting_balance: Money) -> BacktestEngine:
    """Create a BacktestEngine configured for Binance crypto spot."""
    engine = BacktestEngine(
        config=BacktestEngineConfig(
            logging=LoggingConfig(log_level="ERROR"),
        ),
    )
    engine.add_venue(
        venue=VENUE,
        oms_type=OmsType.NETTING,
        account_type=AccountType.MARGIN,
        base_currency=None,
        starting_balances=[starting_balance],
    )
    return engine


def _get_instrument(catalog: ParquetDataCatalog, pair: str):
    """Retrieve a single instrument from the catalog by pair symbol."""
    instrument_id_str = f"{pair}.BINANCE"
    for inst in catalog.instruments():
        if str(inst.id) == instrument_id_str:
            return inst
    return None


def _print_results(engine: BacktestEngine, label: str) -> None:
    """Print summary results for a backtest run."""
    account = engine.cache.account_for_venue(VENUE)
    balances = account.balances() if account else "N/A"
    open_pos = engine.cache.positions_open_count()
    closed_pos = engine.cache.positions_closed_count()
    total_orders = engine.cache.orders_total_count()
    print(f"\n  [{label}]")
    print(f"  Account balances : {balances}")
    print(f"  Open positions   : {open_pos}")
    print(f"  Closed positions : {closed_pos}")
    print(f"  Total orders     : {total_orders}")


def _bar_type_str(pair: str) -> str:
    """Return the BarType string for external 1-hour bars from Binance."""
    return f"{pair}.BINANCE-1-HOUR-LAST-EXTERNAL"


# ---------------------------------------------------------------------------
# Strategy backtests
# ---------------------------------------------------------------------------


def run_grid_bot(catalog: ParquetDataCatalog) -> None:
    """Backtest Grid Bot on each crypto pair."""
    from strategies.crypto.grid_bot import GridBotConfig, GridBotStrategy

    print("\n" + "=" * 60)
    print("GRID BOT BACKTEST (Binance Crypto)")
    print("=" * 60)

    usdt = _get_usdt_currency()

    for pair in PAIRS:
        bars = _get_bars_for_pair(catalog, pair)
        if not bars:
            print(f"  [SKIP] No bars for {pair}")
            continue

        instrument = _get_instrument(catalog, pair)
        if instrument is None:
            print(f"  [SKIP] No instrument for {pair}")
            continue

        lower_price, upper_price = _derive_grid_bounds(bars, precision=instrument.price_precision)

        engine = _create_engine(Money(STARTING_CAPITAL, usdt))
        engine.add_instrument(instrument)
        engine.add_data(bars)

        bar_type = BarType.from_str(_bar_type_str(pair))
        config = GridBotConfig(
            instrument_id=instrument.id,
            bar_type=bar_type,
            trade_size=TRADE_SIZES.get(pair, Decimal("0.001")),
            upper_price=upper_price,
            lower_price=lower_price,
            grid_levels=15,
            max_open_orders=8,
        )
        strategy = GridBotStrategy(config=config)
        engine.add_strategy(strategy)
        engine.run()

        _print_results(engine, f"Grid Bot | {pair}")
        print(f"  Grid range       : {lower_price} - {upper_price}")
        engine.dispose()


def run_dca_bot(catalog: ParquetDataCatalog) -> None:
    """Backtest DCA Bot on each crypto pair."""
    from strategies.crypto.dca_bot import DCABotConfig, DCABotStrategy

    print("\n" + "=" * 60)
    print("DCA BOT BACKTEST (Binance Crypto)")
    print("=" * 60)

    usdt = _get_usdt_currency()

    for pair in PAIRS:
        bars = _get_bars_for_pair(catalog, pair)
        if not bars:
            print(f"  [SKIP] No bars for {pair}")
            continue

        instrument = _get_instrument(catalog, pair)
        if instrument is None:
            print(f"  [SKIP] No instrument for {pair}")
            continue

        buy_amount = DCA_BUDGET_SPLIT.get(pair, Decimal("5.0"))

        engine = _create_engine(Money(STARTING_CAPITAL, usdt))
        engine.add_instrument(instrument)
        engine.add_data(bars)

        bar_type = BarType.from_str(_bar_type_str(pair))
        config = DCABotConfig(
            instrument_id=instrument.id,
            bar_type=bar_type,
            buy_amount=buy_amount,
            buy_interval_bars=24,  # Buy every ~24 hours (24 x 1h bars)
            use_rsi_filter=True,
            rsi_overbought=0.70,
            take_profit_pct=0.10,  # 10% take profit (crypto is volatile)
            stop_loss_pct=0.08,    # 8% stop loss
        )
        strategy = DCABotStrategy(config=config)
        engine.add_strategy(strategy)
        engine.run()

        _print_results(engine, f"DCA Bot | {pair}")
        print(f"  Buy amount/cycle : {buy_amount} USDT")
        engine.dispose()


def run_timesfm_swing(catalog: ParquetDataCatalog) -> None:
    """Backtest TimesFM Swing on each crypto pair (EMA fallback if no TimesFM)."""
    from strategies.crypto.timesfm_swing import TimesFMSwingConfig, TimesFMSwingStrategy

    print("\n" + "=" * 60)
    print("TIMESFM SWING BACKTEST (Binance Crypto)")
    print("=" * 60)

    usdt = _get_usdt_currency()

    for pair in PAIRS:
        bars = _get_bars_for_pair(catalog, pair)
        if not bars:
            print(f"  [SKIP] No bars for {pair}")
            continue

        instrument = _get_instrument(catalog, pair)
        if instrument is None:
            print(f"  [SKIP] No instrument for {pair}")
            continue

        engine = _create_engine(Money(STARTING_CAPITAL, usdt))
        engine.add_instrument(instrument)
        engine.add_data(bars)

        bar_type = BarType.from_str(_bar_type_str(pair))
        config = TimesFMSwingConfig(
            instrument_id=instrument.id,
            bar_type=bar_type,
            trade_size=TRADE_SIZES.get(pair, Decimal("0.001")),
            lookback_bars=256,
            forecast_horizon=12,
            forecast_interval_bars=4,
            confidence_threshold=0.6,
            ema_period=200,         # Full 200 EMA for 365-day window
            stop_loss_pct=0.03,     # 3% stop loss (crypto volatility)
            take_profit_pct=0.06,   # 6% take profit
        )
        strategy = TimesFMSwingStrategy(config=config)
        engine.add_strategy(strategy)
        engine.run()

        _print_results(engine, f"TimesFM Swing | {pair}")
        engine.dispose()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 60)
    print("CRYPTO STRATEGY BACKTEST DEMO")
    print(f"Starting capital: ${STARTING_CAPITAL} USDT per strategy per pair")
    print(f"Pairs: {', '.join(PAIRS)}")
    print(f"Data: Binance {INTERVAL} klines (last 365 days)")
    print("=" * 60)

    catalog = _load_catalog()

    run_grid_bot(catalog)
    run_dca_bot(catalog)
    run_timesfm_swing(catalog)

    print("\n" + "=" * 60)
    print("ALL BACKTESTS COMPLETE")
    print("=" * 60)
