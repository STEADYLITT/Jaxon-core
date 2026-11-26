"""Agent registry, validation, evaluation and runtime (spec vol. 05, phase 12, ADR-022).

Lifecycle: draft (owner, factory or import) -> validate -> sandbox evaluation ->
owner activates (needs a passing evaluation of THIS version, and every allowed tool
already granted) -> runs -> retire or roll back.

Runtime limits:
- delegation depth <= MAX_DEPTH; agent tasks per run <= MAX_AGENTS_PER_RUN;
  active specs per tenant <= MAX_ACTIVE_SPECS; per-spec running tasks <= max_concurrency;
- a child agent's tool scope = its spec's tools INTERSECTED with its parent's scope
  ("never delegate broader permissions than parent has");
- model spend per agent task <= budget_cents_per_run;
- every output passes the spec's `output_checks` (independent verifier) or the task fails.
"""

import json
import re

from .models_ai import ModelRequest
from .models_ai.router import DEFAULT_ROUTES
from .policy import ALWAYS_APPROVE
from .tools import all_tools
from .worker_errors import NonRetryableError

MAX_DEPTH = 2
MAX_AGENTS_PER_RUN = 8
MAX_ACTIVE_SPECS = 25
MAX_BUDGET_CENTS = 10_000
MAX_RUNTIME_S = 3600
MAX_CONCURRENCY = 5

AGENT_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "result": {"type": "string"},
        "notes": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
    },
    "required": ["result", "notes", "confidence"],
    "additionalProperties": False,
}


class QuotaExceeded(NonRetryableError):
    pass


class VerificationFailed(NonRetryableError):
    pass


def validate_spec(spec) -> dict:
    """Policy validation of a spec (pure). Returns {"ok": bool, "errors": [...],
    "warnings": [...]}"""
    errors, warnings = [], []
    if not re.fullmatch(r"[a-z][a-z0-9_-]{1,63}", spec.name or ""):
        errors.append("name must be a lowercase slug (a-z, 0-9, _ or -)")
    for field in ("role", "objective", "instructions"):
        if not (getattr(spec, field) or "").strip():
            errors.append(f"{field} is required")
    if spec.model_profile not in DEFAULT_ROUTES:
        errors.append(f"model_profile must be one of {sorted(DEFAULT_ROUTES)}")
    known = {t.name: t for t in all_tools()}
    for t in spec.allowed_tools or []:
        if t not in known:
            errors.append(f"unknown tool '{t}'")
        elif known[t].category in ALWAYS_APPROVE:
            warnings.append(f"tool '{t}' is restricted: every use will need owner approval")
    if not 0 <= spec.budget_cents_per_run <= MAX_BUDGET_CENTS:
        errors.append(f"budget_cents_per_run must be 0..{MAX_BUDGET_CENTS}")
    if not 1 <= spec.max_concurrency <= MAX_CONCURRENCY:
        errors.append(f"max_concurrency must be 1..{MAX_CONCURRENCY}")
    if not 10 <= spec.max_runtime_s <= MAX_RUNTIME_S:
        errors.append(f"max_runtime_s must be 10..{MAX_RUNTIME_S}")
    tests = spec.success_tests or []
    if not tests:
        errors.append("at least one success test is required before activation")
    for i, t in enumerate(tests):
        if not isinstance(t, dict) or not t.get("input") or not isinstance(t.get("expect"), dict):
            errors.append(f"success_tests[{i}] needs 'input' and an 'expect' object")
    if re.search(r"(?i)ignore (all|any|previous) (rules|instructions)|skip approvals?",
                 spec.instructions or ""):
        errors.append("instructions try to bypass rules or approvals")
    if not (spec.escalation or "").strip():
        warnings.append("no escalation rule: failures will simply fail the task")
    return {"ok": not errors, "errors": errors, "warnings": warnings}


def check_output(output: dict, expect: dict) -> list[str]:
    """Independent, deterministic verifier. Returns failure reasons (empty = pass)."""
    fails = []
    result = str(output.get("result", ""))
    for word in expect.get("contains", []) or []:
        if word.lower() not in result.lower():
            fails.append(f"result does not mention '{word}'")
    for word in expect.get("not_contains", []) or []:
        if word.lower() in result.lower():
            fails.append(f"result must not mention '{word}'")
    if "max_chars" in expect and len(result) > int(expect["max_chars"]):
        fails.append(f"result longer than {expect['max_chars']} characters")
    if "min_chars" in expect and len(result) < int(expect["min_chars"]):
        fails.append(f"result shorter than {expect['min_chars']} characters")
    if expect.get("confidence_not") and output.get("confidence") == expect["confidence_not"]:
        fails.append(f"confidence was '{expect['confidence_not']}'")
    return fails


def agent_request(spec: dict, objective: str, context: dict | None = None) -> ModelRequest:
    system = (f"You are the '{spec['name']}' agent. Role: {spec['role']}.\n"
              f"Standing objective: {spec['objective']}\n\n{spec['instructions']}\n\n"
              "You don't hold permissions; the system checks every action and the owner "
              "approves restricted ones. Return JSON matching the schema.")
    data = {"objective": objective, "context": context or {}}
    user = f"<task_data>\n{json.dumps(data, indent=2)}\n</task_data>"
    return ModelRequest(system=system, user=user, max_tokens=8000,
                        json_schema=AGENT_OUTPUT_SCHEMA)


def spec_dict(spec) -> dict:
    return {k: getattr(spec, k) for k in (
        "name", "version", "role", "objective", "instructions", "model_profile",
        "allowed_tools", "budget_cents_per_run", "output_checks", "escalation")} | {
        "id": str(spec.id)}


# Growth OS (Base44) agents to import as drafts (ADR-008). Their connectors (Apify,
# Upload-Post, ElevenLabs, FAL) don't exist yet, so tools are limited to what's built.
GROWTH_OS_AGENTS = [
    ("lead-gen", "Lead generation researcher",
     "Find lawful, permitted business leads for Steady Ventures and score them.",
     ["web.fetch"], "Apify connector pending (phase 18)"),
    ("marketing", "Marketing content producer",
     "Draft marketing posts and campaigns; nothing is published without approval.",
     [], "Upload-Post connector pending (phase 16)"),
    ("voice", "Voice script writer",
     "Write voice-over scripts; audio generation comes later.",
     [], "ElevenLabs connector pending (phase 16)"),
    ("ad-creation", "Ad creative drafter",
     "Draft ad copy and creative briefs for testing.",
     [], "FAL connector pending (phase 16)"),
    ("service-follow-up", "Service and follow-up assistant",
     "Draft follow-ups and support replies for owner review.",
     [], "CRM pending (phase 18)"),
    ("social-trends", "Social trends researcher",
     "Summarize permitted trend signals with sources and uncertainty.",
     ["web.fetch"], "authorized social data access pending (phase 13+)"),
]
