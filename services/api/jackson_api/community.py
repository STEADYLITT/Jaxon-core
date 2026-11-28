"""Community tasks (ADR-044): Discord/Telegram access follows real Whop memberships, and
announcements are approved public posts.

community_sync
- Discord: a member whose Whop membership grants access (active, trialing, completed) and
  who linked Discord on Whop gets DISCORD_MEMBER_ROLE_ID; anyone else loses it. Only rows
  whose access changed since the last sync are touched, so a re-run calls Discord 0 times.
- Telegram: a member who lost access and linked Telegram is removed from TELEGRAM_CHAT_ID
  (ban + unban, so they can come back if they pay again). New members join with a one-use
  invite link (POST /community/telegram/invite).
community_announce
- One message to the Discord announcements channel or the Telegram chat. Claims-checked,
  then a PUBLIC_POST: the owner approves it, or a Launch whose social accounts include
  "discord"/"telegram" covers it.
"""

from .worker_errors import NonRetryableError


def sync_handler(ctx):
    from sqlalchemy import select

    from .connectors import community as K
    from .connectors.whop import ACCESS
    from .models import RunEvent, WhopMember
    from .worker import TaskResult

    if not (K.discord_configured() or K.telegram_configured()):
        raise NonRetryableError("neither Discord nor Telegram is connected: set "
                                "DISCORD_BOT_TOKEN/DISCORD_GUILD_ID/DISCORD_MEMBER_ROLE_ID or "
                                "TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID in Railway")
    import os
    role = os.environ.get("DISCORD_MEMBER_ROLE_ID", "")
    dc = K.discord() if K.discord_configured() else None
    tg = K.telegram() if K.telegram_configured() else None
    out = {"discord_added": 0, "discord_removed": 0, "telegram_removed": 0, "unchanged": 0}
    with ctx.db_session() as db:
        rows = db.scalars(select(WhopMember).where(WhopMember.tenant_id == ctx.tenant_id)
                          .order_by(WhopMember.updated_at)).all()
        for m in rows:
            access = m.status in ACCESS
            want = "granted" if access else "revoked"
            touched = False
            if dc and m.discord_id and m.discord_synced != want:
                dc.set_role(m.discord_id, role, access,
                            f"Jackson: Whop membership {m.membership_id} is {m.status}")
                m.discord_synced = want
                out["discord_added" if access else "discord_removed"] += 1
                touched = True
            if tg and m.telegram_id and not access and m.telegram_synced != "revoked":
                tg.remove(m.telegram_id)
                m.telegram_synced = "revoked"
                out["telegram_removed"] += 1
                touched = True
            if tg and m.telegram_id and access and m.telegram_synced == "revoked":
                m.telegram_synced = None  # they can rejoin with a new invite link
            out["unchanged"] += not touched
            db.flush()
        db.add(RunEvent(tenant_id=ctx.tenant_id, run_id=ctx.run_id, type="community.synced",
                        data=out))
        db.commit()
    out["summary"] = (f"Community sync: +{out['discord_added']} / -{out['discord_removed']} "
                      f"Discord roles, {out['telegram_removed']} removed from Telegram")
    return TaskResult(out)


def announce_handler(ctx):
    import os

    from .connectors import community as K
    from .connectors.claims import check_claims
    from .models import RunEvent
    from .policy import Action, Category
    from .worker import TaskResult

    inp = ctx.input or {}
    platform, text = inp.get("platform"), str(inp.get("text") or "").strip()
    if platform not in ("discord", "telegram"):
        raise NonRetryableError("platform must be discord or telegram")
    if not text:
        raise NonRetryableError("nothing to announce")
    problems = check_claims(text)
    if problems:
        raise NonRetryableError("the announcement fails the claims check: " + "; ".join(problems))
    if platform == "discord":
        channel = os.environ.get("DISCORD_ANNOUNCE_CHANNEL_ID")
        if not (K.discord_configured() and channel):
            raise NonRetryableError("set DISCORD_BOT_TOKEN, DISCORD_GUILD_ID, "
                                    "DISCORD_MEMBER_ROLE_ID and DISCORD_ANNOUNCE_CHANNEL_ID")
    elif not K.telegram_configured():
        raise NonRetryableError("set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID")
    ctx.authorize(Action(Category.PUBLIC_POST, f"{platform} announcement: {text[:120]}",
                         channel="social", target=platform))
    msg_id = (K.discord().post(channel, text) if platform == "discord"
              else K.telegram().post(text))
    out = {"platform": platform, "message_id": msg_id, "characters": len(text)}
    with ctx.db_session() as db:
        db.add(RunEvent(tenant_id=ctx.tenant_id, run_id=ctx.run_id,
                        type="community.announced", data=out))
        db.commit()
    out["summary"] = f"Posted to {platform}"
    return TaskResult(out)
