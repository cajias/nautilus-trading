# StockAgent

## System

- **Paper**: When AI Meets Finance (StockAgent): Large Language
  Model-based Stock Trading in Simulated Real-world Environments
- **Authors**: Chong Zhang, Xinyi Liu, Zhongmou Zhang, Mingyu Jin,
  Lingyao Li, Zhenting Wang, Wenyue Hua, Dong Shu, Suiyuan Zhu,
  Xiaobo Jin, Sujian Li, Mengnan Du, Yongfeng Zhang
- **Year**: 2024 (arXiv v1 Jul 15 2024; v4 Sep 21 2024)
- **Link**: https://arxiv.org/abs/2407.18957
- **Code**: Not surfaced in the fetched abstract page
  [source: related-stockagent]

## What it does

Multi-agent LLM system that simulates real-world stock trading
environments so researchers can study how external factors
(macroeconomics, policy changes, company fundamentals, global events)
influence investor behavior [source: related-stockagent]. It's an
agent-based market simulator, not a live trader -- populated by many
LLM-driven "investor" agents that react to market events.

## Architecture

```
  +----------+  +----------+  +----------+  +----------+
  | Agent 1  |  | Agent 2  |  | Agent 3  |  | Agent N  |
  | (LLM +   |  | (LLM +   |  | (LLM +   |  | (LLM +   |
  |  persona)|  |  persona)|  |  persona)|  |  persona)|
  +----+-----+  +----+-----+  +----+-----+  +----+-----+
       \\            |             |             //
        \\           v             v            //
         +--------------------+-------------------+
         | Simulated exchange / order book / news |
         +--------------------+-------------------+
                              ^
                              | external factors
                              | (macro, policy, events)
```

Key contribution: avoids test-set leakage by forbidding models from
using prior knowledge about the test data [source: related-stockagent].

## Tech stack

- **Language**: Python (inferred) [source: related-stockagent]
- **LLMs**: Benchmark of multiple LLMs under the StockAgent harness
  [source: related-stockagent]
- **Simulation layer**: Custom agent-based market simulator
  [source: related-stockagent]
- **Data scope**: Stock market [source: related-stockagent]

## Simulation / backtest story

StockAgent *is* a simulation framework. It generates synthetic market
episodes where LLM agents interact with each other and react to
external shocks [source: related-stockagent]. It does not paper-trade
or backtest against historical bars in the conventional sense --
instead it measures how different LLMs behave as trading agents under
controlled conditions.

## Our platform mapping

| StockAgent concept          | Nautilus equivalent                        |
|-----------------------------|--------------------------------------------|
| Many LLM-driven agents      | Our 5 competition agents (agent-1..5)      |
| Simulated exchange          | `BacktestEngine` with synthetic bars       |
| External shock injection    | Custom `DataProvider` that mutates bars    |
| Investor personas           | `StrategyConfig` fields + prompt           |
| Market-impact feedback loop | NO direct equivalent -- orders in the     |
|                             | sim move price; in Nautilus they don't     |
|                             | unless we wire up a reactive `DataClient`  |

StockAgent is the one cited system that is **not** a tradeable strategy
template. It's a research harness.

## Integration plan

1. Do NOT port StockAgent as a trading strategy. It's the wrong shape.
2. Consider mining StockAgent's "external-factor injection" mechanism
   as a stress-test layer for our own `BacktestEngine` runs --
   inject simulated shocks (sudden drawdowns, news bursts) and see
   how our agents react. This would land under
   `competition/stress_tests/` rather than `strategies/`.
3. Read the paper's persona definitions as a source of ideas for
   agent-1..5 prompt styling.

## Competition fit

**No -- architectural mismatch**. StockAgent simulates trading; our
competition grades real PnL on real (paper-traded) venues. Porting it
as a strategy would be category error.

## Effort estimate

**M** only if we repurpose the external-shock injection layer as a
stress-test harness. **N/A** as a direct strategy port.

## Open questions

- Is the StockAgent simulator open-source? Abstract doesn't link it
  [source: related-stockagent].
- Would a stress-test layer benefit the competition eval, or is it
  out-of-scope for the hidden-period evaluation? User input needed.
- Should we pull persona definitions from StockAgent into our
  agent-1..5 briefs? Low risk but needs user confirmation.
