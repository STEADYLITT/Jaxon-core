"""CRM and compliant outreach (spec vol. 03, phase 18, ADR-028).

Leads must come from a permitted source with a recorded lawful basis. They're deduped and
scored. Campaigns are prepared (every message rendered and reviewed), approved by a person
(outbound_campaign: always), then sent by the worker through the email/SMS connector
(MOCK until a provider key exists). Opt-outs are permanent, stored as hashes, honoured
at send time, and enforced again by a database trigger.

These rules follow the common baseline of US CAN-SPAM (sender identity, a physical postal
address, a working opt-out, no deceptive subject lines) and TCPA (prior consent for
SMS/calls, 8am-9pm recipient time). They are guard rails, not legal advice.
"""

import base64
import hashlib
import hmac
import os
import re
import uuid
from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .connectors.claims import check_claims
from .worker_errors import NonRetryableError

SOURCE_WEIGHT = {"inbound": 30, "referral": 25, "existing_customer": 25, "event": 15,
                 "public_business_listing": 10}
QUIET_START, QUIET_END = 8, 21  # contact allowed 08:00-20:59 recipient local time


# --- Normalisation, dedupe, scoring -------------------------------------------------------------

def norm_email(v: str | None) -> str | None:
    v = (v or "").strip().lower()
    return v if re.fullmatch(r"[^@\s]+@[^@\s]+\.[a-z]{2,}", v) else None


def norm_phone(v: str | None) -> str | None:
    digits = re.sub(r"\D", "", v or "")
    if len(digits) == 10:  # assume North American number without country code
        digits = "1" + digits
    return f"+{digits}" if 11 <= len(digits) <= 15 else None


def address_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def dedupe_key(name: str, company: str, email: str | None, phone: str | None) -> str:
    if email:
        return f"email:{email}"
    if phone:
        return f"phone:{phone}"
    return "name:" + re.sub(r"[^a-z0-9]+", " ", f"{name} {company}".lower()).strip()


def score_lead(c: dict, icp: dict | None = None) -> dict:
    """Transparent 0-100 score: source + reachability + fit with the ideal customer."""
    icp = icp or {}
    parts = {"source": SOURCE_WEIGHT.get(c.get("source_type"), 0),
             "has_email": 15 if c.get("email") else 0,
             "has_phone": 10 if c.get("phone") else 0,
             "has_company": 10 if c.get("company") else 0}
    industries = [i.lower() for i in icp.get("industries", [])]
    regions = [r.upper() for r in icp.get("regions", [])]
    parts["industry_fit"] = 20 if industries and (c.get("industry") or "").lower() in \
        industries else 0
    parts["region_fit"] = 15 if regions and (c.get("region") or "").upper() in regions else 0
    return {"score": min(100, sum(parts.values())), "parts": parts}


# --- Unsubscribe tokens -------------------------------------------------------------------------

def _secret() -> bytes:
    s = os.environ.get("UNSUBSCRIBE_SECRET")
    if not s or len(s) < 32:
        raise NonRetryableError("UNSUBSCRIBE_SECRET (32+ characters) must be set before "
                                "outreach can be prepared")
    return s.encode()


def unsubscribe_token(tenant_id: uuid.UUID, channel: str, address: str) -> str:
    payload = f"{tenant_id}|{channel}|{address_hash(address)}".encode()
    sig = hmac.new(_secret(), payload, hashlib.sha256).digest()[:16]
    return base64.urlsafe_b64encode(payload + b"|" + sig).decode().rstrip("=")


def read_unsubscribe_token(token: str) -> tuple[uuid.UUID, str, str] | None:
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
        payload, sig = raw[:-17], raw[-16:]
        if raw[-17:-16] != b"|":
            return None
        good = hmac.new(_secret(), payload, hashlib.sha256).digest()[:16]
        if not hmac.compare_digest(sig, good):
            return None
        tenant, channel, h = payload.decode().split("|")
        return uuid.UUID(tenant), channel, h
    except (ValueError, UnicodeDecodeError):
        return None


def unsubscribe_url(tenant_id, channel, address) -> str:
    base = os.environ.get("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")
    return f"{base}/u/{unsubscribe_token(tenant_id, channel, address)}"


# --- Rendering + review -------------------------------------------------------------------------

def render(template: str, contact: dict, extra: dict) -> str:
    values = {"first_name": (contact.get("name") or "").split(" ")[0],
              "name": contact.get("name") or "", "company": contact.get("company") or "",
              **extra}
    return re.sub(r"\{(\w+)\}", lambda m: str(values.get(m.group(1), m.group(0))), template)


