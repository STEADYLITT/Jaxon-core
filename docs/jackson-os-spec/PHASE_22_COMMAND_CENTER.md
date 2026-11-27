# Phase 22 — Command-center console

Date: 2026-11-27 · Owner design request.

## What you see
- **Greeting:** "Good morning, Steady — 3 things need you today". The count is real: pending approvals, launches Jackson proposed, and warnings or critical alerts.
- **Jackson's core** (center): a 3D lattice that pulses faster while runs are in progress, turns red under Global Stop, and shows its status in words.
- **Agents:** they orbit the core as glowing nodes, linked by light and coloured by state (idle, running, waiting for approval, error). Click a node, or the same agent in the list below, to see its current task, status, cost today, role and task counts.
- **Live feed:** run and approval events stream in from the tenant event stream as they happen.
- **Businesses:** holographic cards with revenue, spend and profit. Estimates are labelled "not actual" and never added in. The full table is one click away.
- **Prominent controls:** the approvals inbox, the Launch button on each opportunity, and Global Stop as a big red control.
- **Everything else is still there:** metrics, live browser work, artifacts, connectors, audit log.

## Fast, readable, honest
- **3D only where it helps:** on screens at least 900px wide with WebGL, and only without a reduced-motion or low-power hint (≤2 GB memory or ≤2 CPU cores). Everyone else gets the 2D layout: an animated CSS core and one column, ordered core → approvals → launches → alerts → feed → agents. Rendering pauses in background tabs, and the pixel ratio is capped at 1.5.
- **Contrast:** body text is about 17:1 (WCAG AAA); accent colours are ≥ 6:1.
- **No fake data:** only real records, with empty states when nothing exists.
- **Same security as before:** strict CSP (`script-src 'self'`), every value written as text, sign-in unchanged. Three.js r180 (MIT) is served from Jackson itself (`/console/vendor/`, allow-listed file names), so no third-party script host is needed.

## Tests
- `test_console_ui.py`, in real Chromium against the real API:
  - the greeting counts only real items
  - desktop: 3D core, agent nodes and agent details
  - phone 375px: 2D layout and no sideways scroll
  - reduced motion: 2D
  - the live feed streams a real `run.created`
  - business cards and empty states
  - text contrast is at least 7:1
  - vendor files are allow-listed
- The existing console tests still pass: stop/resume, the XSS-as-text check, Clerk sign-in and the Launch button.
- Full suite: 470 passed.
