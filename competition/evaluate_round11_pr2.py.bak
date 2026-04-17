"""
Round 11 Competition Evaluator — NautilusTrader BacktestEngine.

Unlike rounds 1–10 (which used custom pandas simulations), this evaluator
runs strategies through NautilusTrader's actual BacktestEngine. The winning
strategy is paper-trade-ready on Binance Testnet.

Evaluation period: hidden (set EVAL_START / EVAL_END before running).
Starting capital: $500 USDT (Spot account, single asset).

Usage:
    cd nautilus && uv run python ../competition/evaluate_round11.py

Strategies must satisfy ROUND11_CONTRACT.md. Run validate_strategy.py first.
"""

from __future__ import annotations

import importlib.util
import inspect
import math
import sys
import time
import traceback
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

_HERE = Path(__file__).parent
_PROJECT_ROOT = _HERE.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
if str(_PROJECT_ROOT / "nautilus" / "src") not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT / "nautilus" / "src"))

from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.currencies import USDT
from nautilus_trader.model.data import Bar, BarSpecification, BarType
from nautilus_trader.model.enums import AccountType, AggregationSource, BarAggregation, OmsType, PriceType
from nautilus_trader.model.identifiers import InstrumentId, Venue
from nautilus_trader.model.instruments import CurrencyPair
from nautilus_trader.model.objects import Money, Price, Quantity
from nautilus_trader.test_kit.providers import TestInstrumentProvider

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

EVAL_START = "2025-07-01"
EVAL_END = "2025-12-31"
INITIAL_CAPITAL_USDT = 500.0

# BINANCE venue (simulated)
VENUE = Venue("BINANCE")
INSTRUMENT_ID = InstrumentId.from_str("BTCUSDT.BINANCE")

# Bar type: 1-hour bars aggregated from external (Binance) data
BAR_INTERVAL = "1-HOUR-LAST-EXTERNAL"
BAR_TYPE_STR = f"BTCUSDT.BINANCE-{BAR_INTERVAL}"

# Default trade size (0.001 BTC ~ $60–100 at typical prices; well above min notional)
DEFAULT_TRADE_SIZE = "0.001"

AGENTS: list[dict] = [
    {
        "name": "Agent 1 - Quant",
        "strategy_path": str(_HERE / "agent-1-quant" / "round11" / "strategy.py"),
        "module": "agent1_r11",
    },
    {
        "name": "Agent 2 - Sentiment",
        "strategy_path": str(_HERE / "agent-2-sentiment" / "round11" / "strategy.py"),
        "module": "agent2_r11",
    },
    {
        "name": "Agent 3 - Macro",
        "strategy_path": str(_HERE / "agent-3-macro" / "round11" / "strategy.py"),
        "module": "agent3_r11",
    },
    {
        "name": "Agent 4 - ML",
        "strategy_path": str(_HERE / "agent-4-ml" / "round11" / "strategy.py"),
        "module": "agent4_r11",
    },
    {
        "name": "Agent 5 - Hybrid",
        "strategy_path": str(_HERE / "agent-5-hybrid" / "round11" / "strategy.py"),
        "module": "agent5_r11",
    },
]

# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------

