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
- Paper-trade driver: run ``nt paper-trade --config configs/paper/kronos.yaml``.
  The per-strategy ``paper_runner`` shim was removed in sub-project B.5;
  ``PaperTradeStrategyRunner`` + ``STRATEGY_SPECS["kronos"]`` now wire the
  actor + strategy declaratively for every paper-trade run.

Imports are deliberately **not** re-exported at the package root. Eager
package-root imports of ``actor``/``strategy`` would pull heavy modules
(``pandas``/``torch``) on every ``nt paper-trade`` invocation, even for
strategies that don't need them. Import from submodules directly when
you need the symbols.

Quick start (backtest)
----------------------
See ``strategies/crypto/kronos/backtest.py`` for a complete runnable example.

Model selection
---------------
Set ``model_size="mini"`` (default), ``"small"``, or ``"base"`` in
``KronosActorConfig``. Or override the HuggingFace model/tokenizer IDs:

    KronosActorConfig(
        huggingface_model_id="NeoQuasar/Kronos-mini",
        huggingface_tokenizer_id="NeoQuasar/Kronos-Tokenizer-2k",
    )

Environment variable alternative::

    export KRONOS_MODEL_SIZE=mini   # read in backtest.py
"""
