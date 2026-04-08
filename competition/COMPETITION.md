# Crypto Strategy Competition — 10 Rounds

## Rules
- 5 agents, each isolated, no visibility into others' strategies
- Each round: agents get TRAIN + TEST periods, must be profitable on TEST
- Evaluation on HIDDEN period (agents never see this)
- $1,000 starting capital per round
- Agent with most round wins takes the competition

## Agents
| # | Persona | Approach |
|---|---------|----------|
| 1 | Quantitative Trader | Statistical arb, mean reversion, momentum |
| 2 | Sentiment Trader | Fear/greed, behavioral, volume-price patterns |
| 3 | Macro Strategist | Regime detection, trend following, rotation |
| 4 | ML Engineer | Feature engineering, walk-forward ML, ensembles |
| 5 | Hybrid Strategist | Multi-signal ensemble, adaptive weighting |

## Round Schedule

| Round | Train Period | Test Period | Eval Period (HIDDEN) |
|-------|-------------|-------------|---------------------|
| 1 | Jan-Jun 2024 | Jul-Sep 2024 | Oct-Dec 2024 |
| 2 | Apr-Sep 2024 | Oct-Dec 2024 | Jan-Mar 2025 |
| 3 | Jul-Dec 2024 | Jan-Mar 2025 | Apr-Jun 2025 |
| 4 | Oct 2024-Mar 2025 | Apr-Jun 2025 | Jul-Sep 2025 |
| 5 | Jan-Jun 2025 | Jul-Sep 2025 | Oct-Dec 2025 |
| 6 | Apr-Sep 2025 | Oct-Dec 2025 | Jan-Mar 2026 |
| 7 | Jan-Jun 2024 | Jul-Dec 2024 | Jan-Mar 2025 |
| 8 | Jul 2024-Jun 2025 | Jul-Sep 2025 | Oct-Dec 2025 |
| 9 | Jan-Dec 2025 | Jan-Feb 2026 | Mar-Apr 2026 |
| 10 | 2024 full year | Jan-Jun 2025 | Jul-Dec 2025 |

## Scoring
- Round winner = highest return on hidden eval period (must be positive)
- If no agent is positive, no winner for that round
- Final winner = most round wins. Tiebreaker: cumulative eval return.
