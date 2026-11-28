"""Connections: is each outside service LIVE, MOCK or in ERROR, and a "Test" that makes
one real, harmless call (ADR-038).

- Values of keys are never returned, logged or stored: only variable NAMES, and whether
  each is set. Any provider message is scrubbed of every configured secret before it is
  shown.
- A test result is kept in the audit log (who ran it, when, ok or not, short summary).
- Tests do nothing that costs money or reaches anyone but the owner:
    Claude      GET /v1/models (free)
    Shopify     token + read the shop's name/plan (read-only)
    Upload-Post GET /api/uploadposts/me (account + plan)
    Resend      one email to the signed-in owner's own address
    fal.ai      GET /v1/account/billing?expand=credits (balance)
    Whop        GET /api/v1/accounts/me (the account behind the key)
    ElevenLabs  GET /v1/user/subscription (plan and characters left)
    Apify       GET /v2/users/me (account and plan)
    Discord     GET /guilds/{id}?with_counts=true (the server, read-only)
    Telegram    getMe + getChat (the bot and the group, read-only)
    Netlify     GET /api/v1/user (the account behind the token)
    BatchData   no free endpoint: checks the token and price are set (no call, no charge)
    Alpaca      GET paper-api /v2/account (the PAPER account; live is never called)
    Meta ads    GET act_{id}?fields=name,account_status,currency,amount_spent
    TikTok ads  GET advertiser/info/ (the advertiser account)
    GitHub      GET /user (who the token is)
    YouTube     GET /youtube/v3/videos?part=id (1 quota unit)
    Twitch      app token + GET /helix/games/top?first=1
    Amazon      LWA token + GET /sellers/v1/marketplaceParticipations
    eBay        app token + one Browse search (limit 1)
    Databento   GET metadata.list_datasets (free)
    APEX-7      GET APEX7_RESULTS_URL (reads the runs it publishes)
"""

import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import httpx

SECRET_VARS = ("ANTHROPIC_API_KEY", "SHOPIFY_CLIENT_SECRET", "SHOPIFY_ADMIN_API_TOKEN",
               "UPLOADPOST_API_KEY", "RESEND_API_KEY", "RESEND_WEBHOOK_SECRET", "FAL_KEY",
               "CLERK_SECRET_KEY", "WHOP_API_KEY", "WHOP_WEBHOOK_SECRET",
               "ELEVENLABS_API_KEY", "APIFY_TOKEN",
               "DISCORD_BOT_TOKEN", "TELEGRAM_BOT_TOKEN",
               "NETLIFY_AUTH_TOKEN", "BATCHDATA_API_TOKEN", "TELEGRAM_WEBHOOK_SECRET",
               "ALPACA_PAPER_SECRET_KEY",
               "META_ACCESS_TOKEN", "TIKTOK_ACCESS_TOKEN",
               "GITHUB_APP_TOKEN", "YOUTUBE_API_KEY", "TWITCH_CLIENT_SECRET",
               "AMAZON_SP_CLIENT_SECRET", "AMAZON_SP_REFRESH_TOKEN", "EBAY_CLIENT_SECRET",
               "DATABENTO_API_KEY", "APEX7_RESULTS_TOKEN")


def _new_client() -> httpx.Client:
    return httpx.Client(timeout=20)


def scrub(text: str) -> str:
    out = str(text)
    for name in SECRET_VARS:
        raw = os.environ.get(name) or ""
        # also the trimmed and escaped forms: a pasted key with a stray newline shows up
        # in error messages without it, or as "\n"
        for v in {raw, raw.strip(), raw.strip().strip("\"'").strip(), repr(raw)[1:-1]}:
            if v and len(v) >= 6:
                out = out.replace(v, "•••")
    return out[:500]


class TestFailed(Exception):
    pass


def _json(r: httpx.Response) -> dict:
    try:
        body = r.json()
        return body if isinstance(body, dict) else {"data": body}
    except ValueError:
        return {}


def _msg(body: dict, r: httpx.Response) -> str:
    err = body.get("error")
    if isinstance(err, dict):
        err = err.get("message")
    return str(err or body.get("message") or body.get("detail") or f"HTTP {r.status_code}")


