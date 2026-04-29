"""``BacktestStrategyRunner`` — generic backtest runner.

Parallels :class:`~nautilus_trading.paper_trade.strategy_runner.PaperTradeStrategyRunner`
from PR 1 but for the backtest mode. A single concrete runner class
parameterized by:

- a :class:`~nautilus_trading.cli._strategy_specs.StrategySpec` from the
  unified registry (knows the strategy + actor wiring);
- a :class:`~nautilus_trading.backtest.run_config.BacktestRunConfig`
  (the YAML run-config — venue, account, balances, data source spec,
  date range, params);
- a :class:`~nautilus_trading.backtest.data_sources.DataSource` adapter
  that materializes the ``(Instrument, bars)`` slice for the run.

Signal-flow ordering
--------------------
Actor configs are built **before** the strategy config. The emitted
:class:`BacktestEngineConfig` carries them in its ``actors`` list,
which the engine starts before anything in ``strategies`` at boot.
Preserves the Kronos contract documented in
``strategies/crypto/kronos/strategy.py`` (actor publishes signals,
strategy consumes them — actor must be up first). Mirror of PR 1's
paper-trade ordering invariant.

Kronos integration
------------------
PR 3 ported kronos onto this runner via a parity-snapshot test. The
durable anchor at
``tests/strategies/crypto/kronos/test_backtest_parity.py`` asserts that
the kronos config emitted by this runner (driven by
``STRATEGY_SPECS["kronos"]`` + :class:`BinanceRestDataSource`) matches
the frozen snapshot — any drift trips the regression guard.

Build-once contract
-------------------
Per Task #10's lesson on the paper-trade CLI: ``build_config()`` is
called exactly once per :meth:`main` invocation. The result is reused
for both engine construction and (transitively) any external
validation step the CLI performs in Task D. Re-building inside
``main()`` would double the msgspec / ImportableActorConfig / builder
work for zero benefit.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
from nautilus_trader.config import (
    ImportableActorConfig,
    ImportableStrategyConfig,
    LoggingConfig,
)
from nautilus_trader.model.enums import AccountType, OmsType
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.objects import Money

from nautilus_trading.backtest.data_sources import DataSource
from nautilus_trading.backtest.run_config import BacktestRunConfig
from nautilus_trading.backtest.runner_base import BacktestRunner
from nautilus_trading.cli._strategy_specs import StrategySpec


@dataclass
class BacktestStrategyRunner(BacktestRunner):
    """Spec-driven backtest runner.

    Parameters
    ----------
    spec : StrategySpec
        Pulled from
        :data:`~nautilus_trading.cli._strategy_specs.STRATEGY_SPECS`.
        Carries the strategy + (optional) actor import paths plus the
        config builders.
    run_config : BacktestRunConfig
        Decoded from a YAML file under ``configs/backtest/``.
    data_source : DataSource
        Per-run data adapter. Built from
        :data:`run_config.data_source` via
        :func:`~nautilus_trading.backtest.data_sources.build_data_source`
        in the canonical CLI flow; tests can pass any adapter directly.
    """

    spec: StrategySpec
    run_config: BacktestRunConfig
    data_source: DataSource

    # ------------------------------------------------------------------
    # BacktestRunner ABC implementation
    # ------------------------------------------------------------------

    def build_config(self) -> BacktestEngineConfig:
        """Build the :class:`BacktestEngineConfig` for this run.

        Build order: actors → strategy. The merged ``params`` dict feeds
        both builders, so per-run state (instrument_id / bar_type /
        overrides) reaches each config without duplication.

        Mirrors the paper-trade runner's pure-compositional shape: this
        method does no engine wiring, just produces an immutable config
        the caller can pass to ``BacktestEngine``.
        """
        merged_params = self._merged_params()

        # Actors first — list comprehension preserves ``spec.actor_specs``
        # order so downstream message-bus wiring matches the spec
        # declaration, and the engine picks up actors before the strategy
        # at boot.
        actor_configs: list[ImportableActorConfig] = [
            ImportableActorConfig(
                actor_path=actor_spec.actor_path,
                config_path=actor_spec.config_path,
                config=actor_spec.builder.build(merged_params),
            )
            for actor_spec in self.spec.actor_specs
        ]

        strategy_config = ImportableStrategyConfig(
            strategy_path=self.spec.strategy_path,
            config_path=self.spec.config_path,
            config=self.spec.builder.build(merged_params),
        )

        return BacktestEngineConfig(
            actors=actor_configs,
            strategies=[strategy_config],
            logging=LoggingConfig(log_level=self.run_config.log_level),
        )

    def add_data(self, engine: BacktestEngine, config: BacktestEngineConfig) -> None:
        """Load the instrument + bars from the data source and attach
        them to ``engine``.

        ``config`` is unused here — the engine config is wired during
        construction; the data adapter is the one that knows where to
        get the bars from. Kept in the signature to match the
        :class:`BacktestRunner` ABC.
        """
        del config  # unused; ABC signature only
        date_range = self.run_config.date_range
        result = self.data_source.load(
            instrument_id=self.run_config.instrument_id,
            bar_type=self.run_config.bar_type,
            start=date_range.start if date_range is not None else None,
            end=date_range.end if date_range is not None else None,
        )
        engine.add_instrument(result.instrument)
        engine.add_data(result.data)

    def run(self, engine: BacktestEngine) -> BacktestEngine:
        """Execute the backtest. Returns the engine so
        :meth:`print_results` can introspect it."""
        engine.run()
        return engine

    def print_results(self, results: Any) -> None:
        """Print a stable one-line summary of the run.

        Detailed analysis (PnL, fills, equity curve) lives in notebooks
        that consume the engine reports separately; this method only
        emits a stable header so CLI users + smoke tests can confirm
        the run completed. Engine disposal is :meth:`main`'s job — see
        the ``finally`` block there.

        ``results`` is loosely typed because PR 2's runner emits the
        engine itself (kronos parity), but a future runner — e.g. a
        ``BacktestNode``-based one — might emit a result list. The
        printed header is the stable contract; downstream tooling
        keys off it without poking at engine internals.
        """
        del results  # not used by the header — kept for ABC parity
        print(f"BacktestStrategyRunner complete: {self.run_config.strategy}")

    def main(self) -> None:
        """Run the backtest end-to-end.

        Build the engine config once, construct the engine, wire the
        venue + data + strategy, run, print, dispose. The ``finally``
        clause ensures the engine is disposed even if the run raises —
        leaks of in-flight engine state would compound across
        consecutive backtest invocations.
        """
        config = self.build_config()
        engine = self._build_engine(config)
        engine.add_venue(
            venue=Venue(self.run_config.venue),
            oms_type=OmsType.NETTING,
            # CASH = multi-currency SPOT — base_currency must be None.
            # Other account types (MARGIN, USDT_FUTURES) need a base
            # currency; PR 2 ships only CASH defaults so this stays
            # simple. PR 4 / live-trading can extend.
            account_type=self._resolve_account_type(),
            base_currency=None,
            starting_balances=[Money.from_str(b) for b in self.run_config.starting_balances],
        )
        self.add_data(engine, config)
        try:
            self.run(engine)
            self.print_results(engine)
        finally:
            engine.dispose()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _merged_params(self) -> dict[str, Any]:
        """Build the args dict the strategy / actor builders consume.

        Top-level fields land LAST so they win against any stray
        duplicate inside ``params:`` — same precedence as the paper-
        trade CLI dispatch (Task #10 NIT). If a user copies one of the
        top-level keys into a per-strategy block by mistake, it gets
        harmlessly overwritten.
        """
        merged: dict[str, Any] = {
            **self.run_config.params,
            "instrument_id": self.run_config.instrument_id,
            "bar_type": self.run_config.bar_type,
        }
        if self.run_config.trade_size is not None:
            merged["trade_size"] = self.run_config.trade_size
        return merged

    def _build_engine(self, config: BacktestEngineConfig) -> BacktestEngine:
        """Engine factory hook — split out so tests can stub it."""
        return BacktestEngine(config=config)

    def _resolve_account_type(self) -> AccountType:
        """Map a ``run_config.account_type`` string to the enum.

        Raises ``ValueError`` (with a list of valid names) on unknown
        values so the future CLI can map to ``BadParameter`` exactly
        like the existing builder error contract.
        """
        try:
            return getattr(AccountType, self.run_config.account_type)
        except AttributeError as exc:
            valid = ", ".join(t.name for t in AccountType)
            raise ValueError(
                f"Unknown account_type {self.run_config.account_type!r}. Valid: {valid}",
            ) from exc
