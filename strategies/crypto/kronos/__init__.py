"""Kronos foundation model integration for NautilusTrader.

Components
----------
- ``data.KronosSignal``: custom data object published by KronosActor via the
  MessageBus.
- ``actor.KronosActor`` / ``actor.KronosActorConfig``: actor that maintains a
  rolling OHLCV window, runs Kronos inference, and publishes ``KronosSignal``
  objects every N bars.
- ``strategy.KronosStrategy`` / ``strategy.KronosStrategyConfig``: strategy
  that subscribes to ``KronosSignal`` and makes trading decisions with
  stop-loss, take-profit, and a peak-drawdown circuit breaker.
- Paper-trade driver: ``nt paper-trade --config configs/paper/kronos.yaml``.
- Backtest driver: ``nt backtest --config configs/backtest/kronos.yaml``.
  The per-strategy ``paper_runner`` shim was removed in sub-project B.5
  PR 1; the legacy ``KronosBacktestRunner`` (``backtest.py`` /
  ``backtest_config.py`` / ``_fetch_binance.py``) was retired in
  sub-project B.5 PR 3 once the parity-snapshot test confirmed
  equivalence with :class:`BacktestStrategyRunner` +
  :data:`STRATEGY_SPECS["kronos"]` + :class:`BinanceRestDataSource`.

Imports are deliberately **not** re-exported at the package root. Eager
package-root imports of ``actor``/``strategy`` would pull heavy modules
(``pandas``/``torch``) on every ``nt`` invocation, even for strategies
that don't need them. Import from submodules directly when you need
the symbols.

Model selection
---------------
Set ``model_size="mini"`` (default), ``"small"``, or ``"base"`` in
``KronosActorConfig`` (or override via the YAML ``params:`` block).
HuggingFace model/tokenizer IDs are configurable for advanced use::

    KronosActorConfig(
        huggingface_model_id="NeoQuasar/Kronos-mini",
        huggingface_tokenizer_id="NeoQuasar/Kronos-Tokenizer-2k",
    )
"""