# --- The real, harmless test calls -------------------------------------------------------------

def test_models(ctx: dict) -> dict:
    """Uses the worker's own settings (cleaned key, base URL check), so a pass here means
    Jaxon's brain can reach Claude. A failure names the real cause, never the key."""
    from .models_ai.anthropic_provider import base_url, clean_key, config_report

    cfg = config_report()
    root = base_url() or "https://api.anthropic.com"
    try:
        with _new_client() as c:
            r = c.get(f"{root}/v1/models", params={"limit": 20}, headers={
                "x-api-key": clean_key(os.environ["ANTHROPIC_API_KEY"]),
                "anthropic-version": "2023-06-01"})
    except httpx.HTTPError as e:
        raise TestFailed(f"Can't reach {cfg['base_url_host']}: {type(e).__name__}: {e} "
                         f"[{cfg}]") from None
    body = _json(r)
    if r.status_code != 200:
        raise TestFailed(f"Anthropic refused the key: {_msg(body, r)} [{cfg}]")
    ids = [m.get("id") for m in body.get("data", []) if isinstance(m, dict)]
    return {"models_available": len(ids), "examples": ids[:3], **cfg}


def test_shopify(ctx: dict) -> dict:
    from .connectors.shopify import ClientCredentials, ShopifyAdmin

    domain = os.environ["SHOPIFY_STORE_DOMAIN"].strip().lower()
    token = os.environ.get("SHOPIFY_ADMIN_API_TOKEN")
    with _new_client() as c:
        auth = token or ClientCredentials(domain, os.environ["SHOPIFY_CLIENT_ID"],
                                          os.environ["SHOPIFY_CLIENT_SECRET"], client=c)
        try:
            body = ShopifyAdmin(domain, auth, client=c)._gql(
                "{ shop { name myshopifyDomain plan { displayName } } }", {})
        except Exception as e:  # noqa: BLE001 - shown (scrubbed) to the owner
            raise TestFailed(str(e)) from None
    if body.get("errors"):
        raise TestFailed(f"Shopify: {body['errors']}")
    shop = (body.get("data") or {}).get("shop") or {}
    return {"store": shop.get("myshopifyDomain"), "name": shop.get("name"),
            "plan": (shop.get("plan") or {}).get("displayName"),
            "access": "drafts only until a Launch approval covers publishing"}


def test_uploadpost(ctx: dict) -> dict:
    with _new_client() as c:
        r = c.get("https://api.upload-post.com/api/uploadposts/me",
                  headers={"Authorization": f"Apikey {os.environ['UPLOADPOST_API_KEY']}"})
    body = _json(r)
    if r.status_code != 200 or body.get("success") is False:
        raise TestFailed(f"Upload-Post refused the key: {_msg(body, r)}")
    return {"plan": body.get("plan"), "account_email": body.get("email"),
            "posting_profile": os.environ.get("UPLOADPOST_USER")}


def test_resend(ctx: dict) -> dict:
    from .connectors.email import ResendSender

    to = ctx.get("owner_email")
    if not to:
        raise TestFailed("your user has no email address to send the test to")
    r = ResendSender(os.environ["RESEND_API_KEY"], os.environ["RESEND_FROM"],
                     client=_new_client())
    try:
        email_id = r.send(to=to, subject="Jackson OS: email connection test",
                          text="This is a test from Jackson OS. If you can read it, sending "
                               "email through Resend works. No one else received this.",
                          from_addr=r.default_from,
                          unsubscribe_url=f"{os.environ.get('PUBLIC_BASE_URL', '')}/console",
                          idempotency_key=f"conn-test-{int(time.time())}")
    except Exception as e:  # noqa: BLE001
        raise TestFailed(str(e)) from None
    return {"sent_to": to, "from": r.default_from, "resend_email_id": email_id,
            "webhook_secret_set": bool(os.environ.get("RESEND_WEBHOOK_SECRET"))}


