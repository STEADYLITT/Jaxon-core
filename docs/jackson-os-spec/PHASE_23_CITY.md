# Phase 23 — JAXON OS "The City" live command center

The City replaces the phase 22 console redesign as the main dashboard. It is built in six phases, each its own PR. Rule zero: **nothing fake**. Every light, envelope, beam and number comes from a database row a real agent wrote. Design details are in [ADR-037](../adr/0037-city-events.md).

## Status
| Phase | What | State |
|---|---|---|
| 1 | Registry tables, `emit()`, heartbeat, wrapped worker | done |
| 2 | Snapshot API + live SSE stream + events query | done |
| 3 | 3D exterior: buildings, windows, orb, envelopes, beams, HUD, ticker, phone list | done |
| 4 | Interiors, agent panel with Pause/Resume/Reassign/Kill, CEO inbox | done |
| 5 | Command bar → Jaxon routing, shown end to end | next |
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

## Phase 4: click-through and real controls
- **Click a building:** the camera flies to it and its floor list slides out, with each agent's live state.
- **Click a floor:** you go inside the office. There's one desk per agent, with a low-poly robot whose eye shows the status colour.
  - Each monitor is that agent's live feed: current task, time on task, last 8 log lines, tool calls.
- **Click a robot (or a window from outside):** the agent panel opens. It shows:
  - the current task, traced back to Steady's original command
  - the live log and tool calls
  - cost, done and failed today, and the 7-day success rate
  - **Pause / Resume / Reassign / Kill**
- **Click the Jaxon orb:** recent commands, which department and agent each went to, and memory lookups. Routing reasons arrive in phase 5.
- **Click the CEO tower:** Steady's inbox. **Approve** lets the waiting agent continue; **Deny** stops its task. Alerts are listed too.
- **Phone:** the list view links every agent, the inbox and the orb to the same panels (bottom sheets).

### What the controls really do
- **Pause:**
  - The agent takes no new work (the worker's claim skips it).
  - Its current run is paused, and the handler's next `should_stop()` check stops it.
  - `agent.paused` is emitted after both are in place.
- **Resume:** lifts a pause or a kill. A paused run continues.
- **Kill:**
  - In production every task runs in its **own child process** (`WORKER_ISOLATE`, default on).
  - The worker parent sees the owner's hold, sends **SIGKILL** to the process and waits for it to exit.
  - Then it records the task as cancelled ("killed by the owner") and cancels the run.
  - **Only then** is `agent.killed` emitted, and the window goes gray. If nothing was running, the hold itself is the stop.
  - A fork lock keeps the heartbeat thread out of the database at fork time.
- **Reassign:**
  - Moves a waiting or queued task of an AgentSpec agent to another active AgentSpec agent.
  - Built-in workers each do one kind of job, so for them the API refuses with a reason.
- **Who can use them:** owner or admin only, and every action is audited. The worker can't lift a pause or kill: a DB trigger refuses it.

### Endpoints
| Endpoint | What it does |
|---|---|
| `GET /tenants/{t}/city/agents/{id}` | the monitor/panel data, plus which controls apply |
| `GET /tenants/{t}/city/jaxon` | commands → routed tasks/agents/departments, memory |
| `POST /tenants/{t}/city/agents/{id}/pause` | Pause |
| `POST /tenants/{t}/city/agents/{id}/resume` | Resume |
| `POST /tenants/{t}/city/agents/{id}/kill` | Kill |
| `POST /tenants/{t}/city/agents/{id}/reassign` | Reassign (`{to_agent_id}`) |
