# Phase 23 — JAXON OS "The City" live command center

The City replaces the phase 22 console redesign as the main dashboard. It is built in six phases, each its own PR. Rule zero: **nothing fake**. Every light, envelope, beam and number comes from a database row a real agent wrote. Design details are in [ADR-037](../adr/0037-city-events.md).

## Status
| Phase | What | State |
|---|---|---|
| 1 | Registry tables, `emit()`, heartbeat, wrapped worker | done |
| 2 | Snapshot API + live SSE stream + events query | done |
| 3 | 3D exterior: buildings, windows, orb, envelopes, beams, HUD, ticker, phone list | done |
| 4 | Interiors, agent panel with Pause/Resume/Reassign/Kill, CEO inbox | next |
| 5 | Command bar → Jaxon routing, shown end to end | |
| 6 | Replay, cost guard, failure siren, trace view, client view | |

## Phase 3: what's on screen
- **CEO tower** (Steady) in the middle. Each pending approval is a red letter on its roof, and the count shows on its label.
- **Jaxon orb** above the tower. It pulses on every event, and its glow scales with events per minute.
- **Department ring**, one slot per department.
  - A department with at least one real agent is a building, with one floor per team.
  - A department with no agent is an empty lot labelled "not staffed".
- **Windows:** one per agent, on its floor, visible from every side. Colours:
  - cyan: working
  - green: finished in the last 2 minutes
  - amber: waiting for approval
  - red: failed
  - gray: offline
  - purple: paused
  - dark: idle
- **Envelopes** fly only for real events:
  - CEO → Jaxon (command)
  - Jaxon → floor (task)
  - floor → Jaxon (report)
  - floor → CEO (red-sealed approval)
  - CEO → floor (decision)

  At most 60 fly at once; the rest queue.
- **Beams** rise from a building's roof: green when a task finishes, red when it fails. A gold beam over the CEO tower marks real revenue.
- **HUD:** agents online, tasks in flight, done/failed today, spend today. Revenue appears only when a connected source reports it.
- **Ring-road ticker:** the last 20 events.
- **Phone:** low mode (no bloom, fewer stars, 1× pixels) plus a **List** toggle showing the same live data as a plain feed. With no WebGL, only the list. A slow GPU drops itself to the light look automatically.
- `JAXON_DEMO=true` shows a permanent red "DEMO DATA" banner. There is no demo generator at all: the city simply shows what's in the database.

## Where things are
- **Source:** `apps/city` (React 19, @react-three/fiber, drei, postprocessing Bloom, zustand, Vite).
  - pure logic: `src/lib/` (unit-tested)
  - scene: `src/scene/City.tsx`
  - UI: `src/ui/`
- **Build:** `pnpm --filter @jackson-os/city build` writes `services/api/jackson_api/city_dist/`.
  - That folder is committed, so the API image needs no Node.
  - CI rebuilds it and fails if the commit is stale; the build is deterministic.
- **Served at:** `/city`, under the same strict CSP as the console (`script-src 'self'`). Library code sits in `vendor-*` chunks, so "no random activity" can be checked on our own chunks (`test_production_bundle_has_no_fake_activity`).
- **Sign-in:** same as the console (Clerk, or a pasted token in dev and tests). The console has an "Open the City" button.