def test_fal(ctx: dict) -> dict:
    with _new_client() as c:
        r = c.get("https://api.fal.ai/v1/account/billing", params={"expand": "credits"},
                  headers={"Authorization": f"Key {os.environ['FAL_KEY']}"})
    body = _json(r)
    if r.status_code == 200:
        cr = body.get("credits") or {}
        return {"account": body.get("username"), "credit_balance": cr.get("current_balance"),
                "currency": cr.get("currency")}
    if r.status_code == 403:  # a valid key that may generate but not read billing
        return {"key": "accepted", "credit_balance": "hidden (this key can't read billing)",
                "note": "generation works with this key; for the balance, use a key with "
                        "the READONLY or FULL permission preset"}
    raise TestFailed(f"fal.ai refused the key: {_msg(body, r)}")


def test_whop(ctx: dict) -> dict:
    from .connectors.whop import Whop

    try:
        me = Whop(os.environ["WHOP_API_KEY"], client=_new_client()).me()
    except Exception as e:  # noqa: BLE001
        raise TestFailed(str(e)) from None
    return {"account": me.get("id"), "name": me.get("title") or me.get("name"),
            "webhook_secret_set": bool(os.environ.get("WHOP_WEBHOOK_SECRET")),
            "access": "reads members and sales; makes checkout links only for plans you name"}


def test_elevenlabs(ctx: dict) -> dict:
    from .connectors.voice import ElevenLabs

    try:
        el = ElevenLabs(os.environ["ELEVENLABS_API_KEY"], client=_new_client())
        sub = el.subscription()
    except Exception as e:  # noqa: BLE001
        raise TestFailed(str(e)) from None
    used, limit = sub.get("character_count"), sub.get("character_limit")
    return {"plan": sub.get("tier"), "characters_used": used, "character_limit": limit,
            "characters_left": (limit - used) if isinstance(used, int) and isinstance(
                limit, int) else None, "model": el.model}


def test_apify(ctx: dict) -> dict:
    from .connectors.webresearch import Apify

    try:
        me = Apify(os.environ["APIFY_TOKEN"], client=_new_client()).me()
    except Exception as e:  # noqa: BLE001
        raise TestFailed(str(e)) from None
    plan = me.get("plan") or {}
    return {"account": me.get("username"), "plan": plan.get("id"),
            "monthly_limit_usd": plan.get("maxMonthlyUsageUsd"),
            "access": "runs only after you approve each run's cost cap"}


def test_discord(ctx: dict) -> dict:
    from .connectors.community import Discord

    try:
        g = Discord(os.environ["DISCORD_BOT_TOKEN"], os.environ["DISCORD_GUILD_ID"],
                    client=_new_client()).server()
    except Exception as e:  # noqa: BLE001
        raise TestFailed(str(e)) from None
    role = os.environ.get("DISCORD_MEMBER_ROLE_ID")
    names = {str(r.get("id")): r.get("name") for r in g.get("roles") or []}
    if role not in names:
        raise TestFailed(f"the bot sees server '{g.get('name')}' but no role with id "
                         "DISCORD_MEMBER_ROLE_ID: copy the role's id again")
    return {"server": g.get("name"), "members": g.get("approximate_member_count"),
            "member_role": names[role],
            "announce_channel_set": bool(os.environ.get("DISCORD_ANNOUNCE_CHANNEL_ID"))}


def test_telegram(ctx: dict) -> dict:
    from .connectors.community import Telegram

    try:
        t = Telegram(os.environ["TELEGRAM_BOT_TOKEN"], os.environ["TELEGRAM_CHAT_ID"],
                     client=_new_client())
        me, chat = t.me(), t.chat_info()
    except Exception as e:  # noqa: BLE001
        raise TestFailed(str(e)) from None
    return {"bot": me.get("username"), "chat": chat.get("title"), "type": chat.get("type")}


def test_netlify(ctx: dict) -> dict:
    from .connectors.hosting import NetlifyHost

    try:
        u = NetlifyHost(os.environ["NETLIFY_AUTH_TOKEN"], client=_new_client()).user()
    except Exception as e:  # noqa: BLE001
        raise TestFailed(str(e)) from None
    return {"account": u.get("full_name") or u.get("slug"), "email": u.get("email"),
            "access": "publishes only after you approve each launch"}


