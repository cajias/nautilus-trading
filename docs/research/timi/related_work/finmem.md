# FinMem

## System

- **Paper**: FinMem: A Performance-Enhanced LLM Trading Agent with Layered
  Memory and Character Design
- **Authors**: Yangyang Yu, Haohang Li, Zhi Chen, Yuechen Jiang, Yang Li,
  Denghui Zhang, Rong Liu, Jordan W. Suchow, Khaldoun Khashanah
- **Year**: 2023 (arXiv v1 Nov 23 2023; v2 Dec 3 2023)
- **Link**: https://arxiv.org/abs/2311.13743
- **Code**: https://github.com/pipiku915/FinMem-LLM-StockTrading (MIT license)

## What it does

Single-agent LLM framework that wraps three modules -- Profiling
(persona/risk tolerance), Memory (layered sensory/short/long), and
Decision-making -- into a self-evolving trader that ingests hierarchical
financial inputs and emits daily buy/sell decisions
[source: related-finmem]. Its memory module explicitly mirrors the
cognitive structure of human traders to provide interpretability and
real-time tuning [source: related-finmem].

## Architecture

Three modules wrapped around a single LLM call per decision
[source: related-finmem]:

```
+-------------+   +---------------------------+   +-----------------+
| Profiling   |-->| Memory (layered)          |-->| Decision making |
| (persona,   |   |  sensory / short / long / |   | (prompt LLM,    |
|  risk)      |   |  reflection)              |   |  emit buy/sell) |
+-------------+   +---------------------------+   +-----------------+
                           ^
                           | news, filings, price bars
```

Adjustable "cognitive span" lets the agent retain critical information
beyond the LLM context window by selectively hoisting memory items
between tiers [source: related-finmem].

## Tech stack

- **Language**: Python 3.10 [source: related-finmem-github]
- **License**: MIT [source: related-finmem-github]
- **LLMs**: Supports multiple via `puppy/` adapter module
  [source: related-finmem-github]
- **Data**: Pre-prepared stock + news dataset in `data/`; fine-tuning
  pipeline in `data-pipeline/` [source: related-finmem-github]
- **Formatter**: `black` [source: related-finmem-github]

## Simulation / backtest story

Backtests on a "scalable real-world financial dataset" of stocks; FinMem
is compared to algorithmic baselines and a fine-tuned variant
[source: related-finmem]. TiMi reuses FinMem as a "memory-augmented" LLM
agent baseline in its own altcoin Sortino-ratio comparison
[source: arxiv-2510.04787-html]. Evaluation is end-of-day bar replay --
not tick-level and not paper-traded on an exchange.

## Our platform mapping

| FinMem concept       | Nautilus equivalent                               |
|----------------------|----------------------------------------------------|
| Profiling module     | Fields on a `StrategyConfig` (risk_profile, etc.) |
| Layered memory       | Custom dict in `self.cache` keyed by timestamp    |
| Decision output      | `self.order_factory.market(...)` + `submit_order` |
| Daily bar input      | `subscribe_bars(BarType ...1-DAY-LAST-INTERNAL)`  |
| News input           | NOT built-in -- needs new `DataProvider`          |

The Layered Memory tier is a clean abstraction we can lift in isolation
from the rest of the pipeline -- a reusable `LayeredMemory` helper class
that a Nautilus strategy holds as a field.

## Integration plan

1. Read `puppy/memory.py` in the FinMem repo; port the layered-memory
   data structure into `strategies/crypto/_lib/layered_memory.py` (new
   helper module, no external deps).
2. Build a nightly offline cron that calls the LLM once per symbol to
   produce a decision + reflection, writing to
   `strategies/crypto/_cache/finmem_decisions.parquet`.
3. Thin runtime strategy `strategies/crypto/finmem_live.py` loads today's
   decision pack in `on_start`, acts on bar close, and ignores the
   LLM otherwise -- keeps `on_bar` cheap.
4. News ingestion: wrap CryptoPanic or a GDELT slice in a new
   `nautilus_trading.data` provider.
5. Ship a Claude Code agent definition in `.claude/agents/finmem-nightly.md`
   that runs the offline pipeline.

## Competition fit

**Not ready as-is**. Blockers:
- News input has no drop-in crypto equivalent (original uses 10-K
  filings + news articles).
- LLM-in-the-loop per decision violates the `on_bar` latency budget;
  must decouple to offline nightly pass (Integration plan step 2).
- Competition rules require spot-only long-only, which FinMem supports
  but must be enforced in the ported strategy.

## Effort estimate

**L** -- memory module is small (~400 LoC), but the news pipeline,
nightly cron, Claude agent, and parquet cache plumbing add up. ~1 week.

## Open questions

- Which crypto news source? User prefers a single feed; need to confirm
  CryptoPanic vs GDELT vs an LLM-summarized RSS aggregator.
- LLM budget: one call per (symbol x day) across 8 pairs is ~240/mo.
  Is that within the competition's implicit cost ceiling?
- Is the `puppy/` module code pluggable or tightly coupled to the
  Alpaca-style stock universe? Need to read the source.
