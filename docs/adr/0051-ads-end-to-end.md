# ADR-051: Ads end to end: creatives, real spend and results, and launch budgets on reported spend

**Status:** accepted, 2026-11-28. Amends ADR-048.

## Decision
- **Creatives** (`ad_creative`, the "Ad Builder"; `POST /ads/{id}/creative`):
  - **Meta:**
    - Image ads use `link_data.picture`, a URL, with `link`, `message`, `name` (the headline) and `call_to_action`.
    - Video ads upload with `POST act_{id}/advideos {file_url}` and poll `status.video_status` until it reads `ready`. They then use `video_data` with a cover `image_url`.
    - Then `POST act_{id}/adcreatives {object_story_spec: {page_id = META_PAGE_ID, ...}}` and `POST act_{id}/ads {adset_id, creative: {creative_id}, status: PAUSED}`.
  - **TikTok** (video only):
    - Uploads use `file/video/ad/upload/` and `file/image/ad/upload/` with `UPLOAD_BY_URL`.
    - Then `ad/create/` with `SINGLE_VIDEO`, `identity_id = TIKTOK_IDENTITY_ID`, the cover `image_ids`, `ad_text`, `call_to_action` and `landing_page_url`.
    - The ad group is now created at draft time with `adgroup/create/`: website traffic, automatic placement, `location_ids` mapped from the country (US, CA, GB, AU), `BUDGET_MODE_DAY`, `SCHEDULE_FROM_NOW`, CLICK/CPC, no bid, `operation_status = DISABLE`.
  - **Checks:**
    - Every link must be https.
    - The copy goes through the claims check.
    - An approved content draft can supply the video and the text.
    - Activation is refused until the campaign has its ad.
- **Switching on and off:** Meta toggles the campaign, the ad set and the ad. TikTok toggles the campaign and the ad group (`adgroup/status/update/`).
- **Insights:**
  - Meta: `GET {campaign}/insights?fields=spend,impressions,clicks,actions&date_preset=maximum`. Spend is a decimal string in the account currency. The result is `link_click`.
  - TikTok: `GET report/integrated/get/` with `BASIC`, `AUCTION`, `AUCTION_CAMPAIGN`, `query_lifetime=true`, and JSON-encoded `dimensions`/`metrics`.
  - The numbers are stored as reported on `ad_campaigns` (migration 0026) and are never estimated.
  - The worker refreshes live ads every 30 minutes, before applying the launch rules. `POST /ads/{id}/insights` refreshes on demand.
- **Launch budgets now use real ad spend.** This changes ADR-048:
  - **Before:** activation reserved one day's budget in the ledger. A full day "spent" in one hour also tripped the spend-spike anomaly rule, which paused the Launch straight away.
  - **Now:**
    - Activation still asks for a SPEND of one day's budget, and the Launch must cover it. Its active ads' daily budgets, added together, must fit the daily cap, and at least one day must fit the budget left.
    - Nothing is booked until the platforms report spend.
    - `launches.metrics.spend_cents` and `covers()` count the ledger plus reported ad spend. So the budget, the cost per sale and "completed when the budget is used up" all use real money.
    - When the budget is used up, the Launch completes and `pause_orphans` stops its ads.
- **KPIs (Marketing):** "Ad spend reported by Meta/TikTok" and "Ad clicks reported".

## Variables
New: `META_PAGE_ID`, `TIKTOK_IDENTITY_ID`, and optionally `TIKTOK_IDENTITY_TYPE`.
