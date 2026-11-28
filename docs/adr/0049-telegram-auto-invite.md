# ADR-049: Telegram auto-invite for paying Whop members

**Status:** accepted, 2026-11-28

## Decision
The Telegram Bot API can't add a user to a group on its own. The supported automatic path is a **join-request link**:

1. `POST /tenants/{t}/community/telegram/setup` does two things:
   - calls `setWebhook(url = PUBLIC_BASE_URL/webhooks/telegram/{tenant}, secret_token = TELEGRAM_WEBHOOK_SECRET, allowed_updates = ["chat_join_request"])`;
   - creates a reusable `createChatInviteLink(creates_join_request = true)`.

   It returns that link and records an audit entry.
2. A person taps the link. Telegram POSTs a `chat_join_request` to `/webhooks/telegram/{tenant}`. Jackson then:
   - refuses the call (401) unless the `X-Telegram-Bot-Api-Secret-Token` header matches the secret, checked in constant time;
   - ignores requests for any other chat.
3. Jackson looks up the requester's Telegram id among `whop_members` with access (active, trialing or completed):
   - **match:** `approveChatJoinRequest`, `telegram_synced = joined`, audited;
   - **no match:** `declineChatJoinRequest`, plus one message to `user_chat_id` (allowed while the request is open) explaining how to link Telegram in Whop.
4. Removal is unchanged. `community_sync` removes members who lose access (ban + unban), and they can request again after paying.

The one-use 48-hour invite link (`/community/telegram/invite`) stays as a manual fallback.

## Variables
- `TELEGRAM_WEBHOOK_SECRET`: new, optional.
- Also needs `PUBLIC_BASE_URL` (already set for the app), plus the existing `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.
