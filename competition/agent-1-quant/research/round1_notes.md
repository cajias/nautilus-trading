# Round 1 Research Notes - Quantitative Crypto Trading

## Key Findings from NotebookLM Research

### Strategy Performance (Crypto Futures Benchmarks)
| Strategy | Sharpe (Mainstream) | Sharpe (Altcoins) |
|----------|-------------------|-------------------|
| Grid Trading (Mean Rev) | 0.25 | 0.15 |
| Pairs Trading (Stat Arb) | 0.22 | 0.49 |
| TSMOM (Momentum) | -0.77 | -0.78 |
| ML Ensemble | 0.80-0.91 | - |
| TiMi Multi-Agent | 0.79 | 0.86 |

### Key Takeaways
1. Pure momentum (TSMOM) has NEGATIVE Sharpe in crypto -- whipsaw kills returns
2. Mean reversion works in range-bound markets but fails during trends
3. Adaptive/regime-aware approaches dominate (Sharpe 0.7-0.9)
4. Rolling 30-day window optimization maintained 0.72 Sharpe vs 0.36 static
5. Transaction costs must be explicit -- 0.5% round-trip is realistic

### Strategy Decision
Build a **Regime-Adaptive Dual Strategy**:
- Detect regime via volatility + MA crossover
- Trending regime: Use momentum (breakout) signals
- Range-bound regime: Use mean reversion (Bollinger Band) signals
- Volatility-scaled position sizing
- Explicit transaction cost modeling (0.1% per trade for Binance spot)
- Rolling parameter optimization

### Pair Selection
- BTC/USDT: Most liquid, lowest spreads, best data quality
- Use 1-hour bars for signal generation (balance between noise and responsiveness)
