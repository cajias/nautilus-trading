# Ding et al. 2024 -- LLM Agent in Financial Trading: A Survey

## System

- **Paper**: Large Language Model Agent in Financial Trading: A Survey
- **Authors**: Han Ding, Yinheng Li, Junhao Wang, Hang Chen, Doudou Guo,
  Yunbai Zhang
- **Year**: 2024 (v1 Jul 2024, v2 Mar 2026)
- **Link**: https://arxiv.org/abs/2408.06361

## What it does

A literature review of LLM-based financial trading agents, providing the
three-way taxonomy (news-driven, reflection-driven, factor-optimization)
that TiMi explicitly adopts for its Related Work section
[source: related-ding-survey]. It summarizes common architectures, the
data inputs agents consume, reported backtest performance, and open
challenges [source: related-ding-survey].

## Architecture

Not an implementation -- this is a survey, so "architecture" here means
the reference architecture the authors extract across papers they review.
At the highest level they describe LLM trading agents as having four
common sub-modules [source: related-ding-survey]:

```
+-----------------+     +-------------+     +--------------+     +---------+
| Perception      | --> | Memory      | --> | Reasoning /  | --> | Action  |
| (market + news) |     | (layered/   |     | reflection   |     | (order) |
+-----------------+     | episodic)   |     +--------------+     +---------+
                        +-------------+
```

## Tech stack

Not applicable -- no implementation [source: related-ding-survey].

## Simulation / backtest story

The survey catalogues how reviewed papers evaluate. Most rely on
historical daily OHLC replays plus a coincident news feed; none of the
surveyed systems describe live paper trading on an exchange testnet
[source: related-ding-survey]. Backtests in this sub-field commonly run
on US equities, DJIA constituents, a handful of crypto assets, or S&P 500
[source: related-ding-survey].

## Our platform mapping

- No direct mapping -- this file exists to anchor terminology. The
  taxonomy maps onto our repo like so:
  - *News-driven* -> a `Strategy` that subscribes to bars plus a
    background news fetcher updating state on `on_data`
  - *Reflection-driven* -> a `Strategy` whose `on_event` handler
    appends trade outcomes to a reflection log replayed nightly
  - *Factor-optimization* -> offline LLM pipeline emits a parameter
    pack consumed by a thin rules-based `Strategy`. This is the
    TiMi pattern.

## Integration plan

1. Use this paper as a terminology source only. No code port.
2. Cross-reference its taxonomy when classifying our own agents (R11+
   `agent-*` directories).
3. Lift its evaluation-metric checklist (CR, Sharpe, Sortino, max
   drawdown, Calmar) for `competition/eval` scorecards.

## Competition fit

Not applicable -- survey paper. Its three-way taxonomy and metric
checklist are already informally used in the repo's eval scripts
(see `competition/evaluate.py` and `competition/archive/evaluate_round*.py`).

## Effort estimate

**S** -- read once, extract taxonomy table, link from INDEX.

## Open questions

- v2 of this survey landed in March 2026 [source: related-ding-survey].
  Need to re-read v2 to see if any newer systems (post-TradingAgents,
  post-FinCon) were added that TiMi didn't cite. That could give us a
  fourth row in the "systems to port" table.
