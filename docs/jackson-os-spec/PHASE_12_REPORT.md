# Phase 12 — Agent registry and factory

Date: 2026-11-26 · Decision: ADR-022 in [PHASE_01_GAP_REPORT.md](PHASE_01_GAP_REPORT.md#3-architecture-decisions-adrs).

## Plain English
- **Agents are now real, versioned job descriptions.** Each one has a role, a goal, instructions, the tools it may use, a budget, limits, tests it must pass, and what to do when stuck. They're settings, not free-roaming identities.
- **Nothing goes live without you.** A new agent (written by you, drafted by the factory, or imported from Growth OS) must pass a rules check and a sandbox test run. Then only you can switch it on, and only if you've already granted every tool it wants. New versions replace old ones; you can roll back.
- **Every answer gets checked.** An independent checker tests each output against the agent's rules; bad output fails instead of slipping through.
- **Agents can hand work to helpers, within limits:**
  - at most 2 levels deep
  - at most 8 helper agents per job
  - a helper never gets more tool access than the agent that started it
  - per-agent limits on how many can run at once and how much AI spend each job may use
- **Growth OS's six agents** (Lead-Gen, Marketing, Voice, Ad Creation, Service & Follow-up, Social Trends) import as drafts. Their outside services (Apify, Upload-Post, ElevenLabs, FAL) aren't connected yet, and each one says so in its instructions.

## What was built
| Piece | File |
|---|---|
| `agent_specs` (versioned; worker can only insert **drafts**, enforced by a restrictive RLS policy) + delegation columns on `tasks` (parent, depth, agent, tool scope, input) | `models.py`, `migrations/versions/0009_agents.py` |
| Validation rules, output verifier, limits, Growth OS agent list | `jackson_api/agents.py` |
| Task handlers: `agent` (run + verify), `agent_eval` (sandbox tests, no tools), `agent_factory` (draft from a described gap) | `jackson_api/agent_handlers.py` |
| `ctx.spawn_agent()` with depth/count quotas and scope intersection; per-agent concurrency in `claim`; per-task agent model budget; tool-scope enforcement in the tool gateway | `jackson_api/worker.py` |
| API: create draft, get (+validation +latest eval), evaluate, activate (owner; needs passing eval + granted tools + quota), retire, rollback, stats, run, factory, import Growth OS | `jackson_api/routes_agents.py` |

**Limit:** agents currently make one model call per task. Their tools are enforced through the gateway and used by built-in handlers. A full model-driven tool-calling loop comes with the connector phases.

## Tests
- **API (`services/api`): 241 passed** against real PostgreSQL 16 (17 new in `test_agents.py`).
- **Validation and activation gates:**
  - validation reports a bad name, unknown tool, missing tests and an instruction trying to bypass rules
  - activation needs a passing evaluation **of that version**; a failing eval blocks it
  - activation needs every tool already granted
  - versioning retires the old version; rollback works; only the owner activates
- **Running and verifying:** an agent run is verified, and stats are recorded; bad output fails with the escalation rule named.
- **Delegation:**
  - a spawned agent carries depth and its parent
  - **depth quota** and **count quota** are enforced
  - a child's tool scope is narrowed by its parent's (an out-of-scope tool is refused)
  - inactive agents can't be spawned
- **Limits:** per-agent concurrency; per-run agent model budget (the 2nd call is blocked at 1 cent).
- **Agents can't promote themselves:** the worker can't insert an active spec (RLS) or update status (permission denied).
- **Factory and import:** the factory produces a draft only (created_by factory; a mock draft fails validation until the owner completes it); the Growth OS import creates 6 drafts once.
- **Mutation checks** (all caught):
  - no depth limit
  - no scope narrowing
  - activation without an eval
  - activation with ungranted tools

## Spec acceptance tests now covered
- ✅ Agent cannot spawn beyond quota

## Next: phase 13 — opportunity research
