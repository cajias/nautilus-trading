# TradingAgents

## System

- **Paper**: TradingAgents: Multi-Agents LLM Financial Trading Framework
- **Authors**: Yijia Xiao, Edward Sun, Di Luo, Wei Wang
- **Year**: 2024/2025 (arXiv v1 Dec 28 2024; v7 Jun 3 2025)
- **Link**: https://arxiv.org/abs/2412.20138
- **Code**: https://github.com/TauricResearch/TradingAgents (48.6k stars,
  Apache-ish; Oral @ Multi-Agent AI in the Real World workshop)
  [source: related-tradingagents, related-tradingagents-github]

## What it does

Multi-agent LLM framework that mirrors a real-world trading firm. Agents
specialize in analyst roles (fundamentals, sentiment, technical, news),
bull/bear researcher debates, a trader who synthesizes the debate, and
a risk-management team that monitors exposure
[source: related-tradingagents-github, related-tradingagents]. Decisions
are reached by collaborative discussion between agents before an order
is emitted.

## Architecture

```
   +-----------------+   +----------------+   +----------------+
   | Fundamentals A. |   | Sentiment A.   |   | Technical A.   |
   +--------+--------+   +--------+-------+   +-------+--------+
            \\                     |                   //
             \\          +---------v---------+       //
              +--------->| Researcher debate  |<-----+
                         |  (Bull vs Bear)    |
                         +---------+----------+
                                   v
                          +--------+---------+
                          | Trader (synth)   |
                          +--------+---------+
                                   v
                          +--------+---------+
                          | Risk management  |
                          +--------+---------+
                                   v
                             buy / sell / hold
```

Built on LangGraph for graph-structured agent orchestration
[source: related-tradingagents-github].

## Tech stack

- **Language**: Python 3.13 via conda, `pip install .`
  [source: related-tradingagents-github]
- **Orchestration**: LangGraph [source: related-tradingagents-github]
- **LLM providers**: OpenAI, Google, Anthropic, xAI, OpenRouter, and
  Ollama (local!) [source: related-tradingagents-github]
- **Data**: yfinance / pandas (inferred from the pip install manifest;
  requires API keys for news/sentiment feeds)
- **License**: See repo (48.6k star; project disclaims "research
  purposes", not financial advice) [source: related-tradingagents-github]

## Simulation / backtest story

Paper reports "notable improvements in cumulative returns, Sharpe ratio,
and maximum drawdown" over baselines on a stock trading framework
[source: related-tradingagents]. Evaluation is daily-bar replay; no
mention of intraday or orderbook simulation
[source: related-tradingagents]. TiMi includes TradingAgents as a
baseline in its own experiments on crypto altcoins
[source: arxiv-2510.04787-html].

## Our platform mapping

| TradingAgents concept        | Nautilus equivalent                         |
|------------------------------|---------------------------------------------|
| Analyst team (4 LLM roles)   | Offline subagents writing to JSON/parquet   |
| Bull vs Bear debate          | Offline Claude Code sub-agent workflow      |
| Trader synthesizer           | Nightly cron that emits a decision pack     |
| Risk management agent        | `StrategyConfig.max_position_size` etc.     |
| Per-day LLM call             | NOT in `on_bar` -- must be decoupled        |
| Stock ticker universe        | Swap to 8 Binance Spot pairs                |

The graph pattern maps well to Claude Code sub-agents and to TiMi's
offline/online split.

## Integration plan

1. `pip install tradingagents` in an isolated Python venv outside the
   main repo (avoid poisoning `uv.lock`).
2. Write a shim in `strategies/crypto/_lib/tradingagents_runner.py`
   that invokes the upstream pipeline nightly with our 8 pairs and
   writes `strategies/crypto/_cache/tradingagents_decisions.parquet`
   (ts, pair, action, confidence, rationale).
3. Build `strategies/crypto/tradingagents_live.py`: a thin
   `Strategy` subclass that loads today's decision pack in
   `on_start`, subscribes to 1-day bars, and fires the pre-computed
   action at bar close.
4. Point the nightly LLM at **Ollama** to keep cost zero -- framework
   supports it natively.
5. Replace the news sources (stock-oriented) with a crypto feed via
   a new `DataProvider` (CryptoPanic or a summarized RSS).
6. Define a Claude Code agent `.claude/agents/tradingagents-nightly.md`
   for orchestration.

## Competition fit

**Partial**. Blockers:
- Stock-centric news and fundamentals analyst modules must be replaced
  or stubbed for crypto.
- LLM latency per decision violates the implicit `on_bar` budget --
  must decouple to nightly offline pass.
- Spot-only long-only: the upstream framework outputs
  buy/sell/hold, which is compatible (hold + buy-only mapping).
- `Decimal` + `make_price` + `make_qty`: pure execution-layer concern
  of our Nautilus wrapper, not the upstream framework.

## Effort estimate

**M** -- framework is pip-installable and actively maintained; main
work is the crypto data adapter and the Nautilus execution wrapper.
~3-5 days.

## Open questions

- License: the INDEX lists this as "Apache-ish" but the LICENSE file
  needs a direct read -- the fetched README didn't surface it clearly
  [source: related-tradingagents-github]. Confirm compatible with our
  intended use before porting.
- Can Ollama handle the multi-agent debate latency acceptably on
  user's hardware (Raspberry Pi vs local dev machine)? The user cares
  -- confirm target environment.
- News fetcher: does the upstream repo assume a specific paid API
  (AlphaVantage, Polygon)? Need to grep `tradingagents/` source.
- Does the Bull/Bear debate add material signal beyond a single
  analyst, or is it cosmetic? Worth testing ablation before committing
  to full port.
