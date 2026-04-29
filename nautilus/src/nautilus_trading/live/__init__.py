"""Binance PROD live-trading scaffold (NOT IMPLEMENTED).

Real-money execution is out of scope per the 2026-04-21 no-real-money
directive. This package exists so the third runner in the
``{Backtest, PaperTrade, Live}StrategyRunner`` family has a structural
template — and so the ``nt {backtest, paper-trade, live}`` CLI surface is
complete — without shipping any code path that would actually transact
against PROD.

See :class:`~nautilus_trading.live.strategy_runner.LiveStrategyRunner` for
the contract: ``build_config()`` works, ``main()`` raises
``NotImplementedError``.
"""
