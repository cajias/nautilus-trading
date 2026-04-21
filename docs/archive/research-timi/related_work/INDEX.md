# TiMi Related Work -- Agentic Trading Systems

Task #52 expansion of the Related Work section of TiMi (arxiv 2510.04787). The
TiMi authors classify prior agentic trading work into three architectural
families per Ding et al. (2024) [source: related-ding-survey]:

1. **News-driven** agents that condition on up-to-date news/events
2. **Reflection-driven** agents that refine decisions through self-critique
   and debate
3. **Factor-optimization** agents used as alpha miners rather than direct
   traders

TiMi also uses FinGPT, FinMem, and TradingAgents as baselines in its own
experiments [source: arxiv-2510.04787-html]. All of these are surveyed here,
plus the Ding et al. survey itself and Koa et al.'s SEP reflection framework
which TiMi cites as a representative reflection-driven agent.

## Landscape

The agentic trading field splits cleanly along two axes the TiMi paper
exploits in its critique:

- **Data dependency**: news-driven systems require a continuous sentiment
  or fundamentals feed (FinMem, FinAgent, CryptoTrade, FinGPT); pure
  price/orderbook systems (StockAgent, TradingAgents risk team) do not.
- **Decision loop latency**: most prior agents call an LLM per decision
  (per-bar or per-day), which TiMi argues is fundamentally incompatible
  with intraday quantitative execution. Only factor-optimizing agents
  decouple "LLM thinking" from "trade firing" the way TiMi does.

For our Nautilus + Binance-spot-testnet environment, the decision-loop
latency is the load-bearing constraint: anything that needs an LLM call
in `on_bar` cannot run faster than ~5s/tick and racks up API cost.

## Comparison table

| System        | Year | Agent count      | Needs LLM? | Needs news/sentiment?      | Order book? | Effort | Competition-ready? |
|---------------|------|------------------|------------|----------------------------|-------------|--------|--------------------|
| Ding survey   | 2024 | n/a (survey)     | n/a        | n/a                        | n/a         | n/a    | n/a                |
| FinMem        | 2023 | 1 (layered mem)  | Yes (per-day) | Yes (10-K, news)        | No          | L      | No (needs filings) |
| FinGPT        | 2023 | 1 (fine-tuned)   | Yes (model)| Yes (sentiment head)       | No          | XL     | No (training cost) |
| FinAgent      | 2024 | 1 (multimodal)   | Yes (per-day) | Yes (news + K-line img) | No          | XL     | No (vision model)  |
| CryptoTrade   | 2024 | 1 (reflective)   | Yes (per-day) | Yes (on-chain + news)   | No          | L      | Partial (crypto!)  |
| Koa SEP       | 2024 | 1 (self-reflect) | Yes (PPO fine-tune) | Yes (tweets)      | No          | XL     | No (fine-tune)     |
| StockAgent    | 2024 | N (ABM sim)      | Yes (per-agent) | Macro + events         | No          | M      | No (sim not live)  |
| FinCon        | 2024 | ~6 (mgr/analyst) | Yes (per-day) | Yes (news + filings)    | No          | L      | No (needs filings) |
| TradingAgents | 2024 | 7+ (firm sim)    | Yes (per-day) | Yes (news + sentiment)  | No          | M      | Partial (OSS, port)|

**Legend**: "Competition-ready?" = can this run unmodified as a Round-11+
`Strategy` subclass under the 8 hard constraints (spot-only, long-only,
`Decimal`, price/qty rounding, `on_bar` latency budget)?
See `competition/COMPETITION.md` for the full list.

## Top 3 we should port first

Ranked by tractability-per-upside for our crypto competition setting.

1. **TradingAgents (Xiao et al. 2025)** -- Open-source (48k stars), pip-installable,
   LangGraph-based, supports multiple LLM backends including Ollama (local).
   The firm-simulation structure (analyst team -> researchers -> trader ->
   risk team) is the closest architectural fit to a decoupled TiMi-style
   offline brain + online Nautilus `Strategy`. We can run the full pipeline
   nightly, store a rule-set, and let a thin Nautilus strategy execute it
   [source: related-tradingagents-github].

2. **CryptoTrade (Li et al. 2024b)** -- The only crypto-native system in
   the TiMi bibliography. Combines on-chain signals (Etherscan-style) with
   off-chain news and reflects daily. Short paper, clear benchmark methodology,
   anonymized code available. Best mapping to our BTC/ETH/altcoin pairs
   and directly informs the sentiment/on-chain hybrid signal family
   [source: related-cryptotrade].

3. **FinMem (Yu et al. 2024a)** -- Single-agent layered-memory architecture
   (sensory / short / long / reflection). MIT-licensed GitHub repo, Python,
   well-documented. TiMi uses it as a baseline so the eval methodology is
   known. Memory module is reusable independent of the stock-specific
   pipeline -- good candidate to wire into a Nautilus strategy that keeps
   a decision journal in `self.cache` [source: related-finmem, related-finmem-github].

## Files

Per-system deep-dives in this directory:

- `ding-survey.md` -- the taxonomy paper TiMi leans on
- `finmem.md`
- `fingpt.md`
- `finagent.md`
- `cryptotrade.md`
- `koa-sep.md`
- `stockagent.md`
- `fincon.md`
- `tradingagents.md`

Open questions and cross-system blockers are noted at the end of each
per-system file.
