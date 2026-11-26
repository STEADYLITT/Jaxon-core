# Runbook — something is wrong: use the global stop

Use this when Jackson is doing something you didn't expect. Examples: unexpected spend, posts or messages, repeated failures, a suspected leaked key, or anything that feels off. **Stopping is always safe. Resuming is a decision.**

1. **Stop.** Use the red **Global stop** button in the console (`/console`), or call:
   ```bash
   curl -X POST https://<api>/tenants/<tenant-id>/stop \
     -H "Authorization: Bearer <owner token>" -H "Content-Type: application/json" \
     -d '{"reason": "what you saw"}'
   ```
   Only the owner can do this. The stop:
   - cancels every unfinished run
   - moves scheduled posts back to approved
   - cancels outreach campaigns that are waiting or sending
   - closes watched browser sessions

   The database then refuses any new run or task start until you resume.
2. **Reconcile.** The response (also kept in the audit log as `tenant.global_stop`) lists what may already have happened:
   - `runs_that_were_mid_task`: check their run timelines
   - `outreach_unconfirmed`: messages handed to the sender but not confirmed. Check the provider before re-sending anything.
   - `outreach_sent_last_24h` and `content_published_last_24h`: what went out recently
   - `products_live` and `sites_live`: these **stay up**. Taking them down is a separate production change: unpublish the product, or roll back the site in the console.
   - `money_held_cents`: budget still reserved. Nothing more can be spent while stopped.
3. **If a key may have leaked:**
   - rotate it at the provider and update the environment
   - restart the API and workers
   - check the tool-call and model-usage logs for the time window
4. **Find the cause.** The dashboard alerts, failed tasks and the audit log are the starting points.
5. **Resume** only once the cause is understood: the console's **Resume work** button, or `POST /tenants/<id>/resume`. Canceled runs stay canceled, so start the ones you still want again, on purpose.
