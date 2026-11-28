# ADR-052: Clip scouting on YouTube and Twitch

**Status:** accepted, 2026-11-28

## Decision
**YouTube Data API v3**, authenticated with an API key:
- `search?part=snippet&type=video&order=viewCount&publishedAfter=…` finds videos by query and/or channel. This costs 100 quota units.
- Then `videos?part=snippet,statistics,contentDetails,status` fetches views, likes, comments, the ISO-8601 duration and the license. This costs 1 unit.
- The test call is `videos?part=id`, which costs 1 unit.

**Twitch Helix**, using an app access token from the client-credentials flow (`id.twitch.tv/oauth2/token`), cached until it expires:
- `users?login=` resolves channel names, then `clips?broadcaster_id=&started_at=` returns views, duration, VOD offset and the clipper.
- The test call is `games/top?first=1`.

**Rights:** each pick is classed as one of:
- `allowed`: the creator is on Steady's `CLIP_ALLOWED_CREATORS` list.
- `allowed (Creative Commons: credit the creator)`: the YouTube video carries that license.
- `ask the creator first`: everything else.

Allowed picks rank first, then picks are ordered by views.

**Task `clip_scout`** ("Clip Scout", Clips → Scouting): read-only. It saves the picks as a JSON artifact (`type = clip_candidates`) with their counts. It never downloads, cuts or posts anything; posting still goes through content approval or a Launch.

**KPI (Clips):** "Clip scouting runs".

The Clips building is now staffed by its built-in agent. The tests that use an unstaffed building now use Legal instead.

## Variables
`YOUTUBE_API_KEY`, `TWITCH_CLIENT_ID`, `TWITCH_CLIENT_SECRET`, plus the optional `CLIP_ALLOWED_CREATORS`.
