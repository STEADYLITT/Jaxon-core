# Phase 11 — Visual browser session

Date: 2026-11-25 · Decision: ADR-021 in [PHASE_01_GAP_REPORT.md](PHASE_01_GAP_REPORT.md#3-architecture-decisions-adrs).

## Plain English
- **You can watch Jackson browse.** When an agent uses the browser, every step (open a page, click, type) saves a screenshot and updates the session with the current page and title. The live feed announces each screenshot, so the dashboard can show it as it happens.
- **Private stuff is hidden:** password boxes, card and SSN fields, and anything tagged sensitive are blacked out in screenshots and removed from the text the agent reads.
- **It stays on approved sites.** The browser only loads sites you put on its grant. Even hidden trackers and scripts from other sites are blocked.
- **You're in control:** pause (the agent waits), take over (the agent stops acting and you see the latest screenshot), resume, or terminate (the agent stops for good). The agent can't override your controls; the database blocks it.
- **Clicking and typing need level 2.** Reading pages is allowed at lower levels; clicking or filling forms on a business below level 2 waits for your approval.

## What was built
| Piece | File |
|---|---|
| `artifacts` (canonical record: hash, type, size, provenance) + content-addressed local store | `models.py`, `jackson_api/artifacts.py` |
| `browser_sessions` (worker reports `status`; owner sets `control`, which the worker can't write: column-level grant) | `models.py`, `migrations/versions/0008_browser.py` |
| `with ctx.browser() as b:` → `goto`, `text`, `click`, `fill`, `screenshot`; per-request allowlist; masked screenshots; owner-control gate before every action; policy per action; `browser.*` events | `jackson_api/browser.py`, `worker.py` |
| `browser.session` registry entry (grant config `allowed_domains`) | `tools/builtin.py` |
| API: list/get sessions, `POST .../control` (pause/takeover/resume/terminate), `GET /artifacts/{id}` (bytes) and `/meta` | `jackson_api/routes_browser.py` |

**Honest limits:**
- "Take over" pauses the agent and shows its latest screenshot. Live remote control of the page (VNC-style) isn't built; that's for the console (phase 19).
- Artifacts are stored on local disk, so the API and worker must share `ARTIFACT_DIR` until S3 storage is chosen with hosting.
- Browser isolation is Chromium's own plus our allowlist, not a container (phase 20).

## Tests
- **API (`services/api`): 224 passed** against real PostgreSQL 16 and a **real headless Chromium** (10 new in `test_browser.py`, with a local test site).
- **Browser session visible:**
  - goto + click update the session (URL, title, latest screenshot)
  - the screenshot downloads as a real PNG
  - the session API shows the current page
  - `browser.screenshot` events appear on the live SSE feed
- **Privacy:** password + `[data-sensitive]` are masked (2 elements) and their text is stripped from what the agent reads.
- **Allowlist:** blocked tracker and script sub-requests are recorded; navigating off the allowlist is refused.
- **Grant and autonomy:** no grant → refused; click at level 1 → waits for approval, after the read already worked.
- **Owner controls:**
  - pause really blocks the agent (≥0.5 s) until resume
  - take over → terminate stops the agent and fails the task "terminated by owner"; controls on an ended session → 409
  - a viewer can't control; **the agent can't write `control`** (database permission denied)
  - artifacts are tenant-isolated
- **Mutation checks** (all caught):
  - letting every request through fails the allowlist test
  - ignoring owner control fails 2 tests
  - keeping sensitive text fails the privacy test
- **Phase 10 CI fix (PR #8):** my repo secret scan caught a key-shaped test literal I'd written. The fix builds it at runtime. Shown failing then passing; PR #8 merged after GitHub went green.

## Spec acceptance tests now covered
- ✅ Browser session visible

## Next: phase 12 — agent registry and factory
