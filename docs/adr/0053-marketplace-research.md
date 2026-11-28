# ADR-053: Amazon (SP-API) and eBay (Browse) product research

**Status:** accepted, 2026-11-28

## Decision
**Amazon Selling Partner API (North America, US marketplace):**
- Auth is a Login with Amazon refresh-token grant, then the `x-amz-access-token` header. Requests no longer need AWS signing.
- Test: `GET /sellers/v1/marketplaceParticipations`.
- `GET /catalog/2022-04-01/items` with `keywords`, `includedData=summaries,salesRanks`.
- `GET /products/pricing/v0/items/{asin}/offers?ItemCondition=New` for the Buy Box and lowest landed prices and the offer count.
- `POST /products/fees/v0/items/{asin}/feesEstimate` with `IsAmazonFulfilled: true` at that price, for the FBA fee total.

**eBay Buy Browse:**
- Auth is an app token (client-credentials, basic auth, `api_scope`).
- `GET /buy/browse/v1/item_summary/search` with `X-EBAY-C-MARKETPLACE-ID: EBAY_US`.
- The result summarizes **active-listing asking prices**: median, low, high and total. Browse has no sold prices, and the report says so.

**Task `marketplace_research`** ("Marketplace Researcher", E-commerce → FBA & eBay):
- Takes at most 5 keywords. The top 5 Amazon matches per keyword get pricing and fees.
- Margin = price − FBA fees − Steady's landed cost, computed only when all three are real numbers. Missing values stay `null`.
- The report is saved as a JSON artifact (`marketplace_research`), and the best margins come first.
- Read-only: nothing is listed, bought or messaged.

**KPI (E-commerce):** "Amazon/eBay research runs".

## Variables
- `AMAZON_SP_CLIENT_ID`, `AMAZON_SP_CLIENT_SECRET`, `AMAZON_SP_REFRESH_TOKEN`
- `EBAY_CLIENT_ID`, `EBAY_CLIENT_SECRET`

These replace the placeholder names `AMAZON_SP_API_KEY` and `EBAY_API_KEY`.
