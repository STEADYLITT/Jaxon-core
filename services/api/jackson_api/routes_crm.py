"""CRM API (phase 18): contacts, consent, opt-outs, campaigns and the public unsubscribe
page."""

import html
import json
import os
import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from .crm import (
    address_hash,
    dedupe_key,
    norm_email,
    norm_phone,
    read_unsubscribe_token,
    score_lead,
)
from .db import bind_tenant
from .deps import DB, TenantMember, TenantWriter
from .governance import audit
from .models import (
    Approval,
    Contact,
    LaunchSignal,
    OutreachCampaign,
    OutreachMessage,
    Suppression,
    Task,
)
from .policy import Action, Category
from .runs import CommandError, create_run

router = APIRouter()
Source = Literal["inbound", "referral", "existing_customer", "event", "public_business_listing"]
Basis = Literal["consent", "legitimate_interest", "existing_relationship"]
Stage = Literal["new", "contacted", "replied", "meeting", "proposal", "won", "lost"]


class ContactIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    company: str = Field(default="", max_length=200)
    email: str | None = None
    phone: str | None = None
    region: str = Field(default="", max_length=16)
    timezone: str = Field(default="UTC", max_length=64)
    industry: str = Field(default="", max_length=100)
    source_type: Source
    source_ref: str = Field(min_length=3, max_length=500,
                            description="where this lead came from (URL, event, referrer)")
    lawful_basis: Basis
    business_id: uuid.UUID | None = None
    notes: str = Field(default="", max_length=5000)


class ContactOut(BaseModel):
    model_config = {"from_attributes": True}
    id: uuid.UUID
    business_id: uuid.UUID | None
    name: str
    company: str
    email: str | None
    phone: str | None
    region: str
    timezone: str
    industry: str
    source_type: str
    source_ref: str
    lawful_basis: str
    sms_consent_at: datetime | None
    call_consent_at: datetime | None
    stage: str
    score: int
    score_detail: dict
    notes: str
    created_at: datetime


def _contact(db, tenant_id, cid) -> Contact:
    c = db.get(Contact, cid)
    if c is None or c.tenant_id != tenant_id:
        raise HTTPException(404, "contact not found")
    return c


def _build_contact(tenant_id, body: ContactIn, icp: dict | None) -> Contact:
    email, phone = norm_email(body.email), norm_phone(body.phone)
    if body.email and not email:
        raise ValueError(f"invalid email '{body.email}'")
    if body.phone and not phone:
        raise ValueError(f"invalid phone '{body.phone}'")
    data = body.model_dump()
    data.update(email=email, phone=phone)
    sc = score_lead(data, icp)
    return Contact(tenant_id=tenant_id, **data, stage="new", score=sc["score"],
                   score_detail=sc, dedupe_key=dedupe_key(body.name, body.company, email, phone))


@router.post("/tenants/{tenant_id}/contacts", status_code=201, response_model=ContactOut)
def create_contact(tenant_id: uuid.UUID, body: ContactIn, writer: TenantWriter, db: DB):
    try:
        c = _build_contact(tenant_id, body, None)
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    db.add(c)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "a contact with this email/phone/name already exists") \
            from None
    out = ContactOut.model_validate(c)
    db.commit()
    return out


class ImportIn(BaseModel):
    contacts: list[ContactIn] = Field(max_length=5000)
    icp: dict = Field(default_factory=dict, description="{industries: [], regions: []}")


@router.post("/tenants/{tenant_id}/contacts/import")
def import_contacts(tenant_id: uuid.UUID, body: ImportIn, writer: TenantWriter, db: DB):
    """Bulk import: validates, dedupes (within the file and against existing contacts)
    and scores. Rejected rows come back with the reason."""
    created, duplicates, rejected = 0, 0, []
    existing = set(db.scalars(select(Contact.dedupe_key).where(Contact.tenant_id == tenant_id)))
    for i, row in enumerate(body.contacts):
        try:
            c = _build_contact(tenant_id, row, body.icp)
        except ValueError as e:
            rejected.append({"row": i, "reason": str(e)})
            continue
        if c.dedupe_key in existing:
            duplicates += 1
            continue
        existing.add(c.dedupe_key)
        db.add(c)
        created += 1
    audit(db, tenant_id, writer.user_id, "contacts.imported", "Contact", None,
          created=created, duplicates=duplicates, rejected=len(rejected))
    db.commit()
    return {"created": created, "duplicates": duplicates, "rejected": rejected}


