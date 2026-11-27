"""Business lines API (ADR-039): every line's department, agents (real and proposed),
playbook, KPIs from real data, connectors (LIVE/MOCK/not built), launch template and
budget vs. spend. Read by the City's building panel and by Jackson's planning."""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import city
from . import connections as C
from .deps import DB, TenantMember, TenantWriter
from .governance import audit
from .lines import BY_KEY, LINES, Line, kpis
from .models import (
    Agent,
    AgentSpec,
    Business,
    Department,
    Launch,
    LedgerEntry,
    LinePlaybook,
    ModelUsage,
    WorkflowRun,
)

router = APIRouter()


def ensure_playbook(db: Session, tenant_id, ln: Line) -> LinePlaybook:
    pb = db.scalar(select(LinePlaybook).where(LinePlaybook.tenant_id == tenant_id,
                                              LinePlaybook.line == ln.key))
    if pb is None:
        pb = LinePlaybook(tenant_id=tenant_id, line=ln.key, version=1,
                          body={"summary": ln.summary, "steps": ln.playbook, "rules": ln.rules},
                          budget_monthly_cents=ln.budget_monthly_cents)
        db.add(pb)
        db.flush()
    return pb


def connectors_for(ln: Line) -> list[dict]:
    out = []
    for cid in ln.connectors:
        c = C.BY_ID[cid]
        st = C.status(c, None)
        out.append({"id": cid, "name": c.name, "mode": st["mode"], "missing": st["missing"],
                    "built": st["built"]})
    for var, why in ln.needs_not_built:
        out.append({"id": var.lower(), "name": why, "mode": "MOCK", "missing": [var],
                    "built": False})
    return out


def spend_this_month(db: Session, tenant_id, key: str) -> float:
    month = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    runs = select(WorkflowRun.id).join(Business, Business.id == WorkflowRun.business_id).where(
        WorkflowRun.tenant_id == tenant_id, Business.line == key)
    ledger = int(db.scalar(select(func.coalesce(func.sum(LedgerEntry.amount_cents), 0)).where(
        LedgerEntry.run_id.in_(runs), LedgerEntry.kind == "commit",
        LedgerEntry.created_at >= month)) or 0) / 100
    model = int(db.scalar(select(func.coalesce(func.sum(ModelUsage.cost_usd_micros), 0)).where(
        ModelUsage.run_id.in_(runs), ModelUsage.created_at >= month)) or 0) / 1e6
    return round(ledger + model, 4)


def line_view(db: Session, tenant_id, ln: Line) -> dict:
    city.ensure_registry(db, tenant_id)
    pb = ensure_playbook(db, tenant_id, ln)
    dept = db.scalar(select(Department).where(Department.tenant_id == tenant_id,
                                              Department.key == ln.key))
    agents = db.scalars(select(Agent).where(Agent.tenant_id == tenant_id,
                                            Agent.department_id == dept.id)
                        .order_by(Agent.name)).all() if dept else []
    biz = db.scalars(select(Business).where(Business.tenant_id == tenant_id,
                                            Business.line == ln.key)
                     .order_by(Business.name)).all()
    biz_ids = [b.id for b in biz]
    proposed = db.scalars(select(AgentSpec).where(
        AgentSpec.tenant_id == tenant_id, AgentSpec.status == "draft",
        (AgentSpec.source_ref == f"line:{ln.key}") | AgentSpec.business_id.in_(biz_ids or
                                                                               [uuid.uuid4()])))
    launches = db.scalars(select(Launch).where(Launch.tenant_id == tenant_id,
                                               Launch.business_id.in_(biz_ids or
                                                                      [uuid.uuid4()])))
    conn = connectors_for(ln)
    return {
        "key": ln.key, "name": ln.name, "color": ln.color, "summary": ln.summary,
        "department_id": str(dept.id) if dept else None,
        "staffed": bool(agents),
        "businesses": [{"id": str(b.id), "name": b.name, "kind": b.kind} for b in biz],
        "agents": [{"id": str(a.id), "name": a.name, "role": a.role, "status": a.status}
                   for a in agents],
        "proposed_agents": [{"id": str(p.id), "name": p.name, "role": p.role,
                             "status": "proposed"} for p in proposed],
        "playbook": {"version": pb.version, **pb.body},
        "kpis": kpis(db, tenant_id, ln.key),
        "connectors": conn,
        "missing_variables": sorted({v for c in conn if c["mode"] != "LIVE"
                                     for v in c["missing"]}),
        "budget": {"monthly_cents": pb.budget_monthly_cents,
                   "spent_this_month_usd": spend_this_month(db, tenant_id, ln.key)},
        "launch": {"template": ln.launch,
                   "how": "Jackson proposes one Launch for this line's business: budget + "
                          "daily cap, duration, channels and stop rules. Steady approves "
                          "once; inside that scope Jackson runs it alone, anything outside "
                          "still comes to the inbox.",
                   "active": sum(1 for x in launches if x.status == "active")},
    }


