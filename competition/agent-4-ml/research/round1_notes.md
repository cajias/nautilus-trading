# Round 1 Research Notes - ML Engineer

## Approach: LightGBM with Walk-Forward Validation

### Key Principles
1. **Walk-forward validation** - Train on expanding window, predict next period only
2. **Feature engineering from OHLCV** - Technical indicators, lagged returns, volatility regimes
3. **Avoid overfitting** - Conservative model params, regularization, few features relative to samples
4. **Regime awareness** - Crypto markets are non-stationary; use rolling statistics

### Feature Set
- Lagged returns (1, 2, 3, 5, 10 bars)
- RSI (14, 28 periods)
- MACD signal line crossover
- Bollinger Band position (where price sits relative to bands)
- ATR-normalized volatility
- Volume ratio (current vs rolling average)
- Rolling Sharpe ratio
- Price momentum (rate of change)
- Mean reversion signal (z-score of price vs moving average)

### Model Choice: LightGBM
- Handles non-linear relationships well
- Fast training (important for walk-forward)
- Built-in regularization (num_leaves, min_child_samples, L1/L2)
- Handles missing values natively

### Risk Management
- Position sizing based on model confidence
- Stop-loss at 2x ATR
- Maximum 1 position at a time
- No leverage

### Asset Selection
- BTC/USDT - most liquid, most data, least susceptible to manipulation
- 4-hour bars - balance between noise reduction and signal capture
