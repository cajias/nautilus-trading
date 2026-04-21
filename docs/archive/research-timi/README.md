# Archived: TiMi research corpus

**Moved:** 2026-04-21 (chore branch `chore/docs-cleanup-post-subproject-a`).
**Original location:** `docs/research/timi/`.
**Reason:** Belongs to sub-project C (competition agent teams), which has not started. Archived here so it's not mistaken for active design while A/B are in flight. Kept intact for C's kickoff.

**Path-reference note.** Internal references inside these files (e.g., `docs/research/timi/adapted/round<N>__<PAIR>.md`, `docs/research/timi/macro/`) describe the intended runtime layout *as originally designed*, before the archive move. When sub-project C activates this corpus, either:

- move the corpus back to `docs/research/timi/` (treat this archive as a pause-button), or
- rewrite the internal path references to the `docs/archive/research-timi/...` equivalents.

Do NOT partially rewrite — pick one and apply consistently.

---

## Contents (at archive time)

- `DESIGN.md` — mapping of the TiMi paper's 4-agent architecture onto NautilusTrader.
- `DIRECTIVE_FORMAT.md` — canonical directive schema for A_fr → A_be communication.
- `OPEN_QUESTIONS.md` — unresolved design decisions.
- `PAPER_SUMMARY.md` — notes on the source TiMi paper.
- `AGENT_SPECS/{A_be,A_fr,A_ma,A_sa}.md` — per-agent specifications.
- `prompts/{a_be,a_fr,a_ma,a_sa}.md` — draft prompt templates for each agent.
- `related_work/*.md` — literature review (FinGPT, FinMem, TradingAgents, etc.).
