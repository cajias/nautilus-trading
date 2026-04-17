# FinCon

## System

- **Paper**: FinCon: A Synthesized LLM Multi-Agent System with Conceptual
  Verbal Reinforcement for Enhanced Financial Decision Making
- **Authors**: Yangyang Yu, Zhiyuan Yao, Haohang Li, Zhiyang Deng,
  Yupeng Cao, Zhi Chen, Jordan W. Suchow, Rong Liu, Zhenyu Cui,
  Zhaozhuo Xu, Denghui Zhang, Koduvayur Subbalakshmi, Guojun Xiong,
  Yueru He, Jimin Huang, Dong Li, Qianqian Xie
- **Year**: 2024 (arXiv v1 Jul 9 2024; v3 Nov 7 2024)
- **Link**: https://arxiv.org/abs/2407.06567
- **Code**: Not surfaced in the fetched abstract page
  [source: related-fincon]

## What it does

LLM-based multi-agent framework with "conceptual verbal reinforcement"
tailored for financial tasks [source: related-fincon]. Mirrors the
organizational structure of real-world investment firms -- it uses a
manager/analyst communication hierarchy to synthesize multi-sourced
information and refine decisions through experience between rounds
[source: related-fincon]. Same first author as FinMem.

## Architecture

```
              +-------------+
              | Manager     |
              | (synthesis) |
              +------+------+
                     ^
     +---------------+----------------+
     |               |                |
+----+-----+   +-----+----+    +-----+----+
| Analyst1 |   | Analyst2 |    | Analyst3 |
| (news)   |   | (filings)|    | (technic)|
+----+-----+   +-----+----+    +-----+----+
     |               |                |
     v               v                v
   +---------------------------------+
   | Shared market environment        |
   +----------------+----------------+
                    v
         +----------+------------+
         | Conceptual verbal     |
         | reinforcement loop    |
         | (refines next round)  |
         +-----------------------+
```

The verbal-reinforcement step is the novel contribution: between
decision rounds, the manager agent writes a conceptual critique that
adjusts analyst prompts for next round [source: related-fincon].

## Tech stack

- **Language**: Python (inferred from FinMem sibling project)
- **LLMs**: Multi-agent LLM orchestration
  [source: related-fincon]
- **Comparison**: Claims superior multi-sourced information synthesis
  versus single-agent baselines [source: related-fincon]

## Simulation / backtest story

Backtest against historical market data on "diverse FINancial tasks"
[source: related-fincon]. No mention of tick data, orderbook, or live
paper trading [source: related-fincon]. Evaluation window is likely
multi-year daily US equities.

## Our platform mapping

| FinCon concept              | Nautilus equivalent                        |
|-----------------------------|--------------------------------------------|
| Manager agent               | Orchestrator Claude sub-agent              |
| Analyst agents              | Multiple Claude sub-agents w/ specialties  |
| Shared market environment   | `Cache` + `Portfolio` in a `Strategy`      |
| Conceptual verbal           | Nightly Claude meta-sub-agent that          |
|   reinforcement             | rewrites analyst prompts based on PnL      |
| Trading actions             | `order_factory.market` + `submit_order`    |

This maps cleanly onto the Claude Code multi-agent pattern we already
use for the competition's agent-1..5 directories.

## Integration plan

1. Design a Claude Code sub-agent pack under
   `.claude/agents/fincon/` with one manager + three analyst defs.
2. Nightly cron runs the analyst fan-out, manager synthesis, and a
   reflection step that patches the analyst prompt files in place
   (the "verbal reinforcement" loop).
3. Output: a decision pack parquet consumed by a thin
   `strategies/crypto/fincon_live.py` the same way FinMem/TradingAgents
   are wired.
4. Reuse the news/sentiment provider built for FinMem.

## Competition fit

**Not ready as-is**. Blockers:
- Heavy LLM cost per round (manager + N analysts + reinforcement step).
- Original targets stock filings + news; needs crypto feed adaptation.
- The prompt-rewriting loop could accidentally overfit to the TEST
  period if not gated -- this is the biggest correctness risk.

## Effort estimate

**L** -- architecture is clean, but the verbal-reinforcement loop
needs careful sandboxing to avoid overfitting. ~1 week.

## Open questions

- Is FinCon open source? Abstract does not list a repo
  [source: related-fincon]. Confirm before committing.
- How does verbal reinforcement avoid hindsight bias? Need to read
  the full paper for the critique-generation prompt.
- Does FinCon's improvement over FinMem come from the reinforcement
  loop or from simply having more analyst agents? Ablation matters
  for our port decisions.
- Manager-analyst hierarchy vs TradingAgents' firm simulation: same
  thing or meaningfully different? Need to decide which one to port
  first (see INDEX.md Top-3).
