# ADR-036: UGC video clips through fal.ai

**Status:** accepted, 2026-11-27

## Decision
Short product/UGC clips use fal.ai's queue API with `fal-ai/veo3.1/fast` (override with `FAL_VIDEO_MODEL` plus `FAL_VIDEO_CENTS_PER_SECOND`). Without `FAL_KEY` this is a labeled mock that makes no postable video (`mock://` URL, $0).

## Cost and approval
- The estimate is computed from fal's published per-second price before anything happens. It's returned by:
  - `GET /tenants/{t}/video/estimate`
  - `POST /tenants/{t}/content/{id}/video`
  - the console button
- The `content_video` task authorizes `Action(SPEND, amount=estimate, channel="ugc_video", always_ask=True)`.
  - The new `always_ask` flag makes the policy engine require the owner's approval whatever the autonomy level.
  - An active Launch approval whose budget and daily cap fit counts as that approval.
- The estimate is reserved in the ledger. After the clip is delivered it's committed: fal bills per second, so the actual equals the estimate. A mock commits $0.

## Safety
- The submitted fal request is recorded (`video.submitted` run event). A retry resumes polling the same request instead of paying again; it skips re-authorizing because the reservation is idempotent.
- The clip is attached to a **new** content item in review, with `rights: generated` and `kind: video`. Posting it needs its own approval, and the approved original is never modified; the worker can't change `assets` anyway.
- Upload-Post receives `is_ai_generated=true` for it.
