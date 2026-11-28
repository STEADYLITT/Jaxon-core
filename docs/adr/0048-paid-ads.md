# ADR-048: Meta and TikTok ads: drafts first, spend only under an approved Launch

**Status:** accepted, 2026-11-28

## Decision
- **Meta Marketing API v27.0**, with a System User token in the `access_token` form field:
  - Test: `GET act_{id}?fields=name,account_status,currency,amount_spent`.
  - Draft: `POST act_{id}/campaigns` (`status=PAUSED`, `special_ad_categories=[]` unless Steady names one), then `POST act_{id}/adsets` (`daily_budget` in cents, `billing_event=IMPRESSIONS`, `targeting.geo_locations.countries`, `status=PAUSED`).
  - Activation: `POST {adset}` with `end_time` set to the Launch's end, then `POST {id}` with `status=ACTIVE`.
- **TikTok Marketing API v1.3**, with an `Access-Token` header:
  - Test: `GET advertiser/info/`.
  - Draft: `POST campaign/create/` (`budget_mode=BUDGET_MODE_DAY`, `operation_status=DISABLE`).
  - Activation: `POST campaign/status/update/`.
- **Tasks:**
  - **`ad_draft`** (the "Ads Planner", Marketing → Ads):
    1. Runs the claims check on the name and copy.
    2. Records a DRAFT action.
    3. Creates the campaign paused and saves an `ad_campaigns` row with `status = draft`. Without keys it saves `mock` and creates nothing.
  - **`ad_activate`** (the "Ads Launcher"):
    1. Refuses unless the run's Launch is **active** for the same business, has `ads: true`, and lists the platform in `ad_platforms`. `routes_launches.Channels` gains `ad_platforms`.
    2. Asks for a SPEND of one day's budget (`channel = ads`). The Launch's budget and daily cap must cover it, so an ad over the daily cap stays off.
    3. Reserves and commits that day as exposure.
    4. Sets Meta's end time and switches the ad on.
    - A MOCK draft can never be activated.
  - **`ad_pause`** (the "Ads Stopper"): always allowed.
- **Kill switch:** every worker launch sweep runs `ads.pause_orphans`. It pauses any live ad whose Launch is no longer active or has passed its end time.
- **Data:** the `ad_campaigns` table has RLS. The app has read access. The worker can insert, and can update only `status`, `launch_id` and `updated_at`.

## Not yet
- Reading actual spend back from Meta/TikTok insights into Launch metrics.
- Uploading creatives and ads. Drafts are campaigns and ad sets, which serve nothing until an ad exists.

Both are next steps. Until then, Launch spend counts each activated day's budget as exposure.
