"""BacktestNode configuration and execution."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from nautilus_trader.backtest.node import BacktestRunConfig

from nautilus_trader.model import QuoteTick
from nautilus_trader.persistence.catalog import ParquetDataCatalog

from nautilus_trading.backtest.runner_base import BacktestRunner


def build_backtest_config(
    catalog: ParquetDataCatalog,
    *,
    strategy_path: str = "strategies.forex.ema_cross:EMACrossStrategy",
    config_path: str = "strategies.forex.ema_cross:EMACrossConfig",
    instrument_index: int = 0,
    bar_interval: str = "1-MINUTE-MID-INTERNAL",
    trade_size: str = "100000",
    fast_ema_period: int = 10,
    slow_ema_period: int = 20,
    venue_name: str = "SIM",
    oms_type: str = "NETTING",
    account_type: str = "MARGIN",
    base_currency: str = "USD",
    starting_balance: str = "1_000_000 USD",
    end_time: str | None = "2020-01-10",
    log_level: str = "INFO",
    strategy_config_overrides: dict[str, Any] | None = None,
) -> BacktestRunConfig:
    """Build a complete BacktestRunConfig from high-level parameters.

    The strategy config receives instrument_id, bar_type, and trade_size
    by default. For EMA strategies, fast_ema_period and slow_ema_period
    are also included. Pass ``strategy_config_overrides`` to supply or
    override arbitrary keys for any strategy type.
    """
    # Lazy imports so `import nautilus_trading.backtest.runner` stays cheap at collection time.
    from nautilus_trader.backtest.node import (
        BacktestDataConfig,
        BacktestEngineConfig,
        BacktestRunConfig,
        BacktestVenueConfig,
    )
    from nautilus_trader.config import ImportableStrategyConfig, LoggingConfig

    instruments = catalog.instruments()
    if not instruments:
        raise RuntimeError(f"No instruments found in catalog at {catalog.path}")
    if instrument_index >= len(instruments):
        raise RuntimeError(
            f"instrument_index {instrument_index} out of range "
            f"(catalog has {len(instruments)} instruments)"
        )

    instrument = instruments[instrument_index]
    instrument_id = str(instrument.id)
    bar_type = f"{instrument.id}-{bar_interval}"

    # Base config — works for all strategies
    strat_config: dict[str, Any] = {
        "instrument_id": instrument_id,
        "bar_type": bar_type,
        "trade_size": trade_size,
    }

    # Dispatch to a registered builder when one exists; else keep the base dict.
    # Primary lookup is by full strategy_path against STRATEGY_SPECS — this is
    # the only correct match for nested strategies like
    # ``strategies.crypto.kronos.strategy:KronosStrategy``, where the previous
    # ``rsplit('.', 1)[-1].split(':')[0]`` derivation produced 'strategy' (the
    # module basename) and silently missed the registered key 'kronos'.
    # Fallback to module-name lookup keeps callers passing bare module paths
    # (e.g. 'strategies.crypto.grid_bot') working unchanged.
    from nautilus_trading.cli._strategy_specs import STRATEGY_BUILDERS, STRATEGY_SPECS

    module_name = strategy_path.rsplit(".", 1)[-1].split(":")[0]
    spec = next(
        (s for s in STRATEGY_SPECS.values() if s.strategy_path == strategy_path),
        None,
    )
    builder = spec.builder if spec is not None else STRATEGY_BUILDERS.get(module_name)
    if builder is not None:
        # Build the input dict for the builder. Pre-merge strategy_config_overrides
        # so callers can supply builder-required fields (e.g. grid_bot needs
        # upper_price/lower_price) that the backtest CLI doesn't surface. If a
        # builder's required fields are still missing after the merge, it raises
        # ValueError with a clear message — that's the intended "fail loudly"
        # behavior; callers must pass the missing fields via strategy_config_overrides.
        builder_input: dict[str, Any] = {
            "instrument_id": strat_config["instrument_id"],
            "bar_type": strat_config["bar_type"],
            "trade_size": strat_config["trade_size"],
            "fast_ema": fast_ema_period,
            "slow_ema": slow_ema_period,
            "upper_price": None,
            "lower_price": None,
            "grid_levels": None,
            "buy_amount": None,
            "buy_interval_bars": None,
            "sma_fast": None,
            "sma_slow": None,
            "stop_fast": None,
            "stop_slow": None,
            "module_name": module_name,
        }
        if strategy_config_overrides:
            builder_input.update(strategy_config_overrides)
        strat_config = builder.build(builder_input)

    if strategy_config_overrides:
        strat_config.update(strategy_config_overrides)

    data_config = BacktestDataConfig(
        catalog_path=str(catalog.path),
        data_cls=QuoteTick,
        instrument_id=instrument.id,
        end_time=end_time,
    )

    return BacktestRunConfig(
        engine=BacktestEngineConfig(
            strategies=[
                ImportableStrategyConfig(
                    strategy_path=strategy_path,
                    config_path=config_path,
                    config=strat_config,
                ),
            ],
            logging=LoggingConfig(log_level=log_level),
        ),
        venues=[
            BacktestVenueConfig(
                name=venue_name,
                oms_type=oms_type,
                account_type=account_type,
                base_currency=base_currency,
                starting_balances=[starting_balance],
            ),
        ],
        data=[data_config],
    )


def run_backtest(
    config: BacktestRunConfig,
) -> list:
    """Execute a backtest and return the results list."""
    from nautilus_trader.backtest.node import BacktestNode

    node = BacktestNode(configs=[config])
    return node.run()


def print_results(results: list) -> None:
    """Pretty-print backtest results to stdout."""
    print("\n" + "=" * 60)
    print("BACKTEST RESULTS")
    print("=" * 60)
    for result in results:
        print(result)


class EMABacktestRunner(BacktestRunner):
    """Wrap the function-based EMA/BacktestNode flow behind the BacktestRunner ABC.

    Unlike :class:`BacktestStrategyRunner` (which drives a
    ``BacktestEngine`` directly), this runner's ``run_backtest()``
    constructs a ``BacktestNode`` internally — the engine lives inside
    the node, not in this class. The ``engine`` parameter on
    ``add_data()`` / ``run()`` is therefore unused; ``main()`` is
    overridden to skip the default engine-creation step entirely.
    """

    def __init__(self, catalog: ParquetDataCatalog, **kwargs: Any) -> None:
        self._catalog = catalog
        self._kwargs = kwargs
        self._run_config: Any = None

    def build_config(self) -> Any:
        self._run_config = build_backtest_config(self._catalog, **self._kwargs)
        return self._run_config

    def add_data(self, engine: Any) -> None:
        """No-op — BacktestNode wires data from BacktestDataConfig internally."""
        return

    def run(self, engine: Any) -> Any:
        """Run via BacktestNode; ``engine`` is unused (node owns its own engine)."""
        return run_backtest(self._run_config)

    def print_results(self, results: Any) -> None:
        print_results(results)

    def main(self) -> None:
        """Override base main(): skip engine creation — BacktestNode owns it."""
        self.build_config()  # caches self._run_config
        results = self.run(None)
        self.print_results(results)
