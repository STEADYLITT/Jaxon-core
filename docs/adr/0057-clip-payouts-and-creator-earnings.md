# ADR-057: Clip payouts (and creator earnings) from real statements

## Context
The Clips line's **Views** and **Payouts** numbers said "needs CLIP_PAYOUTS_SOURCE". Clip money comes from campaign programmes:
- Whop content rewards / bounties (Whop's bounty API is beta and is written from the poster's side, not the clipper's)
- agency payout sheets
- platform creator funds

There is no single API for these. What every one of them does provide is a statement: a CSV export or a JSON feed of payouts, each with the clip, its views and the amount.

## Decision
- **Source variable.** `CLIP_PAYOUTS_SOURCE` says where the numbers come from:
  - an **https feed** (optional Bearer `CLIP_PAYOUTS_TOKEN`) that returns a list of payouts, or `{"payouts"|"earnings"|"data"|"items": [...]}`;
  - `import`: statements are uploaded with `POST /tenants/{t}/payouts/clips/import` (CSV text or JSON rows).

  An upload works even with no variable set.
- **Fields.** Each payout carries a reference, the clip URL, platform, campaign, views, amount (or amount_cents), currency and paid_at. Column names are matched loosely ("Clip URL", "Payout", "Date"…).
  - A row without an amount or a clip is **skipped, never guessed**.
  - A row without an id gets a stable hash, so re-importing the same statement never double counts.
- **Recording.** New payouts go into `payout_lines` (migration 0027).
  - The table is append-only: worker INSERT, app SELECT, RLS on.
  - Unique per (tenant, line, reference).
  - Each new payout with money also becomes one actual `revenue_entries` row (source `import`, reference `clips:<ref>`). It lands on the Clips business when there is exactly one, otherwise on none.
- **Tasks and traces.** Pull (`POST /payouts/clips/pull`) and import both run as a `clip_payouts_import` task. In the City that is the "Payout Recorder" on the Clips posting floor, so every import is traced.
- **KPIs.** "Views paid on this month" and "Payouts this month" are summed from `payout_lines`. They stay empty with "needs CLIP_PAYOUTS_SOURCE" until a real payout is recorded.
- **Connections Test.** Reads the feed and reports how many payouts are usable and their total. It records nothing.
- **Creator earnings.** The same module serves them, with `CREATOR_EARNINGS_SOURCE` / `CREATOR_EARNINGS_TOKEN` and line `creators` (next PR).

## What Steady does
Either:
- set `CLIP_PAYOUTS_SOURCE=import` and upload the campaign's CSV statement, or
- point it at the https feed your clip programme or agency gives you, plus `CLIP_PAYOUTS_TOKEN` if the feed needs one.

## Creator earnings (follow-up PR)
- **Source.** `CREATOR_EARNINGS_SOURCE` (an https feed or `import`), with optional `CREATOR_EARNINGS_TOKEN`. Same rules as clip payouts.
- **Imports.** Statements are uploaded at `POST /payouts/creators/import` or pulled with `/payouts/creators/pull`. Each row is one creator's payout: creator name, platform (Fanvue, OnlyFans, …), net amount, date, transaction id.
- **Task.** `creator_earnings_import`, done by the "Earnings Recorder" on the Creator Agency's Earnings floor. The Creator Agency building is therefore staffed; the City tests now use Agency as the unstaffed example.
- **KPIs.** "Creator earnings this month" (sum) and "Creators paid this month" (distinct creators with money). Both come only from recorded statements.
