"""Community API (ADR-044): sync Discord/Telegram access from Whop, one-use Telegram
invites for paying members, and approved announcements."""

import uuid
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from .connectors import community as K
from .connectors.whop import ACCESS
from .deps import DB, TenantMember, TenantWriter
from .governance import audit
from .models import WhopMember

router = APIRouter(tags=["community"])


@router.get("/tenants/{tenant_id}/community")
def community_status(tenant_id: uuid.UUID, member: TenantMember, db: DB) -> dict:
    def n(*where):
        return int(db.scalar(select(func.count()).select_from(WhopMember).where(
            WhopMember.tenant_id == tenant_id, *where)) or 0)
    return {"discord_connected": K.discord_configured(),
            "telegram_connected": K.telegram_configured(),
            "members_with_access": n(WhopMember.status.in_(ACCESS)),
            "discord_linked": n(WhopMember.discord_id.is_not(None)),
            "discord_role_granted": n(WhopMember.discord_synced == "granted"),
            "telegram_linked": n(WhopMember.telegram_id.is_not(None))}


def _run(db, tenant_id, writer, kind: str, title: str, inp: dict, key: str):
    from .runs import CommandError, create_run
    try:
        run, _ = create_run(db, tenant_id, writer.user_id, {
            "type": "BUILD", "objective": title[:300],
            "idempotency_key": f"{kind}-{key}"},
            first_task={"kind": kind, "title": title[:200], "tool_scope": [], "input": inp})
    except CommandError as e:
        db.rollback()
        raise HTTPException(e.status, {"code": e.code, "message": e.message}) from None
    db.commit()
    return {"run_id": str(run.id)}


class KeyIn(BaseModel):
    idempotency_key: str = Field(min_length=8, max_length=200)


@router.post("/tenants/{tenant_id}/community/sync", status_code=202)
def community_sync(tenant_id: uuid.UUID, body: KeyIn, writer: TenantWriter, db: DB) -> dict:
    if not (K.discord_configured() or K.telegram_configured()):
        raise HTTPException(409, "set DISCORD_BOT_TOKEN, DISCORD_GUILD_ID and "
                                 "DISCORD_MEMBER_ROLE_ID (or TELEGRAM_BOT_TOKEN and "
                                 "TELEGRAM_CHAT_ID) in Railway")
    return _run(db, tenant_id, writer, "community_sync",
                "Sync Discord/Telegram access from Whop", {}, body.idempotency_key)


class InviteIn(BaseModel):
    membership_id: str = Field(pattern=r"^mem_[A-Za-z0-9]+$")


@router.post("/tenants/{tenant_id}/community/telegram/invite", status_code=201)
def telegram_invite(tenant_id: uuid.UUID, body: InviteIn, writer: TenantWriter, db: DB) -> dict:
    """A one-person, 48-hour invite link, only for a member Whop says has access."""
    m = db.scalar(select(WhopMember).where(WhopMember.tenant_id == tenant_id,
                                           WhopMember.membership_id == body.membership_id))
    if m is None:
        raise HTTPException(404, "membership not found (run a Whop sync first)")
    if m.status not in ACCESS:
        raise HTTPException(409, f"this membership is {m.status}: no access, no invite")
    if not K.telegram_configured():
        raise HTTPException(409, "set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in Railway")
    try:
        link = K.telegram().invite(f"whop {m.membership_id}")
    except Exception as e:  # noqa: BLE001
        from .connections import scrub
        raise HTTPException(502, scrub(str(e))) from None
    audit(db, tenant_id, writer.user_id, "community.telegram_invite", "whop_member", m.id,
          membership_id=m.membership_id)
    db.commit()
    return {"invite_link": link, "member_limit": 1, "expires_in_hours": 48}


class AnnounceIn(BaseModel):
    platform: Literal["discord", "telegram"]
    text: str = Field(min_length=1, max_length=2000)
    idempotency_key: str = Field(min_length=8, max_length=200)