def review_message(campaign: dict, contact: dict, subject: str, body: str,
                   suppressed: bool) -> dict:
    problems: list[str] = []
    ch = campaign["channel"]
    if suppressed:
        problems.append("recipient has opted out")
    if ch == "email":
        if not contact.get("email"):
            problems.append("contact has no valid email address")
        if not campaign.get("sender_name") or not norm_email(campaign.get("sender_email")):
            problems.append("sender name and a valid sender email are required")
        if len((campaign.get("postal_address") or "").strip()) < 10:
            problems.append("a physical postal address is required in commercial email")
        if "/u/" not in body:
            problems.append("email must include the unsubscribe link ({unsubscribe_url})")
        if re.match(r"\s*(re|fw|fwd)\s*:", subject, re.IGNORECASE):
            problems.append("subject must not pretend to be a reply or forward")
        if not subject.strip():
            problems.append("subject is empty")
        if (campaign.get("postal_address") or "x") not in body:
            problems.append("email must show the postal address ({postal_address})")
    else:
        if not contact.get("phone"):
            problems.append("contact has no valid phone number")
        consent = contact.get("sms_consent_at" if ch == "sms" else "call_consent_at")
        if not consent:
            problems.append(f"{ch} needs the contact's recorded prior consent")
        if ch == "sms" and "stop" not in body.lower():
            problems.append("SMS must say how to opt out (e.g. 'Reply STOP to opt out')")
        if ch == "sms" and len(body) > 480:
            problems.append("SMS is longer than 3 segments (480 characters)")
    problems += [f"copy: {p}" for p in check_claims(f"{subject}\n{body}")]
    return {"passed": not problems, "problems": problems}


def within_quiet_hours(tz: str, now: datetime | None = None) -> bool:
    """True if it's an acceptable time to text or call the recipient."""
    try:
        local = (now or datetime.now(UTC)).astimezone(ZoneInfo(tz or "UTC"))
    except ZoneInfoNotFoundError:
        return False
    return QUIET_START <= local.hour < QUIET_END


def contact_dict(c) -> dict:
    return {"id": str(c.id), "name": c.name, "company": c.company, "email": c.email,
            "phone": c.phone, "timezone": c.timezone, "stage": c.stage,
            "sms_consent_at": c.sms_consent_at, "call_consent_at": c.call_consent_at}


def campaign_dict(k) -> dict:
    return {"id": str(k.id), "channel": k.channel, "sender_name": k.sender_name,
            "sender_email": k.sender_email, "postal_address": k.postal_address,
            "subject_template": k.subject_template, "body_template": k.body_template,
            "audience": k.audience, "daily_limit": k.daily_limit, "status": k.status}


def is_suppressed(db, tenant_id, channel: str, address: str | None) -> bool:
    from sqlalchemy import select

    from .models import Suppression

    if not address:
        return False
    return db.scalar(select(Suppression.id).where(
        Suppression.tenant_id == tenant_id, Suppression.channel.in_([channel, "all"]),
        Suppression.value_hash == address_hash(address))) is not None


# --- Worker handlers ----------------------------------------------------------------------------

def prepare_handler(ctx):
    """Render and review one message per audience contact. Nothing is sent."""
    from sqlalchemy import select

    from .models import Contact, OutreachCampaign, OutreachMessage
    from .worker import TaskResult

    cid = uuid.UUID((ctx.input or {})["campaign_id"])
    counts = {"queued": 0, "blocked": 0, "skipped": 0}
    with ctx.db_session() as db:
        k = db.get(OutreachCampaign, cid)
        if k is None or k.tenant_id != ctx.tenant_id:
            raise NonRetryableError("campaign not found")
        if k.status != "draft":
            raise NonRetryableError(f"campaign is {k.status}; only drafts can be prepared")
        camp = campaign_dict(k)
        aud = k.audience or {}
        q = select(Contact).where(Contact.tenant_id == ctx.tenant_id)
        if aud.get("stages"):
            q = q.where(Contact.stage.in_(aud["stages"]))
        if aud.get("min_score"):
            q = q.where(Contact.score >= int(aud["min_score"]))
        if k.business_id:
            q = q.where(Contact.business_id == k.business_id)
        contacts = db.scalars(q.order_by(Contact.score.desc()).limit(1000)).all()
        for c in contacts:
            con = contact_dict(c)
            addr = c.email if camp["channel"] == "email" else c.phone
            if not addr:
                counts["skipped"] += 1
                continue
            extra = {"unsubscribe_url": unsubscribe_url(ctx.tenant_id, camp["channel"], addr),
                     "postal_address": camp["postal_address"],
                     "sender_name": camp["sender_name"]}
            subject = render(camp["subject_template"], con, extra)[:300]
            body = render(camp["body_template"], con, extra)
            rv = review_message(camp, con, subject, body,
                                is_suppressed(db, ctx.tenant_id, camp["channel"], addr))
            status = "queued" if rv["passed"] else "blocked"
            counts[status] += 1
            db.add(OutreachMessage(tenant_id=ctx.tenant_id, campaign_id=cid, contact_id=c.id,
                                   channel=camp["channel"], to_address=addr, subject=subject,
                                   body=body, status=status, review=rv))
        db.get(OutreachCampaign, cid).status = "prepared"
        db.commit()
    return TaskResult({"campaign_id": str(cid), **counts, "connector": "none (prepare only)"})


