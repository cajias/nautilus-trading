# FinAgent

## System

- **Paper**: A Multimodal Foundation Agent for Financial Trading:
  Tool-Augmented, Diversified, and Generalist
- **Authors**: Wentao Zhang, Lingxuan Zhao, Haochong Xia, Shuo Sun,
  Jiaze Sun, Molei Qin, Xinyi Li, Yuqing Zhao, Yilei Zhao, Xinyu Cai,
  Longtao Zheng, Xinrun Wang, Bo An
- **Year**: 2024 (arXiv v1 Feb 28 2024; v3 Jun 28 2024)
- **Link**: https://arxiv.org/abs/2402.18485
- **Code**: No link in the fetched abstract [source: related-finagent]

## What it does

Multimodal foundation agent for financial trading. Consumes numerical
(prices), textual (news), and visual (K-line chart images) data; uses a
market-intelligence module, a dual-level reflection module, and a
diversified memory retrieval system to adapt to market dynamics
[source: related-finagent]. Emphasizes tool augmentation and integrates
established trading strategies with expert insights
[source: related-finagent].

## Architecture

```
  +------------------+
  | Market intelligence (text + price + K-line image)
  +--------+---------+
           v
  +------------------+         +------------------+
  | Memory retrieval | <-----> | Dual-level       |
  | (diversified)    |         | reflection       |
  +--------+---------+         +--------+---------+
           v                             v
           +--------------+--------------+
                          v
              +-----------+-----------+
              | Tool-augmented trader |
              | (strategy library)    |
              +-----------+-----------+
                          v
                   buy / sell / hold
```

Dual-level reflection = fast adaptation to market dynamics + slower
learning from historical decisions [source: related-finagent].

## Tech stack

- **Data modalities**: Numerical, textual, visual (K-line chart images)
  [source: related-finagent]
- **LLMs**: Multimodal foundation model (paper is vague; presumably
  GPT-4V or similar at time of writing) [source: related-finagent]
- **Language / repo**: Not surfaced in the fetched abstract page
  [source: related-finagent]

## Simulation / backtest story

The paper presents FinAgent as a generalist across "quantitative trading
and high-frequency trading with various assets" but the abstract gives
no detail on HFT infrastructure used -- it's very likely daily bar
replay with news + chart image windows [source: related-finagent].

## Our platform mapping

| FinAgent concept            | Nautilus equivalent                          |
|-----------------------------|----------------------------------------------|
| K-line chart image input    | NO direct equivalent -- needs matplotlib     |
|                             | renderer + file cache per bar close          |
| Market intelligence (text)  | New `DataProvider` for news                  |
| Dual-level reflection       | Nightly offline Claude sub-agent             |
| Tool-augmented trader       | Rules library pre-loaded in `StrategyConfig` |
| Numerical price input       | `subscribe_bars`                             |
| Action output               | `order_factory.market` + `submit_order`      |

The chart-image modality is the friction point -- all other prior
systems consume numbers or text only.

## Integration plan

1. Skip the visual modality entirely for v1 (treat FinAgent as a
   news + numerical agent minus the K-line image branch).
2. Port the dual-level reflection pattern into a Claude Code sub-agent
   definition that runs twice a day (fast) and once a week (slow).
3. Build the same thin `Strategy` execution wrapper used for FinMem.
4. If initial results warrant it, add a matplotlib K-line renderer
   step to the nightly pipeline and feed images to a vision LLM.

## Competition fit

**Not ready as-is**. Blockers:
- Requires a multimodal LLM (vision) which is API-costly and slow.
- Needs news feed (same blocker as FinMem).
- No open source implementation identified in the abstract fetch --
  we'd be reimplementing from scratch or fishing for a third-party
  fork.

## Effort estimate

**XL** -- no open-source anchor, multimodal, requires vision LLM. Do
not attempt unless simpler ports fail.

## Open questions

- Is there a public code release? The fetched abstract doesn't list
  one and the paper has had multiple revisions -- need to verify
  before any port.
- Does the visual modality actually add signal, or is it window
  dressing? The abstract doesn't include an ablation.
- Which multimodal backbone? GPT-4V, Claude, local LLaVA? Cost and
  availability vary 10x between these.
