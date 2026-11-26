# Phase 14 — Product economics

Date: 2026-11-26 · Decision: ADR-024 in [PHASE_01_GAP_REPORT.md](PHASE_01_GAP_REPORT.md#3-architecture-decisions-adrs).

## Plain English
- **Jackson can now tell you if a product actually makes money.** Give it a price, supplier cost and whatever else you know (shipping, duties, packaging, card fees, platform fees, returns, ad cost per sale, monthly fixed costs, profit goal). You get back:
  - landed cost per unit
  - profit per order before and after ads, and the margin %
  - the most you can pay for a sale and still break even (and the matching minimum return on ad spend)
  - how many orders a month hit your profit goal
  - what happens if price, ad costs or returns move
- **Nothing hidden:** anything you didn't provide uses a stated default (for example card fees 2.9% + $0.30, returns 5%), and it's listed under "assumptions". Results are labeled estimates. Money math is exact to the cent.
- **Opportunities get real margins.** Saving economics on an opportunity replaces the rough price-minus-cost guess with the real number and re-scores it. In the test, an exciting-looking 80% gross margin became a real 18% after fees, returns and ads.
- **Agents can run the numbers too** with the `finance.unit_economics` tool.

## What was built
| Piece | File |
|---|---|
| Pure Decimal calculator with validated inputs, defaults reported as assumptions, warnings, sensitivity | `jackson_api/economics.py` |
| `finance.unit_economics` tool (read-only, deterministic) | `jackson_api/tools/builtin.py` |
| `POST /economics/calculate` (any member; saves nothing), `POST /opportunities/{id}/economics` (owner/admin; saves a snapshot + re-scores, audited) | `jackson_api/routes_opportunities.py` |
| Worker may attach economics to opportunities (column grant), still never `status` | `migrations/versions/0011_economics.py` |

## Worked example (also a test)
Inputs:
- price $30, supplier cost $6, inbound shipping $1, duty 10%, packaging $0.50
- outbound shipping $5, card fees 2.9% + $0.30, platform fees 2%
- returns 5% at $6 each (half resellable), ad cost $8 per order
- fixed costs $100/month, profit goal $1,000/month

Results:
| Result | Value |
|---|---|
| Landed cost/unit | $8.20 |
| Contribution before ads | $13.44 |
| Contribution after ads | $5.44 (18.1%) |
| Break-even ad cost per order | $13.44 (ROAS 2.23) |
| Orders/month for the profit goal | 203 |

## Tests
- **API (`services/api`): 268 passed** against real PostgreSQL 16 (14 new in `test_economics.py`).
- **The math:**
  - the worked example matches the hand calculation to the cent
  - defaults are listed as assumptions
  - a money-losing product is flagged (and never reaches the target)
  - sensitivity moves the right way
  - multi-unit orders spread shipping
  - exact decimal math; invalid inputs rejected
- **API and tool:**
  - the calculator endpoint saves nothing (viewers allowed); invalid input → 422
  - saving economics re-scores the opportunity with "unit economics" as the margin basis (owner/admin only, audited)
  - agents use the tool through the gateway
- **Mutation checks** (both caught):
  - dropping the fixed card fee fails 4 tests
  - hiding assumptions fails 1

## First vertical slice status
"Find a product opportunity and build a draft store":
- ✅ plan (08)
- ✅ research with evidence (13)
- ✅ finance margin (14)
- ✅ approvals for paid actions (07)
- ⏳ sandbox Shopify draft (15)
- ⏳ dashboard (19)

## Next: phase 15 — Shopify draft connector
This needs a **Shopify development store and an Admin API token** (owner action). Until then it runs against a clearly labeled mock.
