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
"""

import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import httpx

SECRET_VARS = ("ANTHROPIC_API_KEY", "SHOPIFY_CLIENT_SECRET", "SHOPIFY_ADMIN_API_TOKEN",
               "UPLOADPOST_API_KEY", "RESEND_API_KEY", "RESEND_WEBHOOK_SECRET", "FAL_KEY",
               "CLERK_SECRET_KEY", "WHOP_API_KEY", "WHOP_WEBHOOK_SECRET")


def _new_client() -> httpx.Client:
    return httpx.Client(timeout=20)


def scrub(text: str) -> str:
    out = str(text)
    for name in SECRET_VARS:
        v = os.environ.get(name)
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
    with _new_client() as c:
        r = c.get("https://api.anthropic.com/v1/models", params={"limit": 20}, headers={
            "x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01"})
    body = _json(r)
    if r.status_code != 200:
        raise TestFailed(f"Anthropic refused the key: {_msg(body, r)}")
    ids = [m.get("id") for m in body.get("data", []) if isinstance(m, dict)]
    return {"models_available": len(ids), "examples": ids[:3]}


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
    Connector("dns", "Domain checks (DNS)", ["SITE_DOMAIN_VERIFY"],
              note="set to dns when you launch a site on your own domain; no key needed"),
    Connector("hosting", "Website hosting", [],
              note="not built yet: sites are previewed and checked, not published"),
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
