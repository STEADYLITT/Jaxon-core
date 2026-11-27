# ADR-035: Email sending through Resend

**Status:** accepted, 2026-11-27

## Decision
Outreach email goes through Resend once `RESEND_API_KEY` and `RESEND_FROM` are set. Until then it stays the labeled mock. SMS and calls stay mock.

## Sending
- `POST https://api.resend.com/emails` with a Bearer key.
- Each message gets `Idempotency-Key: msg-<message id>`, so a retry after a timeout never sends twice.
- Each message carries `List-Unsubscribe: <signed /u/ link>` and `List-Unsubscribe-Post: List-Unsubscribe=One-Click` (RFC 8058). The existing `POST /u/{token}` accepts that POST.
- The campaign's sender must be on the domain of `RESEND_FROM`. Otherwise the send task fails before any call.
- The approval gate is unchanged:
  - the policy engine
  - the `ck_campaign_needs_approval` trigger
  - the per-message `outreach_blocked` guard
- Launch campaigns record a `send` launch signal per message.

## Webhook
`POST /webhooks/resend/{tenant_id}` is a public route and needs no sign-in.
- **Auth:** the Svix signature (`RESEND_WEBHOOK_SECRET`) is the credential. Unsigned, forged or stale (>5 min) calls get 401. A missing secret gets 503.
- **Events handled:** `email.bounced` and `email.complained`.
  - The recipient (looked up through the stored Resend email id, not the payload's address) gets a permanent suppression.
  - When the campaign belongs to a launch, a `bounce` or `complaint` launch signal is recorded, deduplicated by email id. The launch sweep then auto-pauses at the existing 5% bounce / 0.3% complaint thresholds.
- **Other events:** acknowledged and ignored.
