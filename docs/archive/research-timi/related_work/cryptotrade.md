# CryptoTrade

## System

- **Paper**: A Reflective LLM-based Agent to Guide Zero-shot
  Cryptocurrency Trading
- **Authors**: Yuan Li, Bingqiao Luo, Qian Wang, Nuo Chen, Xu Liu,
  Bingsheng He
- **Year**: 2024 (arXiv v1 Jun 27 2024)
- **Link**: https://arxiv.org/abs/2407.09546
- **Code**: Anonymous 4open.science link mentioned in abstract
  [source: related-cryptotrade]

## What it does

LLM-based trading agent that combines on-chain crypto data (transparent,
immutable) with off-chain signals (news). Uses a reflective mechanism to
refine daily trading decisions by analyzing outcomes of prior trades
[source: related-cryptotrade]. Claims superior returns versus
traditional strategies and time-series baselines across multiple
cryptocurrencies and market regimes [source: related-cryptotrade].

## Architecture

```
  +-------------+    +---------------+
  | On-chain:   |    | Off-chain:    |
  | ETH tx,     |    | news, social  |
  | flows, DEX  |    +-------+-------+
  +------+------+            |
         |                   |
         +------+  +---------+
                v  v
         +--------+---------+
         | LLM prompt       |
         | (zero-shot)      |
         +--------+---------+
                  v
           +------+------+
           | Decision    |
           | (buy/sell)  |
           +------+------+
                  v
         +--------+---------+
         | Reflection loop  |
         | (prior outcomes) |
         +--------+---------+
                  v
          next-day decision
```

## Tech stack

- **Language**: Python (inferred from arxiv conventions; not
  confirmed) [source: related-cryptotrade]
- **LLMs**: Zero-shot -- no fine-tuning required, so pluggable
  [source: related-cryptotrade]
- **Data sources**: On-chain (likely Etherscan or Dune-style) + news
  feed [source: related-cryptotrade]
- **Benchmarks**: Claim benchmark over multiple crypto + multiple
  regimes [source: related-cryptotrade]

## Simulation / backtest story

Daily replay of crypto OHLC, paired with a windowed news feed and
on-chain snapshots. No mention of tick data, orderbook, or live paper
trading in the abstract [source: related-cryptotrade]. Evaluation is
across "various cryptocurrencies and market conditions"
[source: related-cryptotrade].

## Our platform mapping

| CryptoTrade concept    | Nautilus equivalent                              |
|------------------------|--------------------------------------------------|
| On-chain data          | New `DataProvider` (Etherscan API / The Graph)   |
| Off-chain news         | Crypto news `DataProvider` (same as FinMem port) |
| Reflective loop        | Nightly Claude sub-agent writing to parquet      |
| Zero-shot LLM call     | Offline -- NOT in `on_bar`                       |
| Daily decision         | `subscribe_bars(... 1-DAY-LAST-INTERNAL)`        |

This is the cleanest mapping of any cited system because it's
crypto-native from the start.

## Integration plan

1. Build an `OnChainDataProvider` that snapshots large-whale transfers
   and exchange in/outflows for BTC and ETH (the only two in our 8
   pairs with meaningful on-chain signal -- altcoins on Binance Spot
   are off-chain for our purposes).
2. Reuse the same news provider built for FinMem.
3. Nightly Claude sub-agent takes (on-chain snapshot, news window,
   price window, prior reflection) -> decision pack.
4. Thin `strategies/crypto/cryptotrade_live.py` reads decision pack
   at start, fires at each 1-DAY bar close, follows the standard
   Nautilus execution pattern (`make_price`, `make_qty`, `Decimal`).
5. Reflection buffer persisted as a small JSON file under
   `strategies/crypto/_cache/` keyed by pair.

## Competition fit

**Partial -- most crypto-native of all cited systems**. Caveats:
- On-chain snapshot is only meaningful for BTC/ETH; 6/8 competition
  pairs would degrade gracefully to an off-chain-only variant.
- LLM-in-loop latency -> decouple to nightly pass (same as FinMem).
- Decision frequency is daily; may underperform in fast-moving
  competition rounds with short eval windows.
- Spot-only long-only: compatible by clamping sell actions to "flatten
  position" rather than short.

## Effort estimate

**L** -- the on-chain provider is the main novel component (~2 days);
the rest reuses the FinMem infrastructure. ~4-6 days total.

## Open questions

- On-chain data source: Etherscan (rate-limited, free tier) vs The
  Graph (more flexible, needs hosted service) vs Dune API (paid).
  User preference needed before committing.
- Is the anonymous 4open.science repo still live? Need to confirm
  before planning detailed port.
- Is daily granularity sufficient for competition eval windows? Some
  rounds may be shorter than the decision cadence.
- Does the reflection loop add signal over a stateless zero-shot
  pass? Abstract claims yes -- verify with a cheap ablation.