def test_batchdata(ctx: dict) -> dict:
    tok = os.environ["BATCHDATA_API_TOKEN"].strip()
    if len(tok) < 20 or " " in tok:
        raise TestFailed("BATCHDATA_API_TOKEN doesn't look like a BatchData server-side token")
    per = os.environ.get("BATCHDATA_CENTS_PER_RECORD", "")
    if not per.isdigit():
        raise TestFailed("set BATCHDATA_CENTS_PER_RECORD to your plan's price per record "
                         "(in cents) so every lookup shows its cost first")
    return {"token": "set", "price_per_record_cents": int(per),
            "note": "BatchData has no free test call: the first approved comps search is "
                    "the real check"}


def test_alpaca(ctx: dict) -> dict:
    from .connectors.broker import AlpacaPaper

    try:
        a = AlpacaPaper(os.environ["ALPACA_PAPER_KEY_ID"], os.environ["ALPACA_PAPER_SECRET_KEY"],
                        client=_new_client()).account()
    except Exception as e:  # noqa: BLE001
        raise TestFailed(str(e)) from None
    return {"paper_account": a["account"], "status": a["status"], "equity": a["equity"],
            "note": "PAPER only: live orders stay refused"}


def test_meta_ads(ctx: dict) -> dict:
    from .connectors.ads import MetaAds

    try:
        a = MetaAds(os.environ["META_ACCESS_TOKEN"], client=_new_client()).account()
    except Exception as e:  # noqa: BLE001
        raise TestFailed(str(e)) from None
    return {**a, "access": "drafts are paused; ads only switch on inside an approved Launch"}


def test_tiktok_ads(ctx: dict) -> dict:
    from .connectors.ads import TikTokAds

    try:
        a = TikTokAds(os.environ["TIKTOK_ACCESS_TOKEN"], os.environ["TIKTOK_ADVERTISER_ID"],
                      client=_new_client()).account()
    except Exception as e:  # noqa: BLE001
        raise TestFailed(str(e)) from None
    return {**a, "access": "drafts are disabled; ads only switch on inside an approved Launch"}


def test_github(ctx: dict) -> dict:
    from .connectors.github_repos import GitHub

    try:
        u = GitHub(os.environ["GITHUB_APP_TOKEN"], os.environ["GITHUB_OWNER"],
                   client=_new_client()).user()
    except Exception as e:  # noqa: BLE001
        raise TestFailed(str(e)) from None
    return {"signed_in_as": u.get("login"), "owner": os.environ["GITHUB_OWNER"],
            "access": "creates PRIVATE repos only, after you approve each push"}


def test_youtube(ctx: dict) -> dict:
    from .connectors.clips import YouTube

    try:
        YouTube(os.environ["YOUTUBE_API_KEY"], client=_new_client()).ping()
    except Exception as e:  # noqa: BLE001
        raise TestFailed(str(e)) from None
    return {"key": "accepted", "cost": "1 quota unit (free daily quota is 10,000)"}


def test_twitch(ctx: dict) -> dict:
    from .connectors.clips import Twitch

    try:
        r = Twitch(os.environ["TWITCH_CLIENT_ID"], os.environ["TWITCH_CLIENT_SECRET"],
                   client=_new_client()).ping()
    except Exception as e:  # noqa: BLE001
        raise TestFailed(str(e)) from None
    return {"app_login": "ok", "top_game_now": r.get("top_game")}


def test_amazon(ctx: dict) -> dict:
    from .connectors.marketplaces import Amazon

    try:
        r = Amazon(os.environ["AMAZON_SP_CLIENT_ID"], os.environ["AMAZON_SP_CLIENT_SECRET"],
                   os.environ["AMAZON_SP_REFRESH_TOKEN"], client=_new_client()).ping()
    except Exception as e:  # noqa: BLE001
        raise TestFailed(str(e)) from None
    return {"marketplaces": r["marketplaces"], "access": "read-only research"}


def test_ebay(ctx: dict) -> dict:
    from .connectors.marketplaces import Ebay

    try:
        r = Ebay(os.environ["EBAY_CLIENT_ID"], os.environ["EBAY_CLIENT_SECRET"],
                 client=_new_client()).search("test", 1)
    except Exception as e:  # noqa: BLE001
        raise TestFailed(str(e)) from None
    return {"app_login": "ok", "listings_for_test_search": r["total_listings"]}