@router.post("/tenants/{tenant_id}/community/announce", status_code=202)
def announce(tenant_id: uuid.UUID, body: AnnounceIn, writer: TenantWriter, db: DB) -> dict:
    """An announcement is a public post: it waits for your approval (or a Launch whose
    social accounts include this platform)."""
    return _run(db, tenant_id, writer, "community_announce",
                f"{body.platform} announcement: {body.text[:100]}",
                {"platform": body.platform, "text": body.text}, body.idempotency_key)


# --- Telegram auto-invite (ADR-049) -------------------------------------------------------------

@router.post("/tenants/{tenant_id}/community/telegram/setup", status_code=201)
def telegram_setup(tenant_id: uuid.UUID, writer: TenantWriter, db: DB) -> dict:
    """Turn on auto-invite: one shareable join link; Telegram asks Jackson about each join,
    and only paying Whop members are let in."""
    import os

    secret = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")
    base = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
    if not K.telegram_configured():
        raise HTTPException(409, "set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in Railway")
    if len(secret) < 16 or not secret.replace("-", "").replace("_", "").isalnum():
        raise HTTPException(409, "set TELEGRAM_WEBHOOK_SECRET in Railway (16+ letters, digits, "
                                 "- or _)")
    if not base.startswith("https://"):
        raise HTTPException(409, "PUBLIC_BASE_URL must be the app's https address")
    t = K.telegram()
    try:
        t.set_webhook(f"{base}/webhooks/telegram/{tenant_id}", secret)
        link = t.join_request_link("Paying members (Jackson)")
    except Exception as e:  # noqa: BLE001
        from .connections import scrub
        raise HTTPException(502, scrub(str(e))) from None
    audit(db, tenant_id, writer.user_id, "community.telegram_auto_invite", "tenant", tenant_id)
    db.commit()
    return {"join_link": link, "how": "Share this link (e.g. on your Whop welcome page). "
            "Paying members who linked Telegram on Whop are let in automatically; anyone "
            "else is declined and told why."}


TELEGRAM_NOT_LINKED = ("To join, link your Telegram account in Whop (Settings -> Connected "
                       "accounts) using an active membership, then tap the invite link again.")


@router.post("/webhooks/telegram/{tenant_id}", include_in_schema=False)
async def telegram_webhook(tenant_id: uuid.UUID, request: Request, db: DB):
    import hmac
    import os

    from .db import bind_tenant

    secret = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")
    got = request.headers.get("x-telegram-bot-api-secret-token", "")
    if not secret or not hmac.compare_digest(secret, got):
        raise HTTPException(401, "bad secret")
    try:
        update = await request.json()
        req = update.get("chat_join_request") or {}
        user = req.get("from") or {}
        chat_id = str((req.get("chat") or {}).get("id") or "")
    except (ValueError, AttributeError):
        raise HTTPException(400, "not a Telegram update") from None
    if not req or not user.get("id"):
        return {"ok": True, "ignored": True}
    if not K.telegram_configured() or chat_id != os.environ.get("TELEGRAM_CHAT_ID"):
        return {"ok": True, "ignored": "another chat"}
    bind_tenant(db, tenant_id)
    m = db.scalar(select(WhopMember).where(WhopMember.tenant_id == tenant_id,
                                           WhopMember.telegram_id == str(user["id"]),
                                           WhopMember.status.in_(ACCESS)).limit(1))
    t = K.telegram()
    if m is None:
        t.decline(user["id"])
        if req.get("user_chat_id"):
            try:
                t.tell(req["user_chat_id"], TELEGRAM_NOT_LINKED)
            except Exception:  # noqa: BLE001 - the decline is what matters
                pass
        return {"ok": True, "decision": "declined"}
    t.approve(user["id"])
    m.telegram_synced = "joined"
    audit(db, tenant_id, None, "community.telegram_join_approved", "whop_member", m.id,
          membership_id=m.membership_id)
    db.commit()
    return {"ok": True, "decision": "approved"}
