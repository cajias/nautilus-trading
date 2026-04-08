# Agent 5 - Hybrid Strategist: Round 1 Research Notes

## Core Philosophy
No single signal works all the time. Blend trend-following, mean reversion,
volatility, and momentum with dynamic weighting and regime switching.

## Key Insights from NotebookLM Research

### Regime Detection
- HMMs on 4H data with log returns, ATR, ADX are effective
- Simpler approach: EMA spread + volatility percentile for regime classification
- 20/100 EMA crossover defines macro regime; volatility percentile refines it

### Signal Combination
- Momentum (MACD/RSI) + Volatility (BB/ATR) provides synergy
- Volatility suppresses whipsaw losses from pure momentum
- Pairwise correlation filtering: remove signals with r > 0.7

### Dynamic Weighting
- Rolling-window performance tracking per signal
- Weight signals by their recent Sharpe ratio or hit rate
- Recalculate weights every N bars

### Robustness
- Walk-forward optimization over rolling windows
- Target volatility scaling (e.g., 15% annualized)
- Avoid overfitting: use simple indicators, few parameters
- Bonferroni correction for feature selection

## Strategy Design

### Assets
- BTC/USDT, ETH/USDT, SOL/USDT (diversification across market cap tiers)

### Signals (4 factors)
1. **Trend**: Dual EMA crossover (fast/slow) with ADX filter
2. **Momentum**: RSI divergence from neutral (50)
3. **Volatility Breakout**: Keltner Channel breakout
4. **Mean Reversion**: Bollinger Band z-score reversion

### Regime Detection
- Volatility regime: ATR percentile (low/medium/high)
- Trend regime: ADX threshold + EMA alignment

### Position Sizing
- Target 1% portfolio risk per trade
- ATR-based stop distances
- Max 30% allocation per asset

### Risk Management
- 2x ATR trailing stops
- Max drawdown circuit breaker at 15%
- Reduce size in high volatility regimes