def test_databento(ctx: dict) -> dict:
    from .connectors.apex7 import Databento

    try:
        ds = Databento(os.environ["DATABENTO_API_KEY"], client=_new_client()).datasets()
    except Exception as e:  # noqa: BLE001
        raise TestFailed(str(e)) from None
    return {"datasets": len(ds), "cme_futures": "GLBX.MDP3" in ds,
            "note": "free check; every data pull shows Databento's own price first"}


def test_apex7(ctx: dict) -> dict:
    from .connectors.apex7 import pull_results

    try:
        runs = pull_results()["runs"]
    except Exception as e:  # noqa: BLE001
        raise TestFailed(str(e)) from None
    return {"runs_published": len(runs),
            "trades_in_latest": len(runs[-1].get("trades") or []) if runs else 0}


# --- The list -----------------------------------------------------------------------------------

@dataclass
class Connector:
    id: str
    name: str
    needs: list[str]  # all required
    any_of: list[list[str]] = field(default_factory=list)  # alternatives (one group must be set)
    optional: list[str] = field(default_factory=list)
    test: Callable[[dict], dict] | None = None
    note: str = ""


CONNECTORS = [
    Connector("models", "Claude (thinking)", ["ANTHROPIC_API_KEY"], test=test_models),
    Connector("shopify", "Shopify store (drafts)", ["SHOPIFY_STORE_DOMAIN"],
              any_of=[["SHOPIFY_CLIENT_ID", "SHOPIFY_CLIENT_SECRET"],
                      ["SHOPIFY_ADMIN_API_TOKEN"]], test=test_shopify),
    Connector("social", "Social posting (Upload-Post)", ["UPLOADPOST_API_KEY", "UPLOADPOST_USER"],
              test=test_uploadpost),
    Connector("email", "Email (Resend)", ["RESEND_API_KEY", "RESEND_FROM"],
              optional=["RESEND_WEBHOOK_SECRET"], test=test_resend),
    Connector("video", "UGC video (fal.ai)", ["FAL_KEY"],
              optional=["FAL_VIDEO_MODEL", "FAL_VIDEO_CENTS_PER_SECOND"], test=test_fal),
    Connector("whop", "Whop (checkout, members, sales)", ["WHOP_API_KEY"],
              optional=["WHOP_WEBHOOK_SECRET"], test=test_whop),
    Connector("voice", "AI voice (ElevenLabs)", ["ELEVENLABS_API_KEY"],
              optional=["ELEVENLABS_MODEL", "ELEVENLABS_CENTS_PER_1K_CHARS"],
              test=test_elevenlabs),
    Connector("research", "Web research (Apify)", ["APIFY_TOKEN"], test=test_apify),
    Connector("discord", "Discord (roles, announcements)",
              ["DISCORD_BOT_TOKEN", "DISCORD_GUILD_ID", "DISCORD_MEMBER_ROLE_ID"],
              optional=["DISCORD_ANNOUNCE_CHANNEL_ID"], test=test_discord),
    Connector("telegram", "Telegram (group access, announcements)",
              ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"], optional=["TELEGRAM_WEBHOOK_SECRET"],
              test=test_telegram),
    Connector("realestate", "Real-estate data (BatchData)",
              ["BATCHDATA_API_TOKEN", "BATCHDATA_CENTS_PER_RECORD"], test=test_batchdata),
    Connector("broker", "Trading: Alpaca PAPER + market data",
              ["ALPACA_PAPER_KEY_ID", "ALPACA_PAPER_SECRET_KEY"], test=test_alpaca),
    Connector("meta_ads", "Ads: Meta (Facebook/Instagram)",
              ["META_ACCESS_TOKEN", "META_AD_ACCOUNT_ID"], optional=["META_PAGE_ID"],
              test=test_meta_ads),
    Connector("tiktok_ads", "Ads: TikTok", ["TIKTOK_ACCESS_TOKEN", "TIKTOK_ADVERTISER_ID"],
              optional=["TIKTOK_IDENTITY_ID", "TIKTOK_IDENTITY_TYPE"], test=test_tiktok_ads),
    Connector("github", "GitHub repos (App Factory)", ["GITHUB_APP_TOKEN", "GITHUB_OWNER"],
              optional=["GITHUB_REPO_PREFIX"], test=test_github),
    Connector("youtube", "YouTube (clip scouting)", ["YOUTUBE_API_KEY"],
              optional=["CLIP_ALLOWED_CREATORS"], test=test_youtube),
    Connector("twitch", "Twitch (clip scouting)", ["TWITCH_CLIENT_ID", "TWITCH_CLIENT_SECRET"],
              optional=["CLIP_ALLOWED_CREATORS"], test=test_twitch),
    Connector("amazon", "Amazon (SP-API research)",
              ["AMAZON_SP_CLIENT_ID", "AMAZON_SP_CLIENT_SECRET", "AMAZON_SP_REFRESH_TOKEN"],
              test=test_amazon),
    Connector("ebay", "eBay (Browse research)", ["EBAY_CLIENT_ID", "EBAY_CLIENT_SECRET"],
              test=test_ebay),
    Connector("databento", "Futures market data (Databento)", ["DATABENTO_API_KEY"],
              test=test_databento),
    Connector("apex7", "APEX-7 results feed", ["APEX7_RESULTS_URL"],
              optional=["APEX7_RESULTS_TOKEN"], test=test_apex7,
              note="or upload results: POST /trading/backtests"),
    Connector("dns", "Domain checks (DNS)", ["SITE_DOMAIN_VERIFY"],
              note="set to dns when you launch a site on your own domain; no key needed"),
    Connector("hosting", "Website hosting (Netlify)", ["NETLIFY_AUTH_TOKEN"],
              optional=["NETLIFY_SITE_PREFIX"], test=test_netlify),
    Connector("sms", "SMS and calls", [], note="not built yet: outreach is email only"),
    Connector("artifacts", "File storage (S3)", ["S3_BUCKET"],
              note="optional: files live on the Railway disk until this is set"),
]
BY_ID = {c.id: c for c in CONNECTORS}


