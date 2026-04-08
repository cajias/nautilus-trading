# Round 1 Research Notes - Macro Strategist

## Key Findings from NotebookLM Research

### Regime Detection
- 20-day / 100-day SMA crossover: bullish when 20d > 100d, bearish otherwise
- HMM on 4H data with log returns, ATR, ADX -> regime score 0-100 (0-30 bear, 30-70 chop, 70-100 bull)
- BTC dominance shifts and funding rates as leading macro signals

### Multi-Timeframe Trend Following
- Major TF for confirmation, minor TF for entry
- 20/50/100/200 SMA for macro trend, 9/21 EMA for entries
- Daily for scanning, 5min/15min for execution

### Cross-Asset Correlation
- BTC-ETH-BNB crash simultaneously (lower-tail dependence)
- Intra-crypto diversification fails during downturns
- Safe haven rotation to stablecoins during risk-off

### Position Sizing by Regime
- Volatility-targeted scaling: target 15% annualized vol, scale = min(2, 0.15/realized_vol)
- ATR risk scaling: larger positions in risk-on, smaller in risk-off
- 126-day quintile filters to prevent overtrading in chop
- Fractional Kelly for max leverage cap

## Strategy Design Decision
- Trade BTC/USDT on 4H timeframe
- Regime detection: dual SMA (20/100) + ADX for trend strength
- Entry: 9/21 EMA cross confirmed by regime
- Position sizing: volatility-targeted (15% target)
- Risk: ATR-based stop losses, wider in high-vol regimes
