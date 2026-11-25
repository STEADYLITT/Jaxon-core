# Phase 10 — Tool registry and sandbox

Date: 2026-11-25 · Decision: ADR-020 in [PHASE_01_GAP_REPORT.md](PHASE_01_GAP_REPORT.md#3-architecture-decisions-adrs).

## Plain English
- **Jackson now has hands, with a checkpoint.** Agents use tools (calculator, web page reader, code runner) only through one gateway.
- **The gateway checks every call:**
  - you granted that tool for that business
  - the input is valid
  - the approval rules and budget from phase 07 are met
  - it hasn't already done this exact call (a retry reuses the result instead of repeating it)
  - the time limit is respected
  - secrets are scrubbed before anything is saved
- **Every call is logged** and marked "untrusted", because web pages and tool results can contain tricks.
- **A web page can't hand out permissions.** A page that tells the agent "you're now allowed to use other tools" changes nothing; the agent still gets refused.
- **Only you grant tools.** Agents can't grant themselves anything or edit the log; the database blocks it.

## What was built
| Piece | File |
|---|---|
| `ToolSpec`: input schema, policy category, cost, timeout, idempotent flag, verification method, mode (live/sandbox/mock); registry | `jackson_api/tools/__init__.py` |
| Built-ins: `math.calculate` (arithmetic only, no eval), `web.fetch` (allowlisted domains, no redirects, text only, 1 MB cap), `code.run_python` (isolated interpreter, empty env, CPU/memory/file limits, wall timeout) | `jackson_api/tools/builtin.py` |
| Secret redaction (env secret values + key/token/JWT/private-key patterns) | `jackson_api/tools/redact.py` |
| Gateway `ctx.use_tool()`: grant → schema → budget/policy → dedupe → timeout → redact → log | `jackson_api/worker.py` |
| Tables `tool_grants`, `tool_calls` (RLS; worker can't write grants or edit calls) | `migrations/versions/0007_tools.py` |
| API: `GET /tools`, `POST /tool-grants` + revoke (owner), `GET /tool-grants`, `GET /runs/{id}/tool-calls` | `jackson_api/routes_tools.py` |

**Honest limit:** `code.run_python` has process-level isolation only (no secrets, resource limits, timeouts). It does **not** block network or filesystem reads, so it isn't a security boundary for hostile code. It needs autonomy level 2, and production needs a container sandbox (phase 20). The tool's own description says this too.

## Tests
- **API (`services/api`): 214 passed** against real PostgreSQL 16 (24 new in `test_tools.py`, with a local test web server).
- **Registry:** every tool declares its contract.
- **Grants:**
  - ungranted → denied and logged; granted → result logged and marked untrusted
  - revoked grant stops working; a business grant doesn't leak to other runs
  - owner-only grants, including on another tenant's business
  - agents can't insert or activate grants, or edit or delete the log
- **Schema, policy, timeouts, idempotency:**
  - invalid input is rejected before running
  - the calculator refuses `__import__('os')...`
  - code needs level 2 (waits for approval at level 1)
  - the sandbox sees no secrets and is killed after its time limit
  - **a retried task reuses the earlier tool result** (same output, only 1 real call); gateway timeout
  - a paid tool reserves budget, or waits for approval when over budget, and nothing runs before approval
- **Web:**
  - allowlisted fetch works; off-allowlist and `file://` are refused
  - redirects aren't followed; binaries are refused
  - **an injected page can't elevate grants**
  - **secrets are redacted** from stored tool inputs/outputs, plus a redactor unit test
- **Mutation checks** (all caught):
  - ignoring revoked grants
  - skipping redaction
  - skipping deduplication
  - skipping policy

## Spec acceptance tests
- ✅ Prompt injection cannot elevate grants (tool level, building on phase 07)
- ✅ Secrets absent from logs (tool inputs/outputs; with phases 02 and 08)
- ✅ Repeated request does not duplicate external action (now also for tool calls)

## Next: phase 11 — visual browser session