@router.get("/tenants/{tenant_id}/contacts", response_model=list[ContactOut])
def list_contacts(tenant_id: uuid.UUID, member: TenantMember, db: DB,
                  stage: Stage | None = None, min_score: int = 0):
    q = select(Contact).where(Contact.tenant_id == tenant_id, Contact.score >= min_score)
    if stage:
        q = q.where(Contact.stage == stage)
    return list(db.scalars(q.order_by(Contact.score.desc(), Contact.created_at).limit(500)))


class ContactPatch(BaseModel):
    stage: Stage | None = None
    notes: str | None = Field(default=None, max_length=5000)


@router.patch("/tenants/{tenant_id}/contacts/{cid}", response_model=ContactOut)
def update_contact(tenant_id: uuid.UUID, cid: uuid.UUID, body: ContactPatch,
                   writer: TenantWriter, db: DB):
    c = _contact(db, tenant_id, cid)
    if body.stage and body.stage != c.stage:
        audit(db, tenant_id, writer.user_id, "contact.stage_changed", "Contact", c.id,
              old=c.stage, new=body.stage)
        c.stage = body.stage
    if body.notes is not None:
        c.notes = body.notes
    out = ContactOut.model_validate(c)
    db.commit()
    return out


class ConsentIn(BaseModel):
    channel: Literal["sms", "call"]
    evidence: str = Field(min_length=10, max_length=1000,
                          description="how and when the contact agreed (form, call recording)")


@router.post("/tenants/{tenant_id}/contacts/{cid}/consent", response_model=ContactOut)
def record_consent(tenant_id: uuid.UUID, cid: uuid.UUID, body: ConsentIn,
                   writer: TenantWriter, db: DB):
    """Record prior express consent for SMS or calls, with evidence (kept in the audit
    log). Without it, the trigger refuses to send SMS or place calls."""
    c = _contact(db, tenant_id, cid)
    if body.channel == "sms":
        c.sms_consent_at = func.now()
    else:
        c.call_consent_at = func.now()
    audit(db, tenant_id, writer.user_id, f"contact.{body.channel}_consent", "Contact", c.id,
          evidence=body.evidence)
    db.flush()
    db.refresh(c)
    out = ContactOut.model_validate(c)
    db.commit()
    return out


def _suppress(db, tenant_id, channel: str, address: str, reason: str) -> None:
    h = address_hash(address)
    if db.scalar(select(Suppression.id).where(Suppression.tenant_id == tenant_id,
                                              Suppression.channel == channel,
                                              Suppression.value_hash == h)) is None:
        db.add(Suppression(tenant_id=tenant_id, channel=channel, value_hash=h, reason=reason))


@router.delete("/tenants/{tenant_id}/contacts/{cid}", status_code=204)
def delete_contact(tenant_id: uuid.UUID, cid: uuid.UUID, writer: TenantWriter, db: DB):
    """Delete a contact (e.g. a privacy request). Their addresses are suppressed first,
    as hashes, so they're never contacted again even if re-imported."""
    c = _contact(db, tenant_id, cid)
    for addr in (c.email, c.phone):
        if addr:
            _suppress(db, tenant_id, "all", addr, "deleted")
    audit(db, tenant_id, writer.user_id, "contact.deleted", "Contact", c.id)
    db.delete(c)
    db.commit()


class SuppressIn(BaseModel):
    address: str = Field(min_length=3, max_length=320)
    channel: Literal["email", "sms", "call", "all"] = "all"


@router.post("/tenants/{tenant_id}/suppressions", status_code=201)
def add_suppression(tenant_id: uuid.UUID, body: SuppressIn, writer: TenantWriter, db: DB):
    addr = norm_email(body.address) or norm_phone(body.address)
    if not addr:
        raise HTTPException(422, "not an email address or phone number")
    _suppress(db, tenant_id, body.channel, addr, "owner")
    audit(db, tenant_id, writer.user_id, "suppression.added", "Suppression", None,
          channel=body.channel)
    db.commit()
    return {"suppressed": True, "channel": body.channel}


# --- Campaigns ----------------------------------------------------------------------------------

