"""Whop: course checkout links, members (who has access), and real sales numbers (ADR-041).

Checked against docs.whop.com (2026-11-27, "Current API" = REST v1):
- base https://api.whop.com/api/v1, `Authorization: Bearer <WHOP_API_KEY>` (server only),
  `Api-Version-Date` pins the response shapes (without it Whop answers in 2025-01-01 shapes)
- GET /accounts/me                -> the account behind the key (the connection test)
- GET /memberships?first=100&after=  -> {data: [Membership], page_info: {end_cursor,
  has_next_page}}; Membership: id (mem_), status, user_id, member_id, product_id,
  variant_id (plan_), external_accounts [{platform: discord|telegram, external_id}],
  current_period_end, metadata
- GET /payments?first=100&after=  -> Payment: id, status (paid ...), substatus, currency,
  total / refunded_amount as Money {amount: "10.00" (dollars, a string), currency: "usd"},
  created_at, product_id, variant_id, membership_id, metadata
- POST /checkout_configurations {variant_id, metadata} -> {id, purchase_url}; payments
  made through it carry its metadata (we put the business and course ids there)
- Webhooks follow Standard Webhooks: webhook-id / webhook-timestamp / webhook-signature
  ("v1,<base64 HMAC-SHA256 of id.timestamp.body>"), 5 minute tolerance. Events used:
  payment.succeeded, refund.created, membership.activated, membership.updated.

Key permissions to tick: member:basic:read, payment:basic:read, checkout_configuration:create.
Without WHOP_API_KEY nothing is synced and no checkout link exists: the course pages say so.
Nothing here charges anyone: Whop runs the checkout, Jackson only links to it and records
what Whop reports.
"""

import base64
import hashlib
import hmac
import os
import time
from decimal import Decimal, InvalidOperation

import httpx

from ..worker_errors import NonRetryableError

API = "https://api.whop.com/api/v1"
VERSION_DATE = "2026-11-25"
MAX_PAGES = 50  # 5,000 rows per sync, newest first; the webhook keeps it current after that
ACCESS = ("active", "trialing", "completed")  # statuses that grant access (Whop docs)


def configured() -> bool:
    return bool(os.environ.get("WHOP_API_KEY"))


def money_cents(m) -> int:
    """Whop Money {amount: "10.00"} (or a bare number on older shapes) -> cents."""
    if isinstance(m, dict):
        m = m.get("amount")
    if m in (None, ""):
        return 0
    try:
        return int((Decimal(str(m)) * 100).quantize(Decimal(1)))
    except InvalidOperation:
        return 0


def money_currency(m, fallback: str = "usd") -> str:
    cur = m.get("currency") if isinstance(m, dict) else None
    return str(cur or fallback or "usd").upper()[:3]


def _new_client() -> httpx.Client:
    return httpx.Client(timeout=30)


def _check(r: httpx.Response, what: str) -> dict:
    if r.status_code == 429 or r.status_code >= 500:
        r.raise_for_status()  # retryable
    try:
        body = r.json()
    except ValueError:
        body = {}
    if r.status_code >= 400:
        err = body.get("error") if isinstance(body, dict) else None
        msg = err.get("message") if isinstance(err, dict) else err
        raise NonRetryableError(f"Whop refused the {what}: {str(msg or r.status_code)[:300]}")
    return body if isinstance(body, dict) else {}


class Whop:
    def __init__(self, key: str, client: httpx.Client | None = None):
        self._headers = {"Authorization": f"Bearer {key}", "Api-Version-Date": VERSION_DATE,
                         "Accept": "application/json"}
        self._client = client or _new_client()

    def me(self) -> dict:
        return _check(self._client.get(f"{API}/accounts/me", headers=self._headers), "key")

    def _pages(self, path: str, what: str):
        after = None
        for _ in range(MAX_PAGES):
            params = {"first": 100, "order": "created_at", "direction": "desc"}
            if after:
                params["after"] = after
            body = _check(self._client.get(f"{API}{path}", params=params,
                                           headers=self._headers), what)
            yield from body.get("data") or []
            info = body.get("page_info") or {}
            after = info.get("end_cursor")
            if not (info.get("has_next_page") and after):
                return

    def memberships(self):
        return self._pages("/memberships", "membership list")

    def payments(self):
        return self._pages("/payments", "payment list")

    def payment(self, payment_id: str) -> dict:
        return _check(self._client.get(f"{API}/payments/{payment_id}", headers=self._headers),
                      "payment")

    def checkout(self, variant_id: str, metadata: dict) -> dict:
        body = _check(self._client.post(f"{API}/checkout_configurations", headers=self._headers,
                                        json={"variant_id": variant_id, "metadata": metadata}),
                      "checkout link")
        url = body.get("purchase_url") or ""
        if not url.startswith("https://"):
            raise NonRetryableError("Whop returned no checkout link")
        return {"id": body.get("id"), "purchase_url": url}


def client() -> Whop:
    if not configured():
        raise NonRetryableError("Whop isn't connected: set WHOP_API_KEY in Railway")
    return Whop(os.environ["WHOP_API_KEY"])


def _key(secret: str) -> bytes:
    """Svix-style `whsec_<base64>` secrets are decoded; Whop's own `ws_...` secret is used
    as given (Whop's SDK base64-encodes it, Standard Webhooks decodes it again)."""
    if secret.startswith("whsec_"):
        return base64.b64decode(secret.split("_", 1)[1])
    return secret.encode()


def verify_webhook(secret: str, headers: dict, body: bytes, tolerance_s: int = 300) -> bool:
    msg_id = headers.get("webhook-id") or ""
    ts = headers.get("webhook-timestamp") or ""
    sigs = headers.get("webhook-signature") or ""
    if not (msg_id and ts.isdigit() and sigs and secret):
        return False
    if abs(time.time() - int(ts)) > tolerance_s:
        return False  # stale or replayed
    expected = base64.b64encode(hmac.new(
        _key(secret), f"{msg_id}.{ts}.".encode() + body, hashlib.sha256).digest()).decode()
    return any(hmac.compare_digest(expected, part.split(",", 1)[1])
               for part in sigs.split() if part.startswith("v1,"))


def sign(secret: str, msg_id: str, ts: int, body: bytes) -> str:
    """The header Whop would send (used by tests and the Test button's docs)."""
    return "v1," + base64.b64encode(hmac.new(
        _key(secret), f"{msg_id}.{ts}.".encode() + body, hashlib.sha256).digest()).decode()
