"""Task handlers for agents: run, sandbox-evaluate, and factory-draft (phase 12)."""

import json
import re

from sqlalchemy import func, select

from .agents import AGENT_OUTPUT_SCHEMA, VerificationFailed, agent_request, check_output
from .models_ai import ModelRequest
from .models_ai.router import DEFAULT_ROUTES
from .worker_errors import NonRetryableError


def _result(output):
    from .worker import TaskResult  # lazy: worker imports this module

    return TaskResult(output=output)


def agent_handler(ctx):
    """Run an active agent once on its objective, then verify the output independently."""
    spec = ctx.agent_spec
    if spec is None or spec["status"] != "active":
        raise NonRetryableError("agent spec is missing or not active")
    objective = (ctx.input or {}).get("objective") or ctx.objective
    resp = ctx.call_model(spec["model_profile"],
                          agent_request(spec, objective, (ctx.input or {}).get("context")))
    out = json.loads(resp.text)
    fails = check_output(out, spec["output_checks"] or {})
    if fails:
        raise VerificationFailed(
            f"output failed verification: {'; '.join(fails)}. "
            f"Escalation: {spec['escalation'] or 'none set; task failed'}")
    return _result({"agent": spec["name"], "version": spec["version"], "mode": resp.mode,
                    "model": resp.model, "output": out, "verified": True,
                    "notes": resp.notes})


def eval_handler(ctx):
    """Sandbox evaluation: run every success test through the agent; report pass/fail.
    No tools run here, so evaluation can't cause side effects."""
    spec = ctx.agent_spec
    if spec is None:
        raise NonRetryableError("agent spec not found")
    results, modes = [], set()
    for i, test in enumerate(spec["success_tests"] or []):
        resp = ctx.call_model(spec["model_profile"], agent_request(spec, test["input"]))
        modes.add(resp.mode)
        out = json.loads(resp.text)
        fails = check_output(out, test.get("expect") or {})
        results.append({"test": test.get("name") or f"test-{i + 1}", "passed": not fails,
                        "failures": fails, "result_excerpt": str(out.get("result", ""))[:300]})
    return _result({
        "spec_id": spec["id"], "spec_version": spec["version"],
        "passed": bool(results) and all(r["passed"] for r in results),
        "results": results, "mode": "live" if modes == {"live"} else "mock",
    })


FACTORY_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "role": {"type": "string"},
        "objective": {"type": "string"},
        "instructions": {"type": "string"},
        "model_profile": {"type": "string", "enum": sorted(DEFAULT_ROUTES)},
        "allowed_tools": {"type": "array", "items": {"type": "string"}},
        "success_tests": {"type": "array", "items": {
            "type": "object",
            "properties": {"name": {"type": "string"}, "input": {"type": "string"},
                           "contains": {"type": "array", "items": {"type": "string"}}},
            "required": ["name", "input", "contains"], "additionalProperties": False}},
        "escalation": {"type": "string"},
    },
    "required": ["name", "role", "objective", "instructions", "model_profile",
                 "allowed_tools", "success_tests", "escalation"],
    "additionalProperties": False,
}


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40]
    return s if re.fullmatch(r"[a-z][a-z0-9_-]{1,63}", s or "") else "new-agent"


def factory_handler(ctx):
    """Draft an AgentSpec from a described gap. The result is only a DRAFT: the database
    refuses anything else from the worker, and the owner must validate, evaluate and
    activate it."""
    from .agents import validate_spec
    from .models import AgentSpec
    from .tools import all_tools

    gap = (ctx.input or {}).get("gap") or ctx.objective
    tools_list = [{"name": t.name, "category": t.category.value, "description": t.description}
                  for t in all_tools()]
    req = ModelRequest(
        system="You design narrowly scoped AI agent configurations for Jackson OS. Prefer "
               "the fewest tools that do the job. Agents hold no permissions; the system "
               "and owner approve restricted actions.",
        user=json.dumps({"gap": gap, "available_tools": tools_list}, indent=2),
        max_tokens=8000, json_schema=FACTORY_SCHEMA, effort="medium")
    resp = ctx.call_model("standard", req)
    d = json.loads(resp.text)
    name = d.get("name") if re.fullmatch(r"[a-z][a-z0-9_-]{1,63}", d.get("name") or "") \
        else _slug(gap)
    known = {t.name for t in all_tools()}
    tests = [{"name": t["name"], "input": t["input"], "expect": {"contains": t["contains"]}}
             for t in d.get("success_tests", []) if t.get("input")]
    with ctx.db_session() as db:
        latest = db.scalar(select(AgentSpec).where(
            AgentSpec.tenant_id == ctx.tenant_id, AgentSpec.name == name)
            .order_by(AgentSpec.version.desc()).limit(1))
        version = (db.scalar(select(func.max(AgentSpec.version)).where(
            AgentSpec.tenant_id == ctx.tenant_id, AgentSpec.name == name)) or 0) + 1
        spec = AgentSpec(
            tenant_id=ctx.tenant_id, business_id=ctx.business_id, name=name, version=version,
            role=d.get("role") or "", objective=d.get("objective") or gap,
            instructions=d.get("instructions") or "", model_profile=d.get("model_profile")
            if d.get("model_profile") in DEFAULT_ROUTES else "standard",
            allowed_tools=[t for t in d.get("allowed_tools", []) if t in known],
            success_tests=tests, output_checks={}, escalation=d.get("escalation") or "",
            status="draft", created_by="factory", source_ref=ctx.idempotency_key,
            previous_version_id=latest.id if latest else None)
        db.add(spec)
        db.flush()
        validation = validate_spec(spec)
        spec_id, version = str(spec.id), spec.version
        db.commit()
    return _result({"spec_id": spec_id, "name": name, "version": version, "status": "draft",
                    "mode": resp.mode, "validation": validation,
                    "dropped_unknown_tools": [t for t in d.get("allowed_tools", [])
                                              if t not in known]})


__all__ = ["AGENT_OUTPUT_SCHEMA", "agent_handler", "eval_handler", "factory_handler"]
