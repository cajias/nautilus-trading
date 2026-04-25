# Round 10 — Agent 5 (Hybrid SMA Ensemble) · +93.08%

R10 hidden period 2025-07-01 to 2025-12-31. **Winner by the largest margin of the series.** 50/50 ensemble of two long-only SMA trend-following sub-strategies on BNB:
- sub_fast: SMA(20), trailing stop 7%
- sub_slow: SMA(30), trailing stop 8%

Entry: `close > SMA(n) AND close > prev_close AND sub flat`. Exit: `close < SMA(n) OR close < peak_since_entry * (1 - stop_pct)`. Each sub votes for 50% of equity; strategy rebalances to the combined target on each closed daily bar.

Final equity: $1,930.80 · Sharpe: 1.64 · Max DD: 23.69% · Trades: 17 · Win rate: 58.8%

**Promoted to production:** Ported to NautilusTrader as [`strategies/crypto/hybrid_sma_r10.py`](../../strategies/crypto/hybrid_sma_r10.py), wired into the CLI via `HybridSMAConfigBuilder` in `nautilus/src/nautilus_trading/cli/_strategy_specs.py` (was `_strategy_configs.py` before sub-project B.5 unified the registry), and covered by 48 tests in `tests/test_hybrid_sma_r10.py`. This is why R10's `competition/agent-5-hybrid/round10/` source could be safely deleted without losing the algorithm.

Source archive (removed in THIS commit): `competition/agent-5-hybrid/round10/` · results: `competition/archive/results/round10_results.txt`
