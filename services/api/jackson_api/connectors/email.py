"""Email sending via Resend (ADR-035).

Checked against resend.com/docs (2026-11-27):
- POST https://api.resend.com/emails, Authorization: Bearer <key>
- JSON: from, to[], subject, text, headers{}
- Idempotency-Key header (unique per request, 24h): retries never send twice
- one-click unsubscribe: headers List-Unsubscribe: <url> and
  List-Unsubscribe-Post: List-Unsubscribe=One-Click (RFC 8058; our /u/{token} accepts
  the POST)
- webhooks are signed with Svix: svix-id, svix-timestamp, svix-signature

Settings (names only): RESEND_API_KEY, RESEND_FROM ("Name <you@your-verified-domain>"),
RESEND_WEBHOOK_SECRET (for bounce/complaint events). Without the key, sending is a
labeled MOCK. Campaigns still need the owner's approval or a covering Launch approval.
"""

import base64
import hashlib
import hmac
import os
import re
import time
import uuid
from email.utils import parseaddr

import httpx

from ..worker_errors import NonRetryableError

API = "https://api.resend.com/emails"


def _new_client() -> httpx.Client:
    return httpx.Client(timeout=30)


def _domain(addr: str) -> str:
    return (parseaddr(addr)[1].rsplit("@", 1)[-1] or "").lower()


class ResendSender:
    backend = "resend"

    def __init__(self, api_key: str, default_from: str, client: httpx.Client | None = None):
        self._headers = {"Authorization": f"Bearer {api_key}",
                         "Content-Type": "application/json"}
        self.default_from = default_from
        self.verified_domain = _domain(default_from)
        self._client = client or _new_client()

    def sender_for(self, name: str, email: str) -> str:
        """Use the campaign's sender only if it's on the verified domain."""
        if email and _domain(email) != self.verified_domain:
            raise NonRetryableError(
                f"sender {email} isn't on the verified sending domain {self.verified_domain}")
        if not email:
            return self.default_from
        clean = re.sub(r'["<>\r\n]', "", name or "").strip()
        return f"{clean} <{email}>" if clean else email

    def send(self, *, to: str, subject: str, text: str, from_addr: str,
             unsubscribe_url: str, idempotency_key: str) -> str:
        r = self._client.post(API, headers={**self._headers, "Idempotency-Key": idempotency_key},
                              json={"from": from_addr, "to": [to], "subject": subject,
                                    "text": text,
                                    "headers": {
                                        "List-Unsubscribe": f"<{unsubscribe_url}>",
                                        "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"}})
        if r.status_code == 429 or r.status_code >= 500:
            r.raise_for_status()  # retryable; the Idempotency-Key prevents a double send
        try:
            body = r.json()
        except ValueError:
            body = {}
        if r.status_code >= 400 or "id" not in body:
            raise NonRetryableError(
                f"Resend refused the email: {str(body.get('message') or r.status_code)[:300]}")
        return str(body["id"])


def configured() -> bool:
    return bool(os.environ.get("RESEND_API_KEY") and os.environ.get("RESEND_FROM"))


def email_backend() -> ResendSender | None:
    if not configured():
        return None
    return ResendSender(os.environ["RESEND_API_KEY"], os.environ["RESEND_FROM"])


# --- Webhook signatures (Svix scheme used by Resend) ----------------------------------------

def verify_webhook(secret: str, headers: dict, body: bytes, tolerance_s: int = 300) -> bool:
    msg_id = headers.get("svix-id") or ""
    ts = headers.get("svix-timestamp") or ""
    sigs = headers.get("svix-signature") or ""
    if not (msg_id and ts.isdigit() and sigs and secret):
        return False
    if abs(time.time() - int(ts)) > tolerance_s:
        return False  # stale or replayed
    key = base64.b64decode(secret.split("_", 1)[1] if secret.startswith("whsec_") else secret)
    expected = base64.b64encode(hmac.new(
        key, f"{msg_id}.{ts}.".encode() + body, hashlib.sha256).digest()).decode()
    return any(hmac.compare_digest(expected, part.split(",", 1)[1])
               for part in sigs.split() if part.startswith("v1,"))


def new_id() -> str:  # pragma: no cover - convenience for scripts
    return uuid.uuid4().hex
