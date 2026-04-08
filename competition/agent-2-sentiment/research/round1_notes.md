# Agent 2 - Sentiment Trader: Round 1 Research Notes (Final)

## Strategy: Fear & Greed Regime Momentum (FGRM)

### Core Thesis
Crypto markets are driven by crowd psychology cycles of fear and greed. By detecting
extreme sentiment regimes from price-derived indicators, we can trade mean-reversion
at extremes and momentum during regime transitions.

### Indicators Used (all derived from price/volume)

1. **RSI (14)** - Fear/Greed proxy
   - RSI < 30: Extreme fear (potential buy zone)
   - RSI > 70: Extreme greed (potential sell zone)
   - RSI crossing back from extremes = regime shift signal

2. **Volume-Price Divergence**
   - Price making new lows but volume declining = exhaustion (bullish divergence)
   - Price making new highs but volume declining = distribution (bearish divergence)

3. **Volatility Regime (ATR-based)**
   - ATR expanding: Trending regime -> use momentum
   - ATR contracting: Range-bound regime -> use mean reversion
   - Bollinger Band width for squeeze detection

4. **VWAP Distance**
   - Price far above VWAP = overextended greed
   - Price far below VWAP = overextended fear

5. **Rate of Change (ROC)**
   - Rapid price changes indicate crowd euphoria/panic
   - Useful for timing entries during sentiment extremes

### Strategy Logic
- Combine RSI extremes + volume confirmation + volatility regime
- Enter long when fear is extreme and volume shows exhaustion
- Enter short when greed is extreme and volume shows distribution
- Use ATR-based stops and targets
- Position sizing: risk 1-2% per trade

### Why This Should Work
- Crypto retail-dominated -> sentiment extremes more pronounced
- Fear/greed cycles are shorter and more violent in crypto
- Volume data on Binance is real and actionable
- Works across multiple timeframes

### Pair Selection
- BTC/USDT or ETH/USDT for liquidity
- 4h timeframe for signal quality vs frequency balance

## Iteration History

### v1-v3: Pure mean-reversion (FAILED)
- Tried buying fear dips, selling greed spikes
- Pure sentiment mean-reversion lost money (-10% to -24%)
- Problem: catching falling knives, too many false reversals

### v4: Trend following with sentiment filter (break-even)
- EMA crossover + RSI filter
- -4.6% on daily, too few trades
- Shorts killing profitability

### v5: Regime-adaptive (WINNER)
- Key insight: use EMA alignment (8/21/55) for regime detection
- Only trade in confirmed regimes, flat during transitions
- Multi-asset (ETH+BTC+BNB) for diversification
- Anti-tilt sizing after consecutive losses
- Result: +5.07% return, 0.98 Sharpe, -5.86% max DD

### Key Learnings
1. Pure sentiment strategies fail in crypto -- need trend following core
2. Regime detection is more important than signal quality
3. Multi-asset diversification dramatically reduces drawdowns
4. BNB was the standout performer (strong uptrend through Sep)
5. Anti-tilt sizing prevents compounding losses during choppy periods
6. Trailing stops are the #1 exit type for winners (47% of exits)
