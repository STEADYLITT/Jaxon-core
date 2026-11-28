# ADR-058: Follower analytics from the platforms' own counts

## Context
The AI Influencers line's "Followers gained" said "needs PLATFORM_ANALYTICS". Growth must come from real readings, never a projection.

## Decision
`PLATFORM_ANALYTICS` lists the platforms to read: `youtube,instagram,tiktok`. Each platform has its own variables.

| Platform | Variables | Call | Reads |
|---|---|---|---|
| YouTube | `YOUTUBE_API_KEY` (already used for clip scouting) + `YOUTUBE_CHANNEL_IDS` | `channels.list` (`part=snippet,statistics`), 1 quota unit | `subscriberCount`. A channel that hides its count is skipped. |
| Instagram | `META_ACCESS_TOKEN` (already used for ads) + `INSTAGRAM_USER_IDS` (professional accounts linked to a Facebook Page) | Graph API `/{ig-user-id}?fields=username,followers_count` | `followers_count` |
| TikTok | `TIKTOK_DISPLAY_TOKEN` (a user token with the `user.info.stats` scope, different from the ads token) | `GET open.tiktokapis.com/v2/user/info/?fields=open_id,username,follower_count` | `follower_count` |

### How readings are stored
- **Table.** `follower_snapshots` (migration 0028) keeps one row per account per day.
  - A later reading the same day replaces that day's count.
  - History is never deleted.
  - The worker can INSERT, and can UPDATE only `followers` and `handle`.
  - The app can only SELECT. RLS is on.
- **Starting.** `POST /tenants/{t}/analytics/followers/snapshot` takes the first reading. It runs as a `follower_snapshot` task, done by the "Growth Tracker" on the AI Influencers growth floor.
- **Daily reading.** From then on, the worker's sweep reads once a day for every tenant that has started tracking.
- **Failures.** One platform failing never hides the others; the task lists what was not read and why.

### What it shows
- **KPIs.**
  - "Followers now" is the sum of each account's latest reading.
  - "Followers gained (30 days)" is the latest reading minus the first one in the window. It covers only accounts with readings on two different days. Until then it says when the first reading was taken.
  - Nothing is shown until a real reading exists.
- **Read API.** `GET /tenants/{t}/analytics/followers?days=30` gives each account's first and latest reading.
- **Connections Test.** Reads today's counts without recording them.