def missing(c: Connector) -> list[str]:
    out = [v for v in c.needs if not os.environ.get(v)]
    if c.any_of and not any(all(os.environ.get(v) for v in group) for group in c.any_of):
        out += [v for v in c.any_of[0] if not os.environ.get(v)]  # suggest the first way
    return out


def status(c: Connector, last: dict | None) -> dict:
    """LIVE when configured (and the last test, if any, passed); ERROR when configured but
    the last test failed; MOCK when not configured or not built."""
    built = bool(c.needs or c.any_of)
    miss = missing(c) if built else []
    if not built or miss:
        mode = "MOCK"
    elif last is not None and not last.get("ok"):
        mode = "ERROR"
    else:
        mode = "LIVE"
    return {"id": c.id, "name": c.name, "mode": mode, "built": built,
            "missing": miss, "optional_unset": [v for v in c.optional if not os.environ.get(v)],
            "variables": sorted(set(c.needs + [v for g in c.any_of for v in g] + c.optional)),
            "testable": c.test is not None, "note": c.note, "last_test": last}


def run_test(c: Connector, ctx: dict) -> dict:
    if c.test is None:
        return {"ok": False, "error": "nothing to test for this connector"}
    miss = missing(c)
    if miss:
        return {"ok": False, "error": f"not configured: set {', '.join(miss)} in Railway"}
    started = time.monotonic()
    try:
        result = c.test(ctx)
        return {"ok": True, "result": {k: scrub(v) if isinstance(v, str) else v
                                       for k, v in result.items()},
                "ms": int((time.monotonic() - started) * 1000)}
    except httpx.HTTPError as e:
        return {"ok": False, "error": scrub(f"couldn't reach the service: {e}"),
                "ms": int((time.monotonic() - started) * 1000)}
    except Exception as e:  # noqa: BLE001 - every failure is shown, scrubbed
        return {"ok": False, "error": scrub(str(e)), "ms": int((time.monotonic() - started) * 1000)}
