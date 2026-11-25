"""Memory with provenance (spec vol. 01, phase 09, ADR-019).

Rules:
- Every record carries its source (owner, run, tool or import + reference), a timestamp,
  an access scope, an optional retention date, and an epistemic status
  (fact / inference / hypothesis).
- Same `key`, different content:
    * low impact, written by a person -> the new record supersedes the old one;
    * written by an agent -> always waits for an owner (agents never overwrite);
    * high impact -> the new record waits as `pending_confirmation` until an owner/admin
      confirms it ("require confirmation for conflicting high-impact facts").
- Corrections create a new version; the old one is kept as `superseded`.
- Deletion removes a record AND every version of it. The audit log keeps only the id.
- Recall returns only active, unexpired records, each with a citation.
- Agents recall `tenant`-scope memories only; `owner_only` stays with the owner.
"""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from .models import Business, MemoryRecord
from .runs import CommandError


def citation(rec: MemoryRecord) -> dict:
    return {
        "memory_id": str(rec.id),
        "source_type": rec.source_type,
        "source_ref": rec.source_ref,
        "epistemic": rec.epistemic,
        "confidence": rec.confidence,
        "recorded_at": rec.created_at.isoformat() if rec.created_at else None,
    }


def _check_business(db: Session, tenant_id: uuid.UUID, business_id: uuid.UUID | None) -> None:
    if business_id is not None and db.scalar(select(Business.id).where(
            Business.id == business_id, Business.tenant_id == tenant_id)) is None:
        raise CommandError(422, "unknown_business", "business not found in this tenant")


def write_memory(
    db: Session, *, tenant_id: uuid.UUID, kind: str, epistemic: str, content: str,
    source_type: str, source_ref: str | None = None, source_detail: dict | None = None,
    key: str | None = None, business_id: uuid.UUID | None = None, impact: str = "low",
    confidence: float = 1.0, access_scope: str = "tenant", retain_days: int | None = None,
    user_id: uuid.UUID | None = None, agent: str | None = None,
) -> MemoryRecord:
    """Agents (agent=...) never overwrite: any conflict they cause waits for an owner.
    That also means the worker DB role needs no UPDATE right on memories at all."""
    _check_business(db, tenant_id, business_id)
    content = content.strip()
    if not content:
        raise CommandError(422, "empty_memory", "content is required")
    status, supersedes = "active", None
    if key:
        q = select(MemoryRecord).where(
            MemoryRecord.tenant_id == tenant_id, MemoryRecord.key == key,
            MemoryRecord.business_id.is_(None) if business_id is None
            else MemoryRecord.business_id == business_id,
            MemoryRecord.status == "active")
        current = db.scalar(q if agent else q.with_for_update())
        if current is not None:
            if current.content == content and current.epistemic == epistemic:
                return current  # nothing new: no duplicate row
            supersedes = current.id
            if agent or impact == "high" or current.impact == "high":
                status = "pending_confirmation"
            else:
                current.status = "superseded"
    rec = MemoryRecord(
        tenant_id=tenant_id, business_id=business_id, kind=kind, epistemic=epistemic,
        key=key, content=content, impact=impact, confidence=confidence,
        source_type=source_type, source_ref=source_ref, source_detail=source_detail or {},
        access_scope=access_scope, status=status, supersedes_id=supersedes,
        created_by_user=user_id, created_by_agent=agent,
        retain_until=(datetime.now(UTC) + timedelta(days=retain_days)) if retain_days else None,
    )
    db.add(rec)
    db.flush()
    return rec


def confirm(db: Session, rec: MemoryRecord, accept: bool) -> MemoryRecord:
    if rec.status != "pending_confirmation":
        raise CommandError(409, "not_pending", f"memory is {rec.status}")
    if not accept:
        rec.status = "rejected"
        return rec
    if rec.supersedes_id:
        old = db.get(MemoryRecord, rec.supersedes_id)
        if old is not None and old.status == "active":
            old.status = "superseded"
    rec.status = "active"
    return rec


def correct(db: Session, rec: MemoryRecord, user_id: uuid.UUID, content: str,
            epistemic: str | None = None) -> MemoryRecord:
    if rec.status not in ("active", "pending_confirmation"):
        raise CommandError(409, "not_current", f"memory is {rec.status}; correct the latest")
    rec.status = "superseded"
    new = MemoryRecord(
        tenant_id=rec.tenant_id, business_id=rec.business_id, kind=rec.kind,
        epistemic=epistemic or rec.epistemic, key=rec.key, content=content.strip(),
        impact=rec.impact, confidence=1.0, source_type="owner", source_ref=None,
        source_detail={"correction_of": str(rec.id)}, access_scope=rec.access_scope,
        status="active", supersedes_id=rec.id, created_by_user=user_id,
        retain_until=rec.retain_until,
    )
    db.add(new)
    db.flush()
    return new


def history(db: Session, rec: MemoryRecord) -> list[MemoryRecord]:
    """All versions of this memory, oldest first."""
    ids, frontier = {rec.id}, [rec.id]
    while frontier:  # walk both directions of the supersedes chain
        rows = db.scalars(select(MemoryRecord).where(or_(
            MemoryRecord.supersedes_id.in_(frontier),
            MemoryRecord.id.in_(select(MemoryRecord.supersedes_id).where(
                MemoryRecord.id.in_(frontier)))))).all()
        frontier = [r.id for r in rows if r.id not in ids]
        ids.update(frontier)
    return sorted(db.scalars(select(MemoryRecord).where(MemoryRecord.id.in_(ids))),
                  key=lambda r: r.created_at)


def delete_memory(db: Session, rec: MemoryRecord) -> list[uuid.UUID]:
    ids = [r.id for r in history(db, rec)]
    db.execute(delete(MemoryRecord).where(MemoryRecord.id.in_(ids)))
    return ids


def recall(
    db: Session, tenant_id: uuid.UUID, query: str | None = None, *,
    business_id: uuid.UUID | None = None, kinds: list[str] | None = None,
    include_owner_only: bool = False, limit: int = 10,
) -> list[MemoryRecord]:
    q = select(MemoryRecord).where(
        MemoryRecord.tenant_id == tenant_id,
        MemoryRecord.status == "active",
        or_(MemoryRecord.retain_until.is_(None), MemoryRecord.retain_until > func.now()),
    )
    if not include_owner_only:
        q = q.where(MemoryRecord.access_scope == "tenant")
    if business_id is not None:  # this business's memories plus tenant-wide ones
        q = q.where(or_(MemoryRecord.business_id == business_id,
                        MemoryRecord.business_id.is_(None)))
    if kinds:
        q = q.where(MemoryRecord.kind.in_(kinds))
    if query and query.strip():
        doc = func.to_tsvector("english", MemoryRecord.content)
        # OR the query words together: recall should surface anything relevant.
        terms = [w for w in "".join(c if c.isalnum() else " " for c in query).split() if w]
        tsq = func.to_tsquery("english", " | ".join(terms)) if terms else None
        if tsq is not None:
            q = q.where(doc.op("@@")(tsq)).order_by(func.ts_rank(doc, tsq).desc(),
                                                    MemoryRecord.created_at.desc())
    else:
        q = q.order_by(MemoryRecord.created_at.desc())
    return list(db.scalars(q.limit(limit)))


def purge_expired(db: Session) -> int:
    """Hard-delete records past their retention date. Returns how many were removed."""
    res = db.execute(delete(MemoryRecord).where(MemoryRecord.retain_until <= func.now()))
    return res.rowcount or 0
