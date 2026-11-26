# Phase 17 — Website factory

Date: 2026-11-26 · Decision: ADR-027 in [PHASE_01_GAP_REPORT.md](PHASE_01_GAP_REPORT.md#3-architecture-decisions-adrs).

## Plain English
- **Jackson can now build a simple business website** (for CSW Trades, for example) from your answers: business name, tagline, about text, services, contact email and brand colours. Every site gets home, about, services, contact and privacy pages.
- **Every build is tested before you see it.** That covers the basics search engines and screen readers need, readable colours, no broken links and a privacy notice linked from every page. Each build also opens in a real browser on a desktop-size and a phone-size screen. Every build keeps a phone screenshot.
- **A site that fails a test can't go live.** The database enforces this, not just the app.
- **Going live always needs your approval.** First Jackson checks you own the domain: you add a DNS record it gives you. Then the launch waits in your approvals inbox.
- **One-click rollback** puts the previous live version back.
- **Hosting is a clearly labeled stand-in for now.** Nothing is actually put on the internet until you pick a host (e.g. Vercel or Cloudflare Pages) and give me access.

## What was built
| Piece | File |
|---|---|
| Template builder (static HTML, no scripts, owner text escaped), static + accessibility checks, contrast maths, Playwright preview, DNS TXT verifier (DNS-over-HTTPS) + labeled mock, MOCK host, `site_build` and `site_launch` handlers | `jackson_api/sites.py` |
| `sites` and immutable `site_versions` (RLS; trigger: only tested versions of the same site can go live; versions can't be edited) | `models.py`, `migrations/versions/0014_sites.py` |
| API: create/list/get sites, update requirements, build, preview a built page (strict CSP), launch (returns the DNS record), rollback | `jackson_api/routes_sites.py` |

## Tests
- **API (`services/api`): 348 passed** against real PostgreSQL 16 (33 new in `test_sites.py`).
- **Build + static checks:**
  - a template build passes every check
  - 11 kinds of broken HTML are caught
  - low-contrast colours and invented claims fail
  - `<script>` in your text comes out as harmless text
- **Real Chromium preview:**
  - a good site passes and gets a screenshot
  - sideways scrolling on a phone, a hidden heading, a broken nav link and a request to an outside server are all caught
- **Flow:**
  - **"website preview passes tests"** end to end (spec acceptance)
  - a failing version is kept but can't launch
  - a launch waits for approval (nothing live before), then goes live on the mock host
  - a second version replaces the first, and rollback brings the first back
  - an unverified domain stops the launch before any approval is asked
  - the DNS check reads the TXT record
  - non-public domains are rejected
  - tenant isolation holds
- **Database:**
  - the worker can't put an untested version live, can't mark a site live without one, can't change the domain and can't edit versions
  - not even the schema owner can edit a version
- **Mutation checks (12/12 caught):**
  - launch without approval
  - worker launch of an untested version
  - API launch of an untested version
  - no HTML escaping
  - domain check ignored
  - browser preview ignored
  - DB trigger disabled
  - privacy-link check removed
  - rollback doing nothing
  - phone-scroll check removed
  - Two of these first slipped through. I added tests, and both are now caught.

## Spec acceptance tests now covered
- ✅ Website preview passes tests

## Known limit
The worker saves each version together with its pass/fail result, so the database can't prove the tests really ran. Your launch approval is the human check.

## Owner actions to launch for real
1. Pick a host (Vercel or Cloudflare Pages are both fine for static sites) and create an account.
2. Give me an API token for it (`SITE_HOSTING_TOKEN`). I'll check its current docs and connect it behind the existing approval gate.
3. Set `SITE_DOMAIN_VERIFY=dns` so domain ownership is checked for real.

## Next: phase 18 — CRM and lead workflows
