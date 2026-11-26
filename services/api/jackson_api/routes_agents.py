"""Agent registry API (phase 12): drafts, validation, sandbox evaluation, activation,
retirement, rollback, stats, direct runs, factory and the Growth OS import."""

import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from .agents import GROWTH_OS_AGENTS, MAX_ACTIVE_SPECS, validate_spec
from .deps import DB, TenantMember, TenantWriter
from .governance import audit
from .models import AgentSpec, ModelUsage, Task, ToolGrant
from .runs import CommandError, create_run

router = APIRouter()


class SpecIn(BaseModel):
    name: str = Field(min_length=2, max_length=64)
    role: str = Field(min_length=1, max_length=200)
    objective: str = Field(min_length=1, max_length=5000)
    instructions: str = Field(min_length=1, max_length=20_000)
    model_profile: str = "standard"
    allowed_tools: list[str] = Field(default_factory=list)
    business_id: uuid.UUID | None = None
    budget_cents_per_run: int = 100
    max_concurrency: int = 1
    max_runtime_s: int = 300
    success_tests: list[dict] = Field(default_factory=list)
    output_checks: dict = Field(default_factory=dict)
    escalation: str = ""


class SpecOut(BaseModel):
    model_config = {"from_attributes": True}
    id: uuid.UUID
    name: str
    version: int
    status: str
    role: str
    objective: str
    instructions: str
    model_profile: str
    allowed_tools: list[str]
    business_id: uuid.UUID | None
    budget_cents_per_run: int
    max_concurrency: int
    max_runtime_s: int
    success_tests: list[dict]
    output_checks: dict
    escalation: str
    created_by: str
    source_ref: str | None
    previous_version_id: uuid.UUID | None
    created_at: datetime


def _err(e: CommandError) -> HTTPException:
    return HTTPException(e.status, {"code": e.code, "message": e.message})


def _spec(db, tenant_id, spec_id) -> AgentSpec:
    s = db.scalar(select(AgentSpec).where(AgentSpec.id == spec_id,
                                          AgentSpec.tenant_id == tenant_id))
    if s is None:
        raise HTTPException(404, "agent not found")
    return s


def _latest_eval(db, spec: AgentSpec) -> dict | None:
    t = db.scalar(select(Task).where(Task.agent_spec_id == spec.id, Task.kind == "agent_eval",
                                     Task.status == "succeeded")
                  .order_by(Task.finished_at.desc()).limit(1))
    return t.output if t else None


def _new_version(db, tenant_id, user_id, body: dict, created_by: str,
                 source_ref: str | None = None) -> AgentSpec:
    latest = db.scalar(select(AgentSpec).where(AgentSpec.tenant_id == tenant_id,
                                               AgentSpec.name == body["name"])
                       .order_by(AgentSpec.version.desc()).limit(1))
    spec = AgentSpec(tenant_id=tenant_id, owner_user_id=user_id, created_by=created_by,
                     version=(latest.version + 1) if latest else 1, status="draft",
                     previous_version_id=latest.id if latest else None, source_ref=source_ref,
                     **body)
    db.add(spec)
    db.flush()
    return spec


def _out(db, spec) -> SpecOut:
    db.flush()
    db.refresh(spec)
    return SpecOut.model_validate(spec)


@router.get("/tenants/{tenant_id}/agents", response_model=list[SpecOut])
def list_agents(tenant_id: uuid.UUID, member: TenantMember, db: DB) -> list[AgentSpec]:
    return list(db.scalars(select(AgentSpec).where(AgentSpec.tenant_id == tenant_id)
                           .order_by(AgentSpec.name, AgentSpec.version.desc())))


@router.post("/tenants/{tenant_id}/agents", response_model=SpecOut, status_code=201)
def create_agent(tenant_id: uuid.UUID, body: SpecIn, writer: TenantWriter, db: DB) -> SpecOut:
    spec = _new_version(db, tenant_id, writer.user_id, body.model_dump(), "owner")
    audit(db, tenant_id, writer.user_id, "agent.drafted", "AgentSpec", spec.id,
          name=spec.name, version=spec.version)
    out = _out(db, spec)
    db.commit()
    return out


@router.get("/tenants/{tenant_id}/agents/{spec_id}")
def get_agent(tenant_id: uuid.UUID, spec_id: uuid.UUID, member: TenantMember, db: DB) -> dict:
    spec = _spec(db, tenant_id, spec_id)
    return {"spec": SpecOut.model_validate(spec).model_dump(mode="json"),
            "validation": validate_spec(spec), "latest_evaluation": _latest_eval(db, spec)}


