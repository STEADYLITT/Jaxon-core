# ADR-038: Connections panel with real, harmless tests

**Status:** accepted, 2026-11-27

## API
- `GET /tenants/{t}/admin/connectors`, owner or admin only. For each outside service it returns:
  - `mode`: LIVE, MOCK or ERROR
  - the **names** of missing variables
  - optional variables not set
  - the last test result
- `POST /tenants/{t}/admin/connectors/{id}/test` makes one real call. Each test is audited (`connector.tested`).

## Modes
- **MOCK:** the service isn't built, or its variables aren't set.
- **ERROR:** the variables are set, but the last test failed.
- **LIVE:** the variables are set, and the last test passed or no test has run yet.

## Tests: harmless by design
| Service | Call |
|---|---|
| Claude | `GET https://api.anthropic.com/v1/models` (free) |
| Shopify | client-credentials token, then a read-only `{ shop { name myshopifyDomain plan } }` query |
| Upload-Post | `GET /api/uploadposts/me` (key check, plan) |
| Resend | one email to the signed-in owner's own address, from `RESEND_FROM`, with an idempotency key |
| fal.ai | `GET /v1/account/billing?expand=credits` |

For fal.ai, a 403 means a valid key without billing permission. That still counts as working, since generation uses a different permission.

## Secrets
- Values are never returned, stored or logged. Only names, and whether each is set.
- Every provider message is scrubbed of all configured secret values before it's shown.
- Jackson never sets Railway variables; the owner pastes them.
