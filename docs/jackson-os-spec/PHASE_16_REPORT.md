# Phase 16 — Content and virtual influencer workflow

Date: 2026-11-26 · Decision: ADR-026 in [PHASE_01_GAP_REPORT.md](PHASE_01_GAP_REPORT.md#3-architecture-decisions-adrs).

## Plain English
- **Jackson can now write social posts, captions, scripts and emails** in a brand voice or as an original AI character (a "virtual influencer").
- **Every draft is checked automatically.** The check looks for:
  - invented claims ("guaranteed profit", fake star ratings)
  - a missing "AI-generated" label
  - pretending to be a real person
  - a sponsored post without `#ad`
  - posts that are too long or have too many hashtags
  - images or music without cleared rights
- **Nothing gets posted until you approve it.** The database itself enforces this, not just the app. Editing a post clears its approval. Rejecting a scheduled post cancels it.
- **Scheduling is your "yes, post this."** Jackson then waits for the time you picked and posts it.
- **Posting is a clearly labeled stand-in for now.** Real posting needs a social posting account (e.g. Upload-Post) and its key.

## What was built
| Piece | File |
|---|---|
| Personas (brand voice or fictional virtual influencer, disclosure, do-not-impersonate list) and content items | `models.py`, `migrations/versions/0013_content.py` |
| Compliance review (claims, AI disclosure, impersonation, likeness consent, ad disclosure, platform limits, asset rights) | `jackson_api/content.py` |
| `content_generate` handler: drafts in the persona's voice through `ctx.call_model`, disclosure always added, each item reviewed, never approved | `jackson_api/content.py` |
| `content_publish` handler: re-checks approval, policy `public_post`, MOCK post id, `content.published` event | `jackson_api/content.py`, `worker.py` |
| API endpoints | `jackson_api/routes_content.py` |

The API endpoints:
- personas: `POST /personas` and `GET /personas`
- content:
  - `POST /content`, `GET /content`, `GET /content/{id}`
  - `PATCH /content/{id}` (re-reviews and clears approval)
  - `POST /content/{id}/approve` and `POST /content/{id}/reject`
  - `POST /content/{id}/schedule`
  - `POST /content/generate`

## Tests
- **API (`services/api`): 315 passed** against real PostgreSQL 16, 22 of them new in `test_content.py`.
- **Review:** a clean, disclosed post passes. 10 kinds of problems are caught.
- **Personas:** a virtual influencer must be fictional, enforced by both the API and a database CHECK.
- **Approval:**
  - failing content can't be approved
  - an edit clears approval
  - viewers can't approve
  - unapproved content can't be scheduled
- **Scheduling:**
  - the post waits until its time, then publishes
  - the publish is labeled mock and recorded as an event
  - the schedule click shows up as an approved `public_post` approval
- **Cancelling:** a post rejected after scheduling never goes out (the run fails with "not approved").
- **Generation:**
  - a faked Claude reply creates drafts with the disclosure added; a draft with bad claims stays `draft`; none are approved
  - with no model key, generation is labeled mock and creates nothing
- **Tenant isolation:** another tenant gets 404.
- **Mutation checks (all caught):**
  - publish handler skipping the approval re-check
  - approve skipping the review
  - generation not adding the disclosure
  - edit keeping approval
  - impersonation check removed
  - DB CHECK removed
  - worker insert policy removed
  - worker column-limited UPDATE widened

## Spec acceptance tests now covered
- ✅ Content remains draft until approved (enforced in the database: the worker role can't approve, schedule or publish content, even with direct SQL)

## Known limit
The worker can change the status of content that's already approved (for example, mark it published) without posting it. Real posts only go out through the connector after the policy check.

## Owner actions to post for real
1. Pick a posting provider (e.g. Upload-Post) and create an account.
2. Put its API key in settings. Then I'll check its current docs and wire the real connector behind the existing approval gate.

## Next: phase 17 — website factory
