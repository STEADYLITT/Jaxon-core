# Phase 18 — CRM and compliant outreach

Date: 2026-11-26 · Decision: ADR-028 in [PHASE_01_GAP_REPORT.md](PHASE_01_GAP_REPORT.md#3-architecture-decisions-adrs).

## Plain English
- **Jackson now keeps a customer and lead list.** Every lead must say where it came from (inbound, referral, existing customer, event or public business listing) and why you're allowed to contact them. Bought lists aren't accepted.
- **Duplicates are merged on import, and every lead gets a score you can read.** The score shows where it came from, whether you can reach them, and whether they match your ideal customer. Stages run from new → contacted → replied → meeting → proposal → won/lost.
- **Campaigns follow the email and texting rules.** Every email has your name, your mailing address and an unsubscribe link. Fake "RE:" subject lines and invented claims are blocked. Texts and calls need the person's recorded permission, include "Reply STOP", and only go out 8am–9pm their time.
- **You see every message before anything goes out.** Jackson prepares the campaign, you review it, and your "send" click is the approval. There's a daily cap, and you can cancel mid-send.
- **Unsubscribes are permanent.** Someone who opts out, even after you approved the campaign, never gets another message. Deleting a contact keeps them blocked without keeping their address. The database enforces this, not just the app.
- **Sending is a clearly labeled stand-in for now.** Real sending needs an email provider (and a texting provider for SMS) and its key.

## What was built
| Piece | File |
|---|---|
| Contacts, opt-outs (hashed), campaigns and messages; DB trigger guarding every send; worker limits | `models.py`, `migrations/versions/0015_crm.py` |
| Normalising, dedupe, lead score, signed unsubscribe links, compliance review, quiet hours, `outreach_prepare` and `outreach_send` handlers, MOCK sender | `jackson_api/crm.py` |
| API endpoints | `jackson_api/routes_crm.py` |

The API endpoints:
- contacts:
  - create, bulk import, list and update stage
  - record SMS/call consent with evidence
  - delete (the contact is suppressed first)
- opt-outs: add manually
- campaigns: create, prepare, view messages, approve-and-send, cancel
- public: `GET /u/{token}` (unsubscribe page, changes nothing) and `POST /u/{token}` (opts out)

## Tests
- **API (`services/api`): 379 passed** against real PostgreSQL 16, 31 of them new in `test_crm.py`.
- **Rules:**
  - email and phone normalising, dedupe and a transparent score
  - 9 kinds of non-compliant message are blocked (opted out, no postal address, no unsubscribe link, fake "RE:", invented claims, bad sender, SMS without consent, SMS without STOP, call without consent)
  - quiet hours use the recipient's time zone
  - unsubscribe links can't be forged
- **Flow:**
  - leads need a permitted source and a lawful basis
  - import dedupes, scores and reports rejected rows
  - stage changes are audited
  - full prepare → review → approve → send runs; contacts move to "contacted"
  - an unprepared campaign can't be approved, and a non-compliant one is blocked at review
- **Opt-outs:**
  - the unsubscribe link works without logging in, and its GET changes nothing
  - **someone who opts out after approval is skipped**
  - suppressed contacts are never queued
  - deleted, then re-imported contacts stay blocked
- **Sending limits:**
  - SMS needs recorded consent and waits outside 8am–9pm
  - the daily limit holds
  - cancelling mid-send stops cleanly
  - re-running a send never sends twice
  - a send task without an approval waits for one
- **Database guarantees:**
  - the worker can't claim a message for an unapproved campaign, skip the claim step, approve a campaign, send to an opted-out address, text without consent, insert an already-sent message or delete an opt-out
- **Tenant isolation:** another tenant can't see or change a contact.
- **Bug found and fixed by a test:** cancelling while a message was with the provider made the database refuse to record a send that had really happened. Messages are now claimed (`sending`) before the provider call. Only the claim is guarded, so what's recorded always matches what went out.
- **Mutation checks:**
  - 14 of 15 caught.
  - The 15th removes the in-loop cancel check. The database guard now gives the same result, so it's an equivalent mutant, not a gap.

## Spec items covered (vol. 03)
- Lawful leads ✅
- Permitted sources ✅
- Dedupe and score ✅
- Respect opt-outs and email/calling rules ✅
- CRM stages ✅
- Personalized drafts: templates with merge fields ✅

**Deferred:**
- AI-written outreach copy (uses the phase 08 model gateway once a key exists)
- Proposal documents and booking links (need a calendar/e-sign provider)
- Reply handling (needs a real inbox)

## Owner actions
1. Set `UNSUBSCRIBE_SECRET` (32+ random characters) and `PUBLIC_BASE_URL` (where the API is reachable). Outreach preparation refuses to run without them.
2. To send for real, pick an email provider (and an SMS provider if you'll text) and give me its API key. I'll check its current docs and connect it behind the same approval and opt-out guards.
3. Have a lawyer glance at your outreach plan. These checks are guard rails, not legal advice.

## Next: phase 19 — portfolio dashboard and reporting
