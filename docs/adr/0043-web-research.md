# ADR-043: Web research via Apify (trends, leads, product research)

**Status:** accepted, 2026-11-28

## Decision
- **Apify call:** `POST /v2/acts/{actor}/run-sync-get-dataset-items` with `Authorization: Bearer APIFY_TOKEN`, `timeout=280`, and `maxTotalChargeUsd` set to the approved cap.
- **Test:** `GET /v2/users/me`.
- **Actors:**

  | Kind | Actor | Cost per 1,000 |
  | --- | --- | --- |
  | `search` | `apify~google-search-scraper` (`queries`, `maxPagesPerQuery`, `countryCode`) | $1.80 per result page |
  | `trends` | `apify~google-trends-scraper` (`searchTerms`, `timeRange`, `geo`) | $3.00 per result |
  | `places` | `compass~crawler-google-places` (`searchStringsArray`, `locationQuery`, `maxCrawledPlacesPerSearch`) | $1.50 per place |

- **Task `web_research`** (the "Web Researcher" on the R&D board):
  1. Validates the input and builds the cap: per-1k price × items, minimum $0.05, at most 200 items.
  2. Asks for a SPEND (`always_ask`): owner approval or a Launch budget.
  3. Reserves the budget, runs the Actor, then settles the cap (the most Apify can charge).
  4. Saves the normalized results as a JSON artifact (`kind = research`). Rows without a source URL are dropped.
- **Places become leads:**
  - Each place becomes a `contacts` row: `source_type = public_business_listing`, `lawful_basis = legitimate_interest`, `source_ref` = the Google Maps URL.
  - Only the business phone is kept, no personal email.
  - Deduplicated by the existing CRM key, so a repeated search adds nothing twice.
  - Migration 0023 lets the worker INSERT contacts. It still can't edit or delete them.
- **Lines:** R&D, Sales & Lead Gen and E-commerce show the Web research connector.

## Without a key
- `POST /research/web` answers 409 and names `APIFY_TOKEN`.
- Research from links in a command still works as before.