@router.get("/tenants/{tenant_id}/lines")
def list_lines(tenant_id: uuid.UUID, member: TenantMember, db: DB) -> list[dict]:
    out = [line_view(db, tenant_id, ln) for ln in LINES]
    db.commit()  # keeps playbooks/registry rows created on first view
    return out


@router.get("/tenants/{tenant_id}/lines/{key}")
def get_line(tenant_id: uuid.UUID, key: str, member: TenantMember, db: DB) -> dict:
    ln = BY_KEY.get(key)
    if ln is None:
        raise HTTPException(404, "unknown business line")
    out = line_view(db, tenant_id, ln)
    db.commit()
    return out


class PlaybookIn(BaseModel):
    summary: str | None = Field(default=None, max_length=2000)
    steps: list[str] | None = Field(default=None, max_length=30)
    rules: list[str] | None = Field(default=None, max_length=30)
    budget_monthly_cents: int | None = Field(default=None, ge=0, le=100_000_000)


def _owner_admin(m) -> None:
    if m.role not in ("owner", "admin"):
        raise HTTPException(403, "only the owner or an admin can change a line")


@router.patch("/tenants/{tenant_id}/lines/{key}/playbook")
def edit_playbook(tenant_id: uuid.UUID, key: str, body: PlaybookIn, writer: TenantWriter,
                  db: DB) -> dict:
    _owner_admin(writer)
    ln = BY_KEY.get(key)
    if ln is None:
        raise HTTPException(404, "unknown business line")
    pb = ensure_playbook(db, tenant_id, ln)
    before = {"version": pb.version, **pb.body, "budget": pb.budget_monthly_cents}
    new = dict(pb.body)
    for k in ("summary", "steps", "rules"):
        if getattr(body, k) is not None:
            new[k] = getattr(body, k)
    pb.body = new
    if body.budget_monthly_cents is not None:
        pb.budget_monthly_cents = body.budget_monthly_cents
    pb.version += 1
    pb.updated_by = writer.user_id
    db.flush()
    audit(db, tenant_id, writer.user_id, "line.playbook.edited", "LinePlaybook", pb.id,
          line=key, before=before,
          after={"version": pb.version, **pb.body, "budget": pb.budget_monthly_cents})
    out = {"line": key, "version": pb.version, **pb.body,
           "budget_monthly_cents": pb.budget_monthly_cents}
    db.commit()
    return out


class BusinessLineIn(BaseModel):
    line: str | None


@router.patch("/tenants/{tenant_id}/businesses/{business_id}/line")
def set_business_line(tenant_id: uuid.UUID, business_id: uuid.UUID, body: BusinessLineIn,
                      writer: TenantWriter, db: DB) -> dict:
    _owner_admin(writer)
    if body.line is not None and body.line not in BY_KEY:
        raise HTTPException(422, "unknown business line")
    b = db.get(Business, business_id)
    if b is None or b.tenant_id != tenant_id:
        raise HTTPException(404, "business not found")
    before = b.line
    b.line = body.line
    audit(db, tenant_id, writer.user_id, "business.line", "Business", b.id, before=before,
          after=body.line)
    db.commit()
    return {"business_id": str(business_id), "line": body.line}
