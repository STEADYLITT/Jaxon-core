# ADR-044: Discord and Telegram access from Whop, and approved announcements

**Status:** accepted, 2026-11-28

## Decision
- **Discord** (`https://discord.com/api/v10`, `Authorization: Bot …`, `User-Agent: DiscordBot (…)`):
  - Test: `GET /guilds/{id}?with_counts=true`. It also confirms `DISCORD_MEMBER_ROLE_ID` exists.
  - Role changes: `PUT` and `DELETE /guilds/{g}/members/{u}/roles/{r}`, each with an `X-Audit-Log-Reason` naming the Whop membership and its status. Removing the role from someone who already left the server (404) counts as done.
  - Announcements: `POST /channels/{id}/messages`.
- **Telegram** (`https://api.telegram.org/bot<token>/<method>`):
  - Test: `getMe` and `getChat`.
  - Removal: `banChatMember` then `unbanChatMember(only_if_banned)`, so the member is out but can rejoin later.
  - Invites: `createChatInviteLink(member_limit=1, expire_date=+48h)`.
  - Announcements: `sendMessage`.
- **`community_sync`** (the "Role Syncer", Community → Role Sync):
  - Access = Whop status in active, trialing or completed.
  - It uses `whop_members.discord_synced` and `telegram_synced` (migration 0024) to touch only members whose access changed. A re-sync makes 0 calls.
  - It runs as a follow-up after every `whop_sync` when Discord or Telegram is connected.
  - Role sync is routine work on real payment records, so it needs no approval. Each change is in Discord's audit log, and the run's events record the counts.
- **`community_announce`** (the "Announcer"):
  - The claims check runs first.
  - Then it is a `PUBLIC_POST` with `channel = social` and `target = discord|telegram`.
  - The owner approves it, or a Launch whose social accounts include that platform covers it.
- **Invites:** only for a membership Whop says has access. Audited.
- **KPIs (Community):**
  - Paying members
  - Members with Discord linked
  - Discord roles granted

## Without keys
`/community/sync` answers 409 and names the variables. Announcements fail with the missing variable names. Nothing is invented.
