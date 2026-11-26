# Runbook — the first real pilot

Goal (spec vol. 06, first vertical slice): **"Find a product opportunity and build a draft store."** This means research with real sources, margin maths, the owner's approval, a **draft** product on a Shopify **development** store, QA, and the dashboard showing the artifact, cost and run history. There's no public launch.

The whole flow already passes end to end with labeled stand-ins (`services/api/tests/test_pilot.py`). This runbook is the same flow with real accounts.

## What the owner provides
| Item | Where it goes |
|---|---|
| Auth provider (Clerk) issuer, JWKS URL, console origin | `AUTH_ISSUER`, `AUTH_JWKS_URL`, `AUTH_AUTHORIZED_PARTIES` |
| Claude API key | `ANTHROPIC_API_KEY` (keep `MODEL_MONTHLY_BUDGET_USD` small, e.g. 20) |
| Shopify **development** store + Admin API token with product read/write | `SHOPIFY_ADMIN_API_TOKEN`; then a `shopify.drafts` grant with `{"mode":"shopify","store_domain":"<name>.myshopify.com","dev_store":true}` |
| 1–3 public product pages to research | added to the `web.fetch` grant's `allowed_domains` |
| Hosting for the API + worker + Postgres 16 | your choice. `python -m jackson_api.preflight` must pass with `APP_ENV=production`. |

## Steps
1. Deploy with `APP_ENV=production`. The API refuses to start if the preflight fails.
2. Run the migrations as the schema owner, then take a first backup (`docs/runbooks/backup-restore.md`).
3. In the console (`/console`):
   - check that **Connectors** shows Claude and Shopify as **LIVE** and everything else as MOCK
   - set the business to autonomy level 1
4. Research: `POST /tenants/{id}/opportunities/research` with the allowed URLs.
   - Check that every signal on the new opportunity cites one of those URLs.
5. Economics: `POST /opportunities/{id}/economics` with your real costs.
   - Check that the margin and break-even CAC make sense.
6. Build: `POST /opportunities/{id}/build-store`. The run waits for your approval (sandbox change at level 1). Approve it in the console.
7. Verify by hand in the Shopify admin:
   - the product is a **Draft**, with the right title and price
   - the copy contains no invented claims
8. Dashboard: the QA report artifact, the model cost and both runs are visible.
9. Daily report: `POST /reports/daily`. Read it.
10. Practise the stop: press **Global stop**, check that nothing new starts, then resume.

## Pass criteria
- Every number in the dashboard can be traced to a run, a ledger entry or a model-usage row.
- No product was published and no money was spent without an approval in the inbox.
- Backup, restore and verification pass on a copy of the pilot database.

Any failure is a bug report against the phase that owns it. Don't widen permissions to work around a failure.
