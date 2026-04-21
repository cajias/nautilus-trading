# FinGPT

## System

- **Paper**: FinGPT: Open-Source Financial Large Language Models
- **Authors**: Hongyang Yang, Xiao-Yang Liu, Christina Dan Wang
- **Year**: 2023 (FinLLM Symposium @ IJCAI; Best Presentation)
- **Link**: https://arxiv.org/abs/2306.06031
- **Code**: https://github.com/AI4Finance-Foundation/FinGPT (19k stars)
  and https://github.com/AI4Finance-Foundation/FinNLP
  [source: related-fingpt, related-fingpt-github]

## What it does

Open-source financial LLM project. Not a trading agent per se but a
data-centric pipeline for building financial LLMs: automatic data
curation, lightweight LoRA fine-tuning, and application templates for
robo-advising, algorithmic trading, and low-code development
[source: related-fingpt]. TiMi uses FinGPT as a "news-driven" LLM
baseline in its experiments [source: arxiv-2510.04787-html].

## Architecture

```
+----------------+   +----------------+   +----------------+
| Raw financial  |-->| Auto-curation  |-->| LoRA fine-tune |
| data (CNBC,    |   | pipeline       |   | on LLaMA / etc |
| Reuters, SEC)  |   +----------------+   +--------+-------+
+----------------+                                 |
                                                   v
                                          +--------+---------+
                                          | FinGPT model     |
                                          | (sentiment head) |
                                          +--------+---------+
                                                   v
                          +-----------+----+---------+-----------+
                          | Robo-advise | Algo-trade | Forecast  |
                          +-------------+------------+-----------+
```

## Tech stack

- **Language**: Python [source: related-fingpt-github]
- **Model family**: LoRA-fine-tuned LLaMA / ChatGLM / Falcon variants
  hosted on HuggingFace [source: related-fingpt-github]
- **License**: Repo is listed as an open template (19k stars)
  [source: related-fingpt-github]
- **Sister repo**: FinNLP (data pipeline)
  [source: related-fingpt-github]

## Simulation / backtest story

Paper is a position/infrastructure paper rather than a full backtest
study; it showcases applications including algorithmic trading as
"stepping stones for users" [source: related-fingpt]. TiMi evaluates
the trading application under its own benchmark
[source: arxiv-2510.04787-html]. Backtests in the FinGPT repo sub-projects
use daily bars plus sentiment scores.

## Our platform mapping

| FinGPT concept           | Nautilus equivalent                             |
|--------------------------|-------------------------------------------------|
| Fine-tuned sentiment LLM | Hosted model call (OpenAI/Anthropic/local LLM)  |
| Sentiment score per bar  | Field on a custom `Bar`-like data object        |
| Algo-trading application | A `Strategy` consuming the sentiment stream     |
| Data curation pipeline   | Our own `DataProvider` ingesting from RSS       |

Trying to host a fine-tuned model locally is out-of-scope; the useful
artifact is the sentiment-score output, which we can replicate with a
cheaper LLM call or an off-the-shelf FinBERT-style model.

## Integration plan

1. Do NOT port the fine-tuning pipeline. Extract only the
   sentiment-signal pattern.
2. Build `nautilus_trading/data/sentiment_provider.py` that ingests
   crypto news from RSS/GDELT and emits a per-symbol-per-hour
   sentiment score (via a single Anthropic call or FinBERT).
3. Publish that score as a custom `Data` subclass so Nautilus routes
   it to `on_data`.
4. Write `strategies/crypto/sentiment_overlay.py` that combines the
   sentiment score with a base trend signal.

## Competition fit

**Not ready as-is**. Blockers:
- Building/training FinGPT from scratch exceeds competition budget
  and time.
- The FinGPT repo is not a drop-in trader -- it's a model family +
  data pipeline. The actual trader is a downstream example.
- Sentiment-only signals are weak in fast markets; must be combined
  with a base price signal to pass eval.

## Effort estimate

**XL** if we port the fine-tuning pipeline; **M** if we only port
the sentiment-score pattern (recommended). ~3-5 days for the
pragmatic path.

## Open questions

- Which sentiment source? The user has previously preferred "single
  authoritative feed" style ingestion -- confirm.
- Do we host a local FinBERT or pay per sentiment call? Changes the
  `live` cost profile significantly.
- Is there a pre-existing crypto-tuned FinBERT variant we can drop
  in? FinGPT-family has a crypto sentiment sub-project; confirm
  quality before committing.