class MockSender:
    """MOCK email/SMS/call connector: nothing leaves the system."""

    backend = "mock"

    def send(self, channel: str, to: str, subject: str, body: str) -> str:
        return f"mock-{channel}-{uuid.uuid4().hex[:10]}"


def send_handler(ctx):
    """Send an approved campaign: re-check each message at send time (opt-outs, quiet
    hours, daily limit, campaign still approved). Idempotent: sent messages are skipped."""
    from sqlalchemy import func, select
    from sqlalchemy.exc import DBAPIError

    from .models import Contact, OutreachCampaign, OutreachMessage, RunEvent
    from .policy import Action, Category
    from .worker import TaskResult

    cid = uuid.UUID((ctx.input or {})["campaign_id"])
    with ctx.db_session() as db:
        k = db.get(OutreachCampaign, cid)
        if k is None or k.tenant_id != ctx.tenant_id:
            raise NonRetryableError("campaign not found")
        person_approved = k.status in ("approved", "sending") and k.approved_by is not None
        # Phase 21: a prepared campaign inside a Launch approval that names its list may be
        # sent without a per-campaign approval (policy + DB trigger check the scope).
        launch_ok = k.launch_id is not None and k.status in ("prepared", "approved", "sending")
        if not (person_approved or launch_ok):
            raise NonRetryableError(f"campaign is {k.status}, not approved")
        channel, limit, target = k.channel, k.daily_limit, (k.audience or {}).get("list")
        db.rollback()
    ctx.authorize(Action(Category.OUTBOUND_CAMPAIGN, f"send campaign {cid}", channel="email",
                         target=target))
    sender = MockSender()
    out = {"sent": 0, "suppressed": 0, "deferred_quiet_hours": 0, "deferred_daily_limit": 0,
           "stopped": False}
    start_of_day = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    with ctx.db_session() as db:
        k = db.get(OutreachCampaign, cid)
        k.status = "sending"
        db.commit()
        ids = db.scalars(select(OutreachMessage.id).where(
            OutreachMessage.campaign_id == cid, OutreachMessage.status == "queued")
            .order_by(OutreachMessage.created_at)).all()
        for mid in ids:
            db.expire_all()
            if db.get(OutreachCampaign, cid).status != "sending":  # owner cancelled
                out["stopped"] = True
                break
            sent_today = db.scalar(select(func.count()).where(
                OutreachMessage.campaign_id == cid, OutreachMessage.status == "sent",
                OutreachMessage.sent_at >= start_of_day))
            if sent_today >= limit:
                out["deferred_daily_limit"] += 1
                continue
            m = db.get(OutreachMessage, mid)
            if is_suppressed(db, ctx.tenant_id, channel, m.to_address):
                m.status = "skipped"
                m.review = {**m.review, "skipped": "opted out after approval"}
                out["suppressed"] += 1
                db.commit()
                continue
            contact = db.get(Contact, m.contact_id) if m.contact_id else None
            if channel != "email" and not within_quiet_hours(contact.timezone if contact
                                                              else "UTC"):
                out["deferred_quiet_hours"] += 1
                continue
            # Claim first (the DB guard checks approval/opt-out/consent here), then hand it
            # to the provider, then record the result. A crash between claim and record
            # leaves it 'sending' and it is never retried blindly (see `unconfirmed`).
            m.status = "sending"
            try:
                db.commit()
            except DBAPIError as e:
                db.rollback()
                if "outreach_blocked" not in str(e):
                    raise
                out["stopped"] = True  # cancelled or opted out between checks
                break
            m = db.get(OutreachMessage, mid)
            m.external_id = sender.send(channel, m.to_address, m.subject, m.body)
            m.status, m.sent_at = "sent", func.now()
            if contact is not None and contact.stage == "new":
                contact.stage = "contacted"
            db.commit()
            out["sent"] += 1
        remaining = db.scalar(select(func.count()).where(
            OutreachMessage.campaign_id == cid, OutreachMessage.status == "queued"))
        out["unconfirmed"] = db.scalar(select(func.count()).where(
            OutreachMessage.campaign_id == cid, OutreachMessage.status == "sending"))
        k = db.get(OutreachCampaign, cid)
        if remaining == 0 and k.status == "sending":
            k.status = "completed"
        db.add(RunEvent(tenant_id=ctx.tenant_id, run_id=ctx.run_id, type="outreach.sent",
                        data={"campaign_id": str(cid), **out, "remaining": remaining,
                              "connector": sender.backend}))
        db.commit()
    return TaskResult({"campaign_id": str(cid), **out, "remaining": remaining,
                       "connector": sender.backend})
