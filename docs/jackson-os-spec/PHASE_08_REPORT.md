# Phase 08 — Model adapters (Claude, OpenAI, local, mock)

Date: 2026-11-25 · Decision: ADR-018 in [PHASE_01_GAP_REPORT.md](PHASE_01_GAP_REPORT.md#3-architecture-decisions-adrs).

## Plain English
- **Jackson can now think, once a key is added.** The "plan" step sends your order to an AI model in Jackson's voice and gets back a structured plan: summary, steps (each tagged with what kind of action it needs), assumptions, risks, and the evidence to check first.
- **No key, no pretending.** With no AI key configured, every plan is a clearly marked **[MOCK]** placeholder that costs $0. Nothing claims to be real analysis.
- **Default model: Claude Opus 5.5**, with deeper thinking for planning and lighter thinking for quick tasks. OpenAI and self-hosted local models can be switched on with settings.
- **AI spend is metered and capped:** every call's tokens and cost are logged, and each workspace has a monthly AI budget (default $20). When it's used up, Jackson stops calling models instead of running up a bill.
- **Plans are data, not actions.** A plan that says "spend $50 on ads" spends nothing. Every real action still goes through the phase 07 approval rules.

## What was built
| Piece | File |
|---|---|
| Interface, price table, cost math, error types | `jackson_api/models_ai/base.py` |
| Claude via the official `anthropic` SDK (2.14): `claude-opus-5-5`, `output_config.effort`, JSON schema output, server-side refusal fallback (`fallbacks: "default"`), refusal detection, cost billed on the model that actually served | `models_ai/anthropic_provider.py` |
| OpenAI and OpenAI-compatible local servers via the official `openai` SDK (3.59); off until model name and prices are configured | `models_ai/openai_provider.py` |
| Clearly labeled MOCK provider | `models_ai/mock_provider.py` |
| Router: profile → provider/model/effort; falls back to MOCK with a stated reason | `models_ai/router.py` |
| Jackson persona, kept separate from policy | `persona/jackson.md` |
| Planner (`plan` handler): command data passed as delimited JSON; injection-resistant instructions | `jackson_api/planner.py` |
| `ctx.call_model()`: the only path to a model (budget check → route → call → meter → `model.called` event) | `jackson_api/worker.py` |
| `model_usage` table (append-only, RLS) | `migrations/versions/0005_model_usage.py` |

Routes (defaults; change with `JACKSON_MODEL_PROVIDER` / `JACKSON_MODEL_<PROFILE>`):
| Profile | Model | Effort |
|---|---|---|
| planning | claude-opus-5-5 | high |
| standard | claude-opus-5-5 | medium |
| fast | claude-opus-5-5 | low |

Lowering effort on the top model is the first cost lever, and keeping one model keeps one prompt cache. A cheaper model is only used if you choose it.

## Tests
- **API (`services/api`): 170 passed** against real PostgreSQL 16 (20 new in `test_models_ai.py`). No test can reach a real provider: an automatic fixture clears all key settings.
- **Claude adapter**, through the real SDK with a faked network:
  - request shape: model, effort, JSON schema, fallback beta + `fallbacks`, no temperature/top_p/top_k/thinking, no prefill
  - cost billed on the model that actually served
  - refusal is not retried; 400 → permanent failure, 429 → retried, 500 → retried
- **OpenAI adapter**, same approach: request shape and cost, refusal, 400; stays off without a model name and prices.
- **Router:** no keys → labeled MOCK; default is Opus 5.5 with effort by profile; forced provider works, and falls back to MOCK if that provider isn't configured.
- **Persona** contains no policy.
- **Planner through the worker:**
  - mock run succeeds at $0 with a `model_usage` row
  - a "live" run (faked network) sends persona + objective + constraints, records a cost of 20,800 micro-USD ($0.0208)
  - plan steps create no tasks, approvals or ledger entries
  - monthly budget used up → no call is made and the task fails
  - a 429 is retried by the worker and then succeeds
- **Secrets:** the API key was really sent but appears in no stored task, event, audit or usage row (spec acceptance "secrets absent from logs", for stored data).
- **Bug found and fixed:** Postgres returned the monthly spend total as a decimal, which crashed the budget message and turned a clean stop into retries. Caught by the budget test.
- **Mutation checks** (both caught): removing the budget check fails the budget test; making the router ignore "is this provider configured?" fails 3 tests.
- **Real processes:** migrate 0001→0005, then API + worker with no keys. The first-slice command "Find a product opportunity and build a draft store" reached `succeeded` with a `[MOCK]` plan, provider `mock`, cost 0.

## Not verified (needs keys)
- No live call to Claude or OpenAI has been made. Live verification needs `ANTHROPIC_API_KEY` (and optionally OpenAI settings). It costs real money (a few cents per plan), so do it once you add a key.

## Next: phase 09 — memory and provenance