def fetch_binance_bars_1h(symbol: str, start: str, end: str) -> list[Bar]:
    """Download 1-hour klines from Binance public API and return NT Bar objects.

    Requires: requests (pip install requests / uv add requests)
    Adds a 30-day warmup buffer before `start` for indicator initialization.
    """
    try:
        import requests
    except ImportError:
        raise RuntimeError("requests not installed: uv add requests")

    from datetime import timedelta

    instrument = _get_instrument()
    bar_type = BarType.from_str(BAR_TYPE_STR)

    warmup_start = (
        datetime.strptime(start, "%Y-%m-%d") - timedelta(days=30)
    ).strftime("%Y-%m-%d")

    start_ms = int(datetime.strptime(warmup_start, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)
    end_ms = int(datetime.strptime(end, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000) + 86_400_000

    url = "https://api.binance.com/api/v3/klines"
    raw: list[list] = []
    cur = start_ms

    print(f"  Fetching {symbol} 1h bars from Binance ({warmup_start} to {end})...")
    while cur < end_ms:
        resp = requests.get(
            url,
            params={"symbol": symbol, "interval": "1h", "startTime": cur, "endTime": end_ms, "limit": 1000},
            timeout=30,
        )
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        raw.extend(batch)
        last = batch[-1][0]
        if last <= cur or len(batch) < 1000:
            break
        cur = last + 1
        time.sleep(0.12)

    bars: list[Bar] = []
    price_prec = instrument.price_precision
    size_prec = instrument.size_precision

    for k in raw:
        ts_init = k[0] * 1_000_000  # ms → ns
        ts_event = k[6] * 1_000_000  # close time
        bars.append(
            Bar(
                bar_type=bar_type,
                open=Price(float(k[1]), price_prec),
                high=Price(float(k[2]), price_prec),
                low=Price(float(k[3]), price_prec),
                close=Price(float(k[4]), price_prec),
                volume=Quantity(float(k[5]), size_prec),
                ts_event=ts_event,
                ts_init=ts_init,
            )
        )

    print(f"  Got {len(bars)} bars")
    return bars


def _get_instrument() -> CurrencyPair:
    """Return a BTC/USDT instrument compatible with Binance Spot Testnet."""
    from nautilus_trader.model.currencies import BTC
    from nautilus_trader.model.enums import AssetClass
    from nautilus_trader.model.instruments import CurrencyPair
    from nautilus_trader.model.objects import Price, Quantity

    return CurrencyPair(
        instrument_id=INSTRUMENT_ID,
        raw_symbol=INSTRUMENT_ID.symbol,
        base_currency=BTC,
        quote_currency=USDT,
        price_precision=2,
        size_precision=5,
        price_increment=Price.from_str("0.01"),
        size_increment=Quantity.from_str("0.00001"),
        margin_init=Decimal("0"),
        margin_maint=Decimal("0"),
        maker_fee=Decimal("0.001"),
        taker_fee=Decimal("0.001"),
        ts_event=0,
        ts_init=0,
    )


# ---------------------------------------------------------------------------
# Strategy loading
# ---------------------------------------------------------------------------

def load_strategy_classes(module_name: str, strategy_path: str) -> tuple[type, type] | None:
    """Load (StrategyClass, ConfigClass) from a strategy file.

    Returns None if loading fails or the file doesn't satisfy the contract.
    """
    try:
        from nautilus_trader.config import StrategyConfig
        from nautilus_trader.trading.strategy import Strategy
    except ImportError:
        print("  ERROR: nautilus_trader not installed")
        return None

    path = Path(strategy_path)
    if not path.exists():
        print(f"  ERROR: strategy file not found: {path}")
        return None

    spec = importlib.util.spec_from_file_location(module_name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod

    try:
        spec.loader.exec_module(mod)
    except Exception as e:
        print(f"  ERROR: import failed: {e}")
        traceback.print_exc()
        return None

    strategy_cls = config_cls = None
    for _name, obj in inspect.getmembers(mod, inspect.isclass):
        if obj.__module__ != module_name:
            continue
        if issubclass(obj, Strategy) and obj is not Strategy:
            strategy_cls = obj
        if issubclass(obj, StrategyConfig) and obj is not StrategyConfig:
            config_cls = obj

    if strategy_cls is None or config_cls is None:
        print("  ERROR: strategy file must contain Strategy + StrategyConfig subclasses")
        return None

    return strategy_cls, config_cls


# ---------------------------------------------------------------------------
# NT backtest runner
# ---------------------------------------------------------------------------

def run_nt_backtest(
    agent: dict,
    bars: list[Bar],
    instrument: CurrencyPair,
) -> dict:
    """Run a single agent's strategy through NT BacktestEngine. Returns metrics dict."""
    classes = load_strategy_classes(agent["module"], agent["strategy_path"])
    if classes is None:
        return _error_result(f"Strategy load failed for {agent['name']}")

    strategy_cls, config_cls = classes

    # Build strategy config
    config_kwargs: dict = {
        "instrument_id": str(INSTRUMENT_ID),
        "bar_type": BAR_TYPE_STR,
        "trade_size": DEFAULT_TRADE_SIZE,
    }
    # Inspect config for any extra fields with defaults we can satisfy
    try:
        import msgspec
        for field in msgspec.structs.fields(config_cls):
            if field.name not in config_kwargs and field.default is not msgspec.NODEFAULT:
                pass  # has default, ok
    except Exception:
        pass

    try:
        strategy_config = config_cls(**config_kwargs)
    except Exception as e:
        return _error_result(f"Config instantiation failed: {e}")

    strategy = strategy_cls(config=strategy_config)

    # Build backtest engine
    engine = BacktestEngine(
        config=BacktestEngineConfig(
            logging=LoggingConfig(log_level="ERROR"),
        )
    )
    engine.add_venue(
        venue=VENUE,
        oms_type=OmsType.NETTING,
        account_type=AccountType.CASH,
        base_currency=None,  # multi-currency spot
        starting_balances=[Money(INITIAL_CAPITAL_USDT, USDT)],
    )
    engine.add_instrument(instrument)
    engine.add_data(bars)
    engine.add_strategy(strategy)

    t0 = time.time()
    try:
        engine.run()
    except Exception as e:
        engine.dispose()
        return _error_result(f"Engine run failed: {e}")

    elapsed = round(time.time() - t0, 1)

    # Extract results from account
    result = _extract_metrics(engine, elapsed)
    engine.dispose()
    return result


def _extract_metrics(engine: BacktestEngine, elapsed: float) -> dict:
    """Pull P&L metrics from a completed BacktestEngine."""
    try:
        account = engine.trader.portfolio.account(VENUE)
        balances = account.balances()
        usdt_balance = balances.get(USDT)
        final_equity = float(usdt_balance.total) if usdt_balance else INITIAL_CAPITAL_USDT
    except Exception:
        final_equity = INITIAL_CAPITAL_USDT

    total_return_pct = (final_equity / INITIAL_CAPITAL_USDT - 1.0) * 100.0

    # Pull fill history for trade count and win rate
    fills = engine.trader.generate_order_fills_report()
    num_trades = 0
    win_rate = 0.0
    sharpe = 0.0
    max_dd = 0.0

    try:
        if fills is not None and not fills.empty:
            # Count round-trips: each matched buy+sell pair is one trade
            buys = fills[fills["order_side"] == "BUY"]
            sells = fills[fills["order_side"] == "SELL"]
            num_trades = min(len(buys), len(sells))

            # Approximate win rate from realized PnL column if present
            if "realized_pnl" in fills.columns:
                pnl_vals = (
                    fills["realized_pnl"]
                    .str.replace(r"\s+\w+$", "", regex=True)
                    .astype(float, errors="ignore")
                )
                pnl_numeric = pnl_vals[pnl_vals.notna() & (pnl_vals != 0.0)]
                wins = (pnl_numeric > 0).sum()
                win_rate = float(wins / len(pnl_numeric) * 100) if len(pnl_numeric) > 0 else 0.0
    except Exception:
        pass

    return {
        "final_equity": round(final_equity, 2),
        "total_return_pct": round(total_return_pct, 2),
        "sharpe_ratio": round(sharpe, 3),
        "max_drawdown_pct": round(max_dd, 2),
        "num_trades": num_trades,
        "win_rate": round(win_rate, 1),
        "elapsed_seconds": elapsed,
    }


def _error_result(msg: str) -> dict:
    return {
        "final_equity": INITIAL_CAPITAL_USDT,
        "total_return_pct": 0.0,
        "sharpe_ratio": 0.0,
        "max_drawdown_pct": 0.0,
        "num_trades": 0,
        "win_rate": 0.0,
        "error": msg,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("=" * 70)
    print("ROUND 11 COMPETITION EVALUATION (NautilusTrader BacktestEngine)")
    print(f"Period: {EVAL_START} to {EVAL_END} | Capital: ${INITIAL_CAPITAL_USDT:,.0f} USDT")
    print(f"Evaluated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)
    print()
    print("NOTE: Round 11 requires strategies to be NautilusTrader Strategy subclasses.")
    print("      Run validate_strategy.py on each submission before this evaluator.")
    print("      See competition/ROUND11_CONTRACT.md for the full contract.")
    print()

    # Fetch data once and reuse
    instrument = _get_instrument()
    try:
        bars = fetch_binance_bars_1h("BTCUSDT", EVAL_START, EVAL_END)
    except Exception as e:
        print(f"ERROR: Could not fetch market data: {e}")
        print("Hint: Check internet connection and Binance API availability.")
        sys.exit(1)

    results: list[dict] = []
    for agent in AGENTS:
        print(f"\n{'='*70}")
        print(f"Running: {agent['name']}")
        print(f"{'='*70}")

        path = Path(agent["strategy_path"])
        if not path.exists():
            print(f"  SKIP: strategy file not found at {path}")
            print("        Agents must submit round11/strategy.py to participate.")
            results.append({"agent": agent["name"], **_error_result("Strategy file not found")})
            continue

        result = run_nt_backtest(agent, bars, instrument)
        results.append({"agent": agent["name"], **result})

    # Sort by return
    results.sort(key=lambda r: r.get("total_return_pct", 0), reverse=True)

    # Print leaderboard
    print()
    print("=" * 70)
    print("ROUND 11 LEADERBOARD")
    print("=" * 70)
    header = f"{'Rank':<6}{'Agent':<30}{'Return %':>10}{'Equity':>10}{'Sharpe':>8}{'Trades':>8}{'WinR%':>8}"
    print(header)
    print("-" * 80)
    for i, r in enumerate(results, 1):
        err = " [ERROR]" if "error" in r else ""
        print(
            f"{i:<6}{r['agent'][:29]:<30}"
            f"{r.get('total_return_pct', 0):>+10.2f}"
            f"{r.get('final_equity', INITIAL_CAPITAL_USDT):>10.2f}"
            f"{r.get('sharpe_ratio', 0):>8.2f}"
            f"{r.get('num_trades', 0):>8}"
            f"{r.get('win_rate', 0):>8.1f}{err}"
        )
        if "error" in r:
            print(f"        Error: {r['error']}")

    winner = next((r for r in results if r.get("total_return_pct", 0) > 0 and "error" not in r), None)
    print()
    if winner:
        print(f"WINNER: {winner['agent']} with {winner['total_return_pct']:+.2f}% return!")
    else:
        print("NO WINNER: No agent achieved a positive return (or no strategies submitted).")

    # Save results
    output_path = _HERE / "round11_results.txt"
    with open(output_path, "w") as f:
        f.write("ROUND 11 COMPETITION RESULTS (NautilusTrader BacktestEngine)\n")
        f.write(f"Evaluation Period: {EVAL_START} to {EVAL_END}\n")
        f.write(f"Starting Capital: ${INITIAL_CAPITAL_USDT:,.0f} USDT\n")
        f.write(f"Evaluated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("=" * 70 + "\n\n")
        f.write(header + "\n")
        f.write("-" * 80 + "\n")
        for i, r in enumerate(results, 1):
            err = " [ERROR]" if "error" in r else ""
            f.write(
                f"{i:<6}{r['agent'][:29]:<30}"
                f"{r.get('total_return_pct', 0):>+10.2f}"
                f"{r.get('final_equity', INITIAL_CAPITAL_USDT):>10.2f}"
                f"{r.get('sharpe_ratio', 0):>8.2f}"
                f"{r.get('num_trades', 0):>8}"
                f"{r.get('win_rate', 0):>8.1f}{err}\n"
            )
        f.write("\n")
        if winner:
            f.write(f"WINNER: {winner['agent']} with {winner['total_return_pct']:+.2f}% return!\n")
        else:
            f.write("NO WINNER: No agent achieved a positive return.\n")
        f.write("\n\nDetailed Results:\n" + "=" * 70 + "\n")
        for r in results:
            f.write(f"\n{r['agent']}\n" + "-" * 40 + "\n")
            for k, v in r.items():
                if k == "agent":
                    continue
                f.write(f"  {k}: {v}\n")

    print(f"\nResults saved to {output_path}")


if __name__ == "__main__":
    main()