@router.post("/tenants/{tenant_id}/agents/{spec_id}/evaluate", status_code=202)
def evaluate(tenant_id: uuid.UUID, spec_id: uuid.UUID, writer: TenantWriter, db: DB) -> dict:
    spec = _spec(db, tenant_id, spec_id)
    v = validate_spec(spec)
    if not v["ok"]:
        raise HTTPException(422, {"code": "invalid_spec", "errors": v["errors"]})
    payload = {"type": "SIMULATE", "objective": f"Evaluate agent {spec.name} v{spec.version}",
               "idempotency_key": f"eval-{spec.id}-{uuid.uuid4().hex[:8]}"}
    try:
        run, _ = create_run(db, tenant_id, writer.user_id, payload, first_task={
            "kind": "agent_eval", "title": f"Evaluate {spec.name} v{spec.version}",
            "agent_spec_id": spec.id, "tool_scope": [], "input": {"spec_id": str(spec.id)}})
    except CommandError as e:
        db.rollback()
        raise _err(e) from None
    run_id = run.id
    db.commit()
    return {"run_id": str(run_id), "status": "queued"}


@router.post("/tenants/{tenant_id}/agents/{spec_id}/activate", response_model=SpecOut)
def activate(tenant_id: uuid.UUID, spec_id: uuid.UUID, writer: TenantWriter, db: DB) -> SpecOut:
    if writer.role != "owner":
        raise HTTPException(403, "only the owner can activate agents")
    spec = _spec(db, tenant_id, spec_id)
    v = validate_spec(spec)
    if not v["ok"]:
        raise HTTPException(422, {"code": "invalid_spec", "errors": v["errors"]})
    ev = _latest_eval(db, spec)
    if not ev or not ev.get("passed"):
        raise HTTPException(409, {"code": "not_evaluated",
                                  "message": "this version needs a passing sandbox evaluation"})
    granted = {g.tool_name for g in db.scalars(select(ToolGrant).where(
        ToolGrant.tenant_id == tenant_id, ToolGrant.status == "active"))}
    missing = [t for t in spec.allowed_tools if t not in granted]
    if missing:  # an agent can never hold more than the owner has granted
        raise HTTPException(409, {"code": "tools_not_granted", "missing": missing})
    active = db.scalar(select(func.count()).select_from(AgentSpec).where(
        AgentSpec.tenant_id == tenant_id, AgentSpec.status == "active",
        AgentSpec.name != spec.name)) or 0
    if active >= MAX_ACTIVE_SPECS:
        raise HTTPException(409, {"code": "quota", "message": f"max {MAX_ACTIVE_SPECS} active"})
    for other in db.scalars(select(AgentSpec).where(
            AgentSpec.tenant_id == tenant_id, AgentSpec.name == spec.name,
            AgentSpec.status == "active", AgentSpec.id != spec.id)):
        other.status = "retired"
    spec.status = "active"
    audit(db, tenant_id, writer.user_id, "agent.activated", "AgentSpec", spec.id,
          name=spec.name, version=spec.version, evaluation_mode=ev.get("mode"))
    out = _out(db, spec)
    db.commit()
    return out


@router.post("/tenants/{tenant_id}/agents/{spec_id}/retire", response_model=SpecOut)
def retire(tenant_id: uuid.UUID, spec_id: uuid.UUID, writer: TenantWriter, db: DB) -> SpecOut:
    if writer.role != "owner":
        raise HTTPException(403, "only the owner can retire agents")
    spec = _spec(db, tenant_id, spec_id)
    spec.status = "retired"
    audit(db, tenant_id, writer.user_id, "agent.retired", "AgentSpec", spec.id)
    out = _out(db, spec)
    db.commit()
    return out


@router.post("/tenants/{tenant_id}/agents/{spec_id}/rollback", response_model=SpecOut)
def rollback(tenant_id: uuid.UUID, spec_id: uuid.UUID, writer: TenantWriter, db: DB) -> SpecOut:
    """Retire this version and re-activate the previous one (which must have passed)."""
    if writer.role != "owner":
        raise HTTPException(403, "only the owner can roll back agents")
    spec = _spec(db, tenant_id, spec_id)
    if spec.previous_version_id is None:
        raise HTTPException(409, "no previous version")
    prev = _spec(db, tenant_id, spec.previous_version_id)
    ev = _latest_eval(db, prev)
    if not ev or not ev.get("passed"):
        raise HTTPException(409, "previous version has no passing evaluation")
    spec.status, prev.status = "retired", "active"
    audit(db, tenant_id, writer.user_id, "agent.rolled_back", "AgentSpec", prev.id,
          from_version=spec.version, to_version=prev.version)
    out = _out(db, prev)
    db.commit()
    return out


