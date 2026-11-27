# ADR-037: The City — agent registry and one event stream

**Status:** accepted, 2026-11-27 (phase 23)

## Rule zero
Everything the City draws comes from a row a real agent action wrote. There is no seed data and no random activity. Demo data is allowed only behind `JAXON_DEMO=true` (off by default), with a permanent red banner.

## Mapping onto existing tables

| Spec | Jackson |
|---|---|
| `commands` | `workflow_runs`. `POST /tenants/{t}/commands` emits `command.issued`. |
| `tasks` | `tasks`, with `parent_task_id` and `run_id` → command |
| `approvals` | `approvals`, using the existing decision endpoint |
| cost | `model_usage` + budget ledger, emitted as `cost.recorded` |
| `departments`, `floors`, `agents`, `events` | **new** (migration 0018) |

## Agents
An agent is a task handler that exists in the worker (`city.BUILT_IN`, e.g. `content_generate` → Marketing / Content / "Content Writer"), or an active AgentSpec. Its department comes from keywords in its name and role.
- Jaxon's own agents (`plan`, `launch_propose`) live in the orb.
- A department with no agent is "not staffed". Today that's Agency Fulfillment and Support.

## Events
`city.emit()` inserts into `events` and calls `pg_notify('jackson_events', '<tenant>:<id>')`. It runs in the caller's transaction, so an event exists only if the state change committed.

The worker emits at each real transition:

| Event | Agent status |
|---|---|
| `task.started` | working |
| `tool.called` (tools + model calls) | — |
| `cost.recorded` | — |
| `agent.log` (`ctx.log`) | — |
| `approval.requested` | waiting |
| `task.completed` | done |
| `task.failed` | failed |
| retry (logged) | idle |

The API emits `command.issued`, `approval.decided` and `revenue.recorded` (actual revenue only).

Envelopes are `from_kind/from_id → to_kind/to_id`:
- CEO → Jaxon
- Jaxon → building
- building → Jaxon
- building → CEO, for approvals

## Liveness
- Every 15s, a live worker updates `last_heartbeat_at` on all agents. Each running agent also gets an `agent.heartbeat` event naming its task.
- `city.sweep_offline` runs on the API side, because it must work when the worker is dead. An agent with no heartbeat for 60s is marked `offline` and gets an `agent.offline` event.

## Owner holds
- `paused` and `killed` are owner states. A DB trigger (`agent_owner_holds`) refuses the worker role any change away from them.
- `events` is append-only: neither role has UPDATE or DELETE.

## API (phase 2)
Paths follow this API's tenant-scoped style.

| Spec | Jackson |
|---|---|
| `GET /api/city/state` | `GET /tenants/{t}/city/state` |
| `/api/city/stream` | `GET /tenants/{t}/city/stream` |
| `GET /api/events?since=&agent=&task=` | `GET /tenants/{t}/city/events?since=&agent=&task=&run=&since_ts=` |
| `POST /api/commands` | `POST /tenants/{t}/commands` (existing) |
| `POST /api/approvals/:id` | `POST /tenants/{t}/approvals/{id}/decision` (existing) |

- **State** is the full snapshot the page rebuilds from on every load:
  - departments, floors and agents, with live status, current task + parent command, cost, done/failed today, 7-day success rate
  - Jaxon's events/minute
  - HUD
  - CEO inbox: approvals + alerts
  - the last 20 events
  - `last_event_id`
- **Stream** is SSE that resumes with `Last-Event-ID`. While anyone watches, it runs the offline sweep every 15s, so a dead worker turns gray with no UI action.
- **Revenue today** is `null` (hidden) unless a connected source (Shopify/Whop/Stripe/import) has written revenue. It is never estimated.