class CampaignIn(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    channel: Literal["email", "sms", "call"]
    sender_name: str = Field(min_length=2, max_length=200)
    sender_email: str = Field(default="", max_length=320)
    postal_address: str = Field(default="", max_length=500)
    subject_template: str = Field(default="", max_length=300)
    body_template: str = Field(min_length=10, max_length=20_000)
    audience: dict = Field(default_factory=dict,
                           description="{stages: [], min_score: 0, list: 'name'}")
    daily_limit: int = Field(default=50, ge=1, le=500)
    business_id: uuid.UUID | None = None
    launch_id: uuid.UUID | None = Field(
        default=None, description="send under this Launch approval if it names the list")


class CampaignOut(BaseModel):
    model_config = {"from_attributes": True}
    id: uuid.UUID
    name: str
    channel: str
    sender_name: str
    sender_email: str
    postal_address: str
    subject_template: str
    body_template: str
    audience: dict
    daily_limit: int
    status: str
    approved_by: uuid.UUID | None
    approved_at: datetime | None
    created_at: datetime
    counts: dict = {}


def _campaign(db, tenant_id, cid) -> OutreachCampaign:
    k = db.get(OutreachCampaign, cid)
    if k is None or k.tenant_id != tenant_id:
        raise HTTPException(404, "campaign not found")
    return k


def _counts(db, cid) -> dict:
    rows = db.execute(select(OutreachMessage.status, func.count())
                      .where(OutreachMessage.campaign_id == cid)
                      .group_by(OutreachMessage.status)).all()
    return {s: n for s, n in rows}


def _cout(db, k) -> CampaignOut:
    out = CampaignOut.model_validate(k)
    out.counts = _counts(db, k.id)
    return out


@router.post("/tenants/{tenant_id}/campaigns", status_code=201, response_model=CampaignOut)
def create_campaign(tenant_id: uuid.UUID, body: CampaignIn, writer: TenantWriter, db: DB):
    if body.launch_id is not None:
        from .models import Launch

        la = db.get(Launch, body.launch_id)
        if la is None or la.tenant_id != tenant_id or la.business_id != body.business_id:
            raise HTTPException(422, "launch not found for this business")
    k = OutreachCampaign(tenant_id=tenant_id, status="draft", **body.model_dump())
    db.add(k)
    db.flush()
    out = _cout(db, k)
    db.commit()
    return out


@router.get("/tenants/{tenant_id}/campaigns/{cid}", response_model=CampaignOut)
def get_campaign(tenant_id: uuid.UUID, cid: uuid.UUID, member: TenantMember, db: DB):
    return _cout(db, _campaign(db, tenant_id, cid))


@router.get("/tenants/{tenant_id}/campaigns/{cid}/messages")
def campaign_messages(tenant_id: uuid.UUID, cid: uuid.UUID, member: TenantMember, db: DB):
    _campaign(db, tenant_id, cid)
    rows = db.scalars(select(OutreachMessage).where(OutreachMessage.campaign_id == cid)
                      .order_by(OutreachMessage.created_at)).all()
    return [{"id": str(m.id), "contact_id": str(m.contact_id) if m.contact_id else None,
             "to": m.to_address, "subject": m.subject, "body": m.body, "status": m.status,
             "review": m.review, "external_id": m.external_id, "sent_at": m.sent_at}
            for m in rows]


def _start(db, tenant_id, user_id, k, kind, title, key, objective):
    try:
        run, _ = create_run(db, tenant_id, user_id, {
            "type": "EXECUTE" if k.business_id else "BUILD", "objective": objective,
            "idempotency_key": key, "business_id": k.business_id},
            first_task={"kind": kind, "title": title, "tool_scope": [],
                        "input": {"campaign_id": str(k.id)}})
    except CommandError as e:
        db.rollback()
        raise HTTPException(e.status, {"code": e.code, "message": e.message}) from None
    return run


class KeyIn(BaseModel):
    idempotency_key: str = Field(min_length=8, max_length=200)


@router.post("/tenants/{tenant_id}/campaigns/{cid}/prepare", status_code=202)
def prepare(tenant_id: uuid.UUID, cid: uuid.UUID, body: KeyIn, writer: TenantWriter, db: DB):
    k = _campaign(db, tenant_id, cid)
    if k.status != "draft":
        raise HTTPException(409, f"campaign is {k.status}")
    run = _start(db, tenant_id, writer.user_id, k, "outreach_prepare", "Prepare messages",
                 body.idempotency_key, f"Prepare campaign: {k.name}")
    run_id = run.id
    db.commit()
    return {"run_id": str(run_id)}


@router.post("/tenants/{tenant_id}/campaigns/{cid}/approve", status_code=202)
def approve_and_send(tenant_id: uuid.UUID, cid: uuid.UUID, body: KeyIn, writer: TenantWriter,
                     db: DB):
    """The owner reviewed the prepared messages and says send. This click is recorded as
    the outbound_campaign approval for exactly this send task."""
    k = _campaign(db, tenant_id, cid)
    if k.status != "prepared":
        raise HTTPException(409, f"campaign is {k.status}; prepare it and review first")
    if not _counts(db, k.id).get("queued"):
        raise HTTPException(409, "no messages passed review")
    k.status, k.approved_by, k.approved_at = "approved", writer.user_id, func.now()
    run = _start(db, tenant_id, writer.user_id, k, "outreach_send", "Send campaign",
                 body.idempotency_key, f"Send campaign: {k.name}")
    task = db.scalar(select(Task).where(Task.run_id == run.id))
    action = Action(Category.OUTBOUND_CAMPAIGN, f"send campaign {k.id}")
    db.add(Approval(tenant_id=tenant_id, run_id=run.id, task_id=task.id,
                    category=action.category.value, description=action.description,
                    amount_cents=0, fingerprint=action.fingerprint(task.id),
                    reason="owner approved the prepared campaign", status="approved",
                    decided_by=writer.user_id, decided_at=func.now(),
                    decision_note="approved after reviewing prepared messages"))
    audit(db, tenant_id, writer.user_id, "campaign.approved", "OutreachCampaign", k.id,
          queued=_counts(db, k.id).get("queued"))
    run_id = run.id
    db.commit()
    return {"run_id": str(run_id)}


@router.post("/tenants/{tenant_id}/campaigns/{cid}/cancel", response_model=CampaignOut)
def cancel(tenant_id: uuid.UUID, cid: uuid.UUID, writer: TenantWriter, db: DB):
    """Stops sending at the next message; already-sent messages can't be unsent."""
    k = _campaign(db, tenant_id, cid)
    if k.status in ("completed", "cancelled"):
        raise HTTPException(409, f"campaign is {k.status}")
    k.status = "cancelled"
    audit(db, tenant_id, writer.user_id, "campaign.cancelled", "OutreachCampaign", k.id)
    out = _cout(db, k)
    db.commit()
    return out


# --- Public unsubscribe (no login: the signed token is the credential) ---------------------------

PAGE = ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" "
        "content=\"width=device-width, initial-scale=1\"><title>{t}</title></head><body>"
        "<main><h1>{t}</h1>{b}</main></body></html>")


