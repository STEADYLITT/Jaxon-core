"""Connections panel API (ADR-038). Owner/admin only. Key values never leave the server."""

import uuid

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from . import connections as C
from .deps import DB, TenantWriter
from .governance import audit
from .models import AuditEvent, User

router = APIRouter()


def _admin(m) -> None:
    if m.role not in ("owner", "admin"):
        raise HTTPException(403, "only the owner or an admin can see and test connections")


def _entity(cid: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"jackson-connector:{cid}")


def _last_tests(db, tenant_id) -> dict:
    out: dict = {}
    for e in db.scalars(select(AuditEvent).where(
            AuditEvent.tenant_id == tenant_id, AuditEvent.action == "connector.tested")
            .order_by(AuditEvent.created_at.desc()).limit(200)):
        cid = (e.data or {}).get("connector")
        if cid and cid not in out:
            out[cid] = {**{k: v for k, v in (e.data or {}).items() if k != "connector"},
                        "at": e.created_at.isoformat()}
    return out


@router.get("/tenants/{tenant_id}/admin/connectors")
def list_connectors(tenant_id: uuid.UUID, writer: TenantWriter, db: DB) -> list[dict]:
    """LIVE / MOCK / ERROR per outside service, which variable names are missing, and the
    last test. Never returns a key's value."""
    _admin(writer)
    last = _last_tests(db, tenant_id)
    return [C.status(c, last.get(c.id)) for c in C.CONNECTORS]


@router.post("/tenants/{tenant_id}/admin/connectors/{cid}/test")
def test_connector(tenant_id: uuid.UUID, cid: str, writer: TenantWriter, db: DB) -> dict:
    """One real, harmless call to the service (see connections.py). Audited."""
    _admin(writer)
    c = C.BY_ID.get(cid)
    if c is None:
        raise HTTPException(404, "unknown connector")
    email = db.scalar(select(User.email).where(User.id == writer.user_id))
    res = C.run_test(c, {"owner_email": email, "tenant_id": tenant_id})
    audit(db, tenant_id, writer.user_id, "connector.tested", "Connector", _entity(cid),
          connector=cid, ok=res["ok"], ms=res.get("ms"),
          summary=res.get("result") if res["ok"] else None, error=res.get("error"))
    db.flush()
    out = {**res, "status": C.status(c, _last_tests(db, tenant_id).get(cid))}
    db.commit()  # after reading: the tenant scope ends with the transaction
    return out
