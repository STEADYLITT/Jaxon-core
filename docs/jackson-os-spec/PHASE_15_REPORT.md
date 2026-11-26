# Phase 15 — Shopify draft connector

Date: 2026-11-26 · Decision: ADR-025 in [PHASE_01_GAP_REPORT.md](PHASE_01_GAP_REPORT.md#3-architecture-decisions-adrs).

## Plain English
- **Jackson can now turn an opportunity into a draft product on a Shopify store.** You save the economics (phase 14), click build, and it writes the product copy, creates the product **as a draft**, reads it back from the store to confirm it's really a draft with the right price and title, and saves a check report.
- **Nothing goes live on its own.** Drafts only. Publishing is a separate step that **always** waits in your approvals inbox, even at the highest autonomy level.
- **No made-up selling points.** Copy claiming star ratings, review counts, "best seller", "clinically proven", guaranteed results, "customers love it" or bought followers is rejected.
- **No duplicates.** Building twice reuses the same product.
- **Right now it uses a clearly labeled stand-in store.** The real Shopify connection is built and tested against Shopify's documented API (version 2026-10, checked on shopify.dev). It switches on only when you provide a **development store** and an **Admin API token**. The token stays in settings; it never lands in the database, grants or logs.

## What was built
| Piece | File |
|---|---|
| Copy rules shared by product and content work (no invented reviews/claims, no fake engagement) | `jackson_api/connectors/claims.py` |
| Shopify connector: `ShopifyAdmin` (GraphQL 2026-10: find by handle → `productCreate` DRAFT → `productVariantsBulkUpdate` price → read back; `productUpdate` to publish) and `MockShopify` | `jackson_api/connectors/shopify.py` |
| `ctx.shopify()`: create draft (policy `sandbox_change`) → canonical row → verify; publish (policy `production_change`, always approval) | `jackson_api/shop.py`, `worker.py` |
| `store_build` (first-slice builder + independent QA + report artifact) and `shopify_publish` handlers | `jackson_api/store_builder.py` |
| `shop_products` (RLS; worker can only insert DRAFT rows and update status/verification, never title/price) | `models.py`, `migrations/versions/0012_shop.py` |
| API: `GET /shop/products`, `POST /opportunities/{id}/build-store`, `POST /shop/products/{id}/publish` (verified drafts only) | `jackson_api/routes_shop.py` |
| `shopify.drafts` grant entry (config `mode: mock` or `mode: shopify` + `store_domain` + `dev_store: true`) | `tools/builtin.py` |

## Tests
- **API (`services/api`): 293 passed** against real PostgreSQL 16 (25 new in `test_shop.py`).
- **Copy rules:** 7 kinds of invented claims are rejected; plain factual copy passes.
- **Real Shopify backend** (faked network):
  - exact 2026-10 endpoint and token header; product created as DRAFT; price set on the variant
  - idempotent by handle; user errors are permanent, 429 is retryable
  - live mode needs a dev store, a token and a `*.myshopify.com` domain
- **Sandbox Shopify draft verified:** opportunity → economics → build → DRAFT verified, price 2499¢, QA passed, report downloadable, mock copy labeled.
- **Gates and safety:**
  - needs economics first; draft creation needs autonomy level 2
  - building twice makes one product
  - **publishing waits for approval even at level 3**, then goes ACTIVE
  - live end to end: the token was sent but is stored nowhere
  - **verification catches a store reporting the product as live or as a different product**
  - worker can't rewrite or delete products, and can only insert drafts; unverified drafts can't be published
- **Mutation checks:**
  - publishing without approval → caught
  - no handle dedupe → caught
  - a verifier ignoring the handle **first slipped through**; I added the read-back mismatch test, and it's now caught

## Spec acceptance tests now covered
- ✅ Sandbox Shopify draft verified (on the stand-in store; real dev-store run pending your credentials)

## First vertical slice status
- ✅ research (13) → ✅ margin (14) → ✅ approval for paid actions (07) → ✅ draft built in sandbox + QA (15)
- ⏳ dashboard showing artifact, cost and run history (19)

## Owner actions to go live on a dev store
1. Create a Shopify **development store** (Shopify Partner account).
2. Create an Admin API access token with product read/write scope.
3. Put the token in settings as `SHOPIFY_ADMIN_API_TOKEN`.
4. Change the `shopify.drafts` grant config to `{"mode": "shopify", "store_domain": "<name>.myshopify.com", "dev_store": true}`.

## Next: phase 16 — content and virtual influencer workflow
