# Phase 13 — Opportunity research

Date: 2026-11-26 · Decision: ADR-023 in [PHASE_01_GAP_REPORT.md](PHASE_01_GAP_REPORT.md#3-architecture-decisions-adrs).

## Plain English
- **Jackson can now research opportunities and rank them.** Point it at pages you've approved (marketplace listings, trend pages, competitor sites). It pulls out real signals: search interest, growth, best-seller rank, reviews, competitors, price, unit cost, shipping weight, return rate and IP risk.
- **No made-up evidence.** Every signal must link to a page that was actually fetched; anything else is thrown out. Missing data stays missing and makes the score less certain, instead of being guessed. A trademark or patent red flag caps the score at 20.
- **Clear scores:** each opportunity gets a 0–100 score with a visible breakdown (demand, competition, margin, shipping, returns, IP safety), an uncertainty figure, and a brief listing every source and date.
- **Your call:** duplicates merge automatically. Only you change an opportunity's status (validated, rejected, pursuing); research can't.
- **No AI key yet?** Research fetches the pages but extracts nothing, and says it ran in MOCK mode.

## What was built
| Piece | File |
|---|---|
| `opportunities` table (RLS; worker may refresh evidence columns but never `status`; worker inserts must be `new`) | `models.py`, `migrations/versions/0010_opportunities.py` |
| Signal types, normalize, dedupe, merge, transparent scoring, brief, upsert | `jackson_api/opportunities.py` |
| `opportunity_research` handler: web.fetch via the gateway → model extraction → drop unsourced → upsert + rank | `jackson_api/research.py` |
| API: add/enrich, ranked board, get (+brief), set status (owner/admin, audited), start research | `jackson_api/routes_opportunities.py` |

Scoring weights: demand 0.30, margin 0.25, competition 0.15, logistics / returns / IP safety 0.10 each. Weights are renormalized over the factors that have data.
- Uncertainty = 1 − (evidence × coverage).
- Evidence counts independent source domains; signals older than 180 days count half.
- Social platforms (TikTok etc.) need authorized access and aren't scraped.

## Tests
- **API (`services/api`): 254 passed** against real PostgreSQL 16 (13 new in `test_opportunities.py`).
- **Pure scoring:**
  - a strong opportunity scores high with low uncertainty
  - missing data raises uncertainty instead of being guessed
  - an IP flag caps the score
  - stale signals count less
  - the unit-economics margin overrides the gross price-vs-cost estimate
  - normalization rejects unsourced, unknown, non-numeric and negative signals; dedupe and merge
- **Board:** ranking; dedupe + enrichment by title; owner-only status (viewer 403, worker can't UPDATE status); tenant isolation.
- **Research** (local test site + faked model reply):
  - sourced signals are stored
  - **a signal citing a never-fetched URL is dropped**
  - page text really reaches the model
  - no model → MOCK, nothing created
  - no web grant → nothing fetched
- **Mutation checks** (all caught):
  - keeping unsourced signals
  - removing the IP cap
  - guessing missing data

## Next: phase 14 — product economics
