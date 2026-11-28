# ADR-046: Real-estate data via BatchData (comps and skip tracing, research only)

**Status:** accepted, 2026-11-28

## Decision
**BatchData, not PropStream:** PropStream has no public self-serve developer API. BatchData documents comps, property lookup and skip trace endpoints behind one Bearer token.

**API:** `https://api.batchdata.com/api/v1`.
- `POST /property/search` with `searchCriteria.compAddress` returns comps. Options: distance in miles (default 1), bedrooms, area and year built on, `take` up to 25.
- `POST /property/skip-trace` with `requests[{propertyAddress}]` returns `persons[]`: name, emails, phones with a DNC flag, plus `dnc` and `litigator`. `matchCount` is what's billed.

**Prices:** BatchData prices by plan, so `BATCHDATA_CENTS_PER_RECORD` is required. Every lookup shows its cost first.

**Tasks:**
- **`property_comps`** (the "Comps Researcher" on Real Estate → Comps):
  - SPEND approval, or a Launch's budget.
  - The JSON artifact `type = property_comps` holds the comps, the median sale price and the median price per square foot. Only comps that report those numbers count.
  - The settled cost is the number of records returned × the price, capped at the estimate.
- **`skip_trace`** (the "Skip Tracer" on Real Estate → Lead Lists), one property at a time:
  - A `SENSITIVE_DATA` approval (personal data), then the cost's SPEND approval.
  - The artifact has `kind = sensitive` and `meta.personal_data = true`.
  - DNC phones and litigator matches are marked and excluded from "phones ok to call".

**Research only:** nothing here contacts anyone, makes an offer or sends a contract. That still needs Steady (Legal & Admin drafts only).

**KPI (Real Estate):** "Comps searches", counted from real artifacts.

## Without a key
`/realestate/*` answers 409 and names the variables.