@router.get("/u/{token}", response_class=HTMLResponse, include_in_schema=False)
def unsubscribe_page(token: str):
    """GET never changes anything (link scanners follow links); the button POSTs."""
    if read_unsubscribe_token(token) is None:
        return HTMLResponse(PAGE.format(t="Link not valid", b="<p>This link is not valid."
                                        "</p>"), status_code=404)
    return HTMLResponse(PAGE.format(t="Unsubscribe", b=(
        f'<form method="post" action="/u/{html.escape(token)}">'
        '<p>Stop all further messages from us?</p>'
        '<button type="submit">Unsubscribe</button></form>')))


@router.post("/u/{token}", response_class=HTMLResponse, include_in_schema=False)
def unsubscribe(token: str, db: DB):
    """One-click unsubscribe. Also serves RFC 8058 List-Unsubscribe-Post: the body is
    ignored, the signed token says who and which channel."""
    parsed = read_unsubscribe_token(token)
    if parsed is None:
        return HTMLResponse(PAGE.format(t="Link not valid", b="<p>This link is not valid."
                                        "</p>"), status_code=404)
    tenant_id, channel, h = parsed
    bind_tenant(db, tenant_id)
    if db.scalar(select(Suppression.id).where(Suppression.tenant_id == tenant_id,
                                              Suppression.channel == channel,
                                              Suppression.value_hash == h)) is None:
        db.add(Suppression(tenant_id=tenant_id, channel=channel, value_hash=h,
                           reason="unsubscribe_link"))
    db.commit()
    return HTMLResponse(PAGE.format(t="You're unsubscribed",
                                    b="<p>You won't receive further messages from us.</p>"))