@router.get("/tenants/{tenant_id}/agents/{spec_id}/stats")
def stats(tenant_id: uuid.UUID, spec_id: uuid.UUID, member: TenantMember, db: DB) -> dict:
    spec = _spec(db, tenant_id, spec_id)
    rows = db.execute(select(Task.status, func.count()).where(
        Task.agent_spec_id == spec.id, Task.kind == "agent").group_by(Task.status)).all()
    by = {s: n for s, n in rows}
    done = by.get("succeeded", 0) + by.get("failed", 0)
    cost = db.scalar(select(func.coalesce(func.sum(ModelUsage.cost_usd_micros), 0))
                     .join(Task, Task.id == ModelUsage.task_id)
                     .where(Task.agent_spec_id == spec.id, Task.kind == "agent")) or 0
    return {"tasks_by_status": by, "success_rate": (by.get("succeeded", 0) / done) if done
            else None, "model_cost_usd": int(cost) / 1e6}


class RunIn(BaseModel):
    objective: str = Field(min_length=1, max_length=5000)
    idempotency_key: str = Field(min_length=8, max_length=200)


@router.post("/tenants/{tenant_id}/agents/{spec_id}/run", status_code=201)
def run_agent(tenant_id: uuid.UUID, spec_id: uuid.UUID, body: RunIn, writer: TenantWriter,
              db: DB) -> dict:
    spec = _spec(db, tenant_id, spec_id)
    if spec.status != "active":
        raise HTTPException(409, "agent is not active")
    payload = {"type": "BUILD", "objective": body.objective,
               "idempotency_key": body.idempotency_key, "business_id": spec.business_id}
    try:
        run, created = create_run(db, tenant_id, writer.user_id, payload, first_task={
            "kind": "agent", "title": f"{spec.name}: {body.objective[:250]}",
            "agent_spec_id": spec.id, "tool_scope": list(spec.allowed_tools),
            "input": {"objective": body.objective}})
    except CommandError as e:
        db.rollback()
        raise _err(e) from None
    run_id = run.id
    db.commit()
    return {"run_id": str(run_id), "created": created}


class FactoryIn(BaseModel):
    gap: str = Field(min_length=10, max_length=5000)
    business_id: uuid.UUID | None = None


@router.post("/tenants/{tenant_id}/agents/factory", status_code=202)
def factory(tenant_id: uuid.UUID, body: FactoryIn, writer: TenantWriter, db: DB) -> dict:
    payload = {"type": "BUILD", "objective": f"Draft an agent for: {body.gap[:200]}",
               "idempotency_key": f"factory-{uuid.uuid4().hex}", "business_id": body.business_id}
    try:
        run, _ = create_run(db, tenant_id, writer.user_id, payload, first_task={
            "kind": "agent_factory", "title": "Draft agent spec", "tool_scope": [],
            "input": {"gap": body.gap}})
    except CommandError as e:
        db.rollback()
        raise _err(e) from None
    run_id = run.id
    db.commit()
    return {"run_id": str(run_id), "status": "queued"}


@router.post("/tenants/{tenant_id}/agents/import-growth-os", response_model=list[SpecOut])
def import_growth_os(tenant_id: uuid.UUID, writer: TenantWriter, db: DB) -> list[SpecOut]:
    """Bring Growth OS's six agents over as DRAFTS (ADR-008). Idempotent per name."""
    if writer.role != "owner":
        raise HTTPException(403, "only the owner can import agents")
    out = []
    for name, role, objective, tools, note in GROWTH_OS_AGENTS:
        if db.scalar(select(AgentSpec.id).where(AgentSpec.tenant_id == tenant_id,
                                                AgentSpec.name == name)) is not None:
            continue
        spec = _new_version(db, tenant_id, writer.user_id, {
            "name": name, "role": role, "objective": objective,
            "instructions": f"{objective} Cite sources for any fact. Mark guesses as guesses. "
                            f"Note: {note}.",
            "model_profile": "standard", "allowed_tools": tools,
            "success_tests": [{"name": "basic", "input": f"Give one example task for: {role}",
                               "expect": {"min_chars": 1}}],
            "escalation": "Stop and ask the owner when unsure or blocked."},
            "import", source_ref="base44:growth-os")
        out.append(_out(db, spec))
    audit(db, tenant_id, writer.user_id, "agents.imported", "Tenant", tenant_id,
          count=len(out), source="base44:growth-os")
    db.commit()
    return out
