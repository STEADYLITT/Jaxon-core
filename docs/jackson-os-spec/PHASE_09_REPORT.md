# Phase 09 — Memory and provenance

Date: 2026-11-25 · Decision: ADR-019 in [PHASE_01_GAP_REPORT.md](PHASE_01_GAP_REPORT.md#3-architecture-decisions-adrs).

## Plain English
- **Jackson remembers, with receipts.** Preferences, business facts, decisions, project status, tool results and lessons are stored with where they came from (you, a specific run, a tool, an import), when, and how sure we are.
- **Fact vs. guess:** every memory is marked fact, inference or hypothesis, and the planner is told to treat guesses as guesses.
- **Big contradictions wait for you.** If something important conflicts with what's on file, such as a new price, it isn't used until you confirm it. Agents can never overwrite a memory; any conflict they cause waits for your review.
- **You control it:** search, export everything, correct (old versions are kept), delete (every version gone for real), and set expiry dates. Private memories marked "owner only" are hidden from viewers and from agents.
- **Plans cite their sources.** The planner pulls relevant memories, and the saved plan lists exactly which ones it used and where each came from. Made-up references are dropped.

## What was built
| Piece | File |
|---|---|
| `memory_records`: kind, fact/inference/hypothesis, key, impact, confidence, source type/ref/detail, scope, status, version chain, retention; full-text index; RLS | `models.py`, `migrations/versions/0006_memory.py` |
| Write with conflict rules, confirm, correct, history, delete-all-versions, recall (ranked full-text), purge expired | `jackson_api/memory.py` |
| API: `POST/GET /memory`, `GET /memory/pending`, `GET /memory/export` (owner), `GET /memory/{id}` (+history), `POST .../correct`, `POST .../confirm`, `DELETE /memory/{id}` (owner) | `jackson_api/routes_memory.py` |
| Agent hooks `ctx.recall()` / `ctx.remember()` with automatic run/task provenance | `jackson_api/worker.py` |
| Planner uses memory and cites it (`memory_used` + `memory_citations`) | `jackson_api/planner.py` |

**Vector search:** deferred. Retrieval uses Postgres full-text ranking. Meaning-based (vector) search will supplement it once an embedding provider is chosen; canonical records stay the source of truth either way (vol. 00).

## Tests
- **API (`services/api`): 190 passed** against real PostgreSQL 16 (20 new in `test_memory.py`).
- **Provenance and recall:** provenance + citation; ranking and kind filter; fact/inference/hypothesis enforced.
- **Conflicts and corrections:**
  - a low-impact owner conflict supersedes, with history
  - no duplicates for identical content
  - a high-impact conflict waits for confirmation and isn't recalled until confirmed
  - reject keeps the original
  - corrections create versions, and only the latest can be corrected
- **Deletion and export:** delete removes every version, and audit keeps no content; export and delete are owner-only.
- **Scope, retention, isolation:**
  - owner-only memory is hidden from viewers and agents
  - expired memory isn't recalled, then gets purged
  - tenant isolation; another tenant's business is rejected
  - the worker role can't UPDATE or DELETE memories
- **Agents:** agent memories carry run/task provenance; **agent conflicts always wait for the owner** (high and low impact).
- **Memory cites source:** the model is offered the memory, the plan cites it with provenance, and a fabricated id is dropped.
- **Design fix found by tests:** agents had no UPDATE right, so a conflicting agent write failed at the database. Rather than granting UPDATE, which would let an agent confirm its own pending facts, agent conflicts now always wait for the owner.
- **Mutation checks** (all caught):
  - keeping unvetted ids fails the citation test
  - recalling non-active memories fails 1 test
  - bypassing confirmation fails 4 tests
- Not run this phase: a separate real-process run (the API paths are covered through the test client against real Postgres).

## Spec acceptance tests now covered
- ✅ Memory cites source

## Next: phase 10 — tool registry and sandbox
