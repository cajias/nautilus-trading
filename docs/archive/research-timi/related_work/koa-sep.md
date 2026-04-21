# Koa et al. SEP (Summarize-Explain-Predict)

## System

- **Paper**: Learning to Generate Explainable Stock Predictions using
  Self-Reflective Large Language Models
- **Authors**: Kelvin J.L. Koa, Yunshan Ma, Ritchie Ng, Tat-Seng Chua
- **Year**: 2024 (WWW 2024; arXiv v1 Feb 6, v3 Feb 29 2024)
- **Link**: https://arxiv.org/abs/2402.03659
- **Code**: Not surfaced in the fetched abstract page
  [source: related-koa]

## What it does

A Summarize-Explain-Predict (SEP) framework that uses a self-reflective
LLM agent plus Proximal Policy Optimization (PPO) to teach an LLM to
generate explainable stock predictions -- without requiring
expert-annotated explanations [source: related-koa]. The reflective
agent learns to explain past stock movements through self-reasoning;
the PPO trainer learns to emit the most likely explanation from an
input text stream [source: related-koa].

## Architecture

```
  +------------+    +---------------------+    +--------------+
  | Noisy text |--->| Summarize           |--->|  Summary     |
  | (tweets)   |    +---------------------+    +------+-------+
  +------------+                                       v
                                              +--------+-------+
                                              | Explain        |
                                              | (reflection)   |
                                              +--------+-------+
                                                       v
                                              +--------+-------+
                                              | Predict        |
                                              | (classifier)   |
                                              +--------+-------+
                                                       v
                                                 buy / sell
                  (training signal: PPO with self-reflective rewards)
```

Two training phases: reflective rollout generates explanation-outcome
pairs; PPO then supervises the LLM on those pairs [source: related-koa].

## Tech stack

- **ML framework**: PPO-based fine-tuning [source: related-koa]
- **LLM**: Fine-tuned open-source LLM (paper-level detail not in
  abstract) [source: related-koa]
- **Application scope**: Stock classification + portfolio construction
  [source: related-koa]

## Simulation / backtest story

Evaluation is on stock classification (directional prediction) plus a
downstream portfolio construction task with standard metrics
[source: related-koa]. No live paper trading. Input modality is stock-
related text -- news headlines and/or social posts
[source: related-koa].

## Our platform mapping

| SEP concept                 | Nautilus equivalent                        |
|-----------------------------|--------------------------------------------|
| Summarize step              | Claude sub-agent: text -> summary          |
| Explain (reflective)        | Claude sub-agent: summary + outcome ->     |
|                             | explanation, stored to reflection buffer   |
| Predict classifier          | Offline LLM call or a cheap MLP head       |
| PPO fine-tuning             | NOT portable -- skip                       |
| Portfolio construction      | Weight vector over 8 pairs in config       |

The SEP pattern is the most reusable artifact. PPO fine-tuning is
out-of-scope.

## Integration plan

1. Skip the fine-tuning pipeline entirely.
2. Build a three-stage offline Claude Code sub-agent chain
   (`.claude/agents/sep-summarize.md`,
   `.claude/agents/sep-explain.md`,
   `.claude/agents/sep-predict.md`) that produces daily direction
   + explanation triples.
3. Store reflections in a rolling parquet file so the explain step
   can condition on recent outcomes.
4. Thin runtime `Strategy` consumes the predict output, same pattern
   as FinMem.

## Competition fit

**Not ready as-is**. Blockers:
- PPO fine-tuning is completely out of competition scope.
- Text-only input means we need a crypto news/tweet feed (same
  blocker as FinMem/CryptoTrade).
- Classification head gives direction but not size; need wrapper
  rules for position sizing.
- Explanation generation is expensive per decision.

## Effort estimate

**XL** for a full port (PPO training loop). **M** for the
prompt-only SEP pattern layered over an existing sentiment feed.

## Open questions

- Is there a public SEP code release? Abstract mentions none
  [source: related-koa]. Confirm before planning.
- Does the explainability requirement matter for our competition
  or is pure-PnL sufficient? If the latter, SEP's main contribution
  is moot for us.
- Can we reuse the sentiment feed built for FinGPT/FinMem instead
  of the original tweet stream? Very likely yes, but needs to be
  validated.