# --- Resend delivery webhook (ADR-035) ------------------------------------------------------------

WEBHOOK_KINDS = {"email.bounced": "bounce", "email.complained": "complaint"}


@router.post("/webhooks/resend/{tenant_id}", include_in_schema=False)
async def resend_webhook(tenant_id: uuid.UUID, request: Request, db: DB):
    """Bounces and spam complaints from Resend. Signed (Svix); unsigned or stale calls are
    refused. The address is opted out for good and the message's launch gets a signal, so
    the launch auto-pauses if its bounce/complaint rate crosses the limit."""
    from .connectors.email import verify_webhook

    secret = os.environ.get("RESEND_WEBHOOK_SECRET", "")
    if not secret:
        raise HTTPException(503, "RESEND_WEBHOOK_SECRET is not set")
    body = await request.body()
    if not verify_webhook(secret, {k.lower(): v for k, v in request.headers.items()}, body):
        raise HTTPException(401, "bad signature")
    try:
        event = json.loads(body)
        kind = WEBHOOK_KINDS.get(event.get("type"))
        email_id = str((event.get("data") or {}).get("email_id") or "")
    except (ValueError, AttributeError):
        raise HTTPException(400, "not a Resend event") from None
    if kind is None or not email_id:
        return {"ok": True, "ignored": True}
    bind_tenant(db, tenant_id)
    m = db.scalar(select(OutreachMessage).where(OutreachMessage.tenant_id == tenant_id,
                                                OutreachMessage.external_id == email_id))
    if m is None:
        return {"ok": True, "ignored": True}
    h = address_hash(m.to_address)
    if db.scalar(select(Suppression.id).where(Suppression.tenant_id == tenant_id,
                                              Suppression.channel == "email",
                                              Suppression.value_hash == h)) is None:
        db.add(Suppression(tenant_id=tenant_id, channel="email", value_hash=h, reason=kind))
    k = db.get(OutreachCampaign, m.campaign_id)
    if k is not None and k.launch_id is not None and db.scalar(select(LaunchSignal.id).where(
            LaunchSignal.launch_id == k.launch_id, LaunchSignal.kind == kind,
            LaunchSignal.external_ref == email_id)) is None:
        db.add(LaunchSignal(tenant_id=tenant_id, launch_id=k.launch_id, kind=kind,
                            source="resend", external_ref=email_id))
    db.commit()
    return {"ok": True, "recorded": kind}


# --- SMS opt-outs from Twilio (ADR-059) ---------------------------------------------------------

@router.post("/webhooks/twilio/{tenant_id}", include_in_schema=False)
async def twilio_inbound(tenant_id: uuid.UUID, request: Request, db: DB):
    """Incoming texts. A STOP (or UNSUBSCRIBE, CANCEL, END, QUIT…) suppresses that number
    for SMS and calls at once. Only requests signed with the account's auth token count."""
    from .connectors import telephony

    token = os.environ.get("TWILIO_AUTH_TOKEN", "")
    base = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
    from urllib.parse import parse_qsl

    # Twilio posts application/x-www-form-urlencoded; parsed here so no multipart library
    # is needed (blank values kept: they are part of the signature)
    form = dict(parse_qsl((await request.body()).decode("utf-8", "replace"),
                          keep_blank_values=True))
    url = f"{base}/webhooks/twilio/{tenant_id}"
    if not token or not telephony.valid_signature(
            token, url, form, request.headers.get("x-twilio-signature", "")):
        raise HTTPException(403, "bad signature")
    if form.get("AccountSid") != os.environ.get("TWILIO_ACCOUNT_SID"):
        raise HTTPException(403, "another account")
    phone = norm_phone(form.get("From"))
    if not phone or not telephony.is_stop(form.get("Body", "")):
        return HTMLResponse("<Response/>", media_type="text/xml")
    bind_tenant(db, tenant_id)
    _suppress(db, tenant_id, "all", phone, "stop")
    audit(db, tenant_id, None, "suppression.added", "Suppression", None, channel="all",
          via="sms_stop")
    db.commit()
    # Twilio itself sends the carrier-required STOP confirmation: reply with nothing.
    return HTMLResponse("<Response/>", media_type="text/xml")
