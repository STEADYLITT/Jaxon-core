"""SMS and calls through Twilio (ADR-059). MOCK until the three variables are set; then an
approved SMS/call campaign really texts or calls, with every CRM guard still in front:
prior consent, opt-outs, 8am-9pm recipient time, daily limit, approval, DB trigger.

- base  https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}
- auth  HTTP basic: account SID : auth token
- SMS   POST .../Messages.json  form To, From, Body                 -> {"sid": "SM..."}
- call  POST .../Calls.json     form To, From, Twiml=<Response><Say>  -> {"sid": "CA..."}
        (the approved script is read out; no recording, no AI voice pretending to be human)
- test  GET  .../Accounts/{sid}.json (the account) and IncomingPhoneNumbers.json?PhoneNumber
        (the From number really belongs to it). Costs nothing.
- inbound STOP: Twilio posts incoming texts to /webhooks/twilio/{tenant}; the request is
  verified with X-Twilio-Signature = base64(HMAC-SHA1(auth token, URL + sorted params)).
"""

import base64
import hashlib
import hmac
import os
import re
from xml.sax.saxutils import escape

import httpx

from ..worker_errors import NonRetryableError

API = "https://api.twilio.com/2010-04-01"
VARS = ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM_NUMBER")
STOP_WORDS = {"stop", "stopall", "unsubscribe", "cancel", "end", "quit", "revoke", "optout"}


def configured() -> bool:
    return all(os.environ.get(v) for v in VARS)


def _new_client() -> httpx.Client:
    return httpx.Client(timeout=30)


def e164(v: str) -> str:
    v = re.sub(r"[^\d+]", "", v or "")
    if not re.fullmatch(r"\+[1-9]\d{7,14}", v):
        raise NonRetryableError(f"not an E.164 phone number: {v[:20]!r}")
    return v


class Twilio:
    def __init__(self, sid: str, token: str, from_number: str,
                 client: httpx.Client | None = None):
        self.sid, self._auth, self.from_number = sid, (sid, token), e164(from_number)
        self._c = client or _new_client()

    def _post(self, path: str, form: dict) -> dict:
        r = self._c.post(f"{API}/Accounts/{self.sid}/{path}", data=form, auth=self._auth)
        return self._check(r)

    @staticmethod
    def _check(r: httpx.Response) -> dict:
        if r.status_code == 429 or r.status_code >= 500:
            r.raise_for_status()  # retryable
        try:
            body = r.json()
        except ValueError:
            body = {}
        if r.status_code >= 400:
            raise NonRetryableError(f"Twilio refused: {body.get('message') or r.status_code} "
                                    f"(code {body.get('code')})")
        return body

    def sms(self, to: str, body: str) -> str:
        return str(self._post("Messages.json", {"To": e164(to), "From": self.from_number,
                                                "Body": body})["sid"])

    def call(self, to: str, script: str) -> str:
        twiml = f"<Response><Say>{escape(script)}</Say></Response>"
        return str(self._post("Calls.json", {"To": e164(to), "From": self.from_number,
                                             "Twiml": twiml})["sid"])

    def account(self) -> dict:
        a = self._check(self._c.get(f"{API}/Accounts/{self.sid}.json", auth=self._auth))
        nums = self._check(self._c.get(f"{API}/Accounts/{self.sid}/IncomingPhoneNumbers.json",
                                       params={"PhoneNumber": self.from_number},
                                       auth=self._auth))
        owned = bool(nums.get("incoming_phone_numbers"))
        return {"account": a.get("friendly_name"), "status": a.get("status"),
                "type": a.get("type"), "from_number_owned": owned}


def twilio() -> Twilio:
    if not configured():
        raise NonRetryableError("set " + ", ".join(VARS) + " in Railway for SMS and calls")
    return Twilio(os.environ["TWILIO_ACCOUNT_SID"], os.environ["TWILIO_AUTH_TOKEN"],
                  os.environ["TWILIO_FROM_NUMBER"])


def valid_signature(token: str, url: str, params: dict, signature: str) -> bool:
    """Twilio's request signature: HMAC-SHA1 over the full URL plus every POST parameter
    (sorted by name, name+value appended), base64."""
    data = url + "".join(f"{k}{params[k]}" for k in sorted(params))
    mac = base64.b64encode(hmac.new(token.encode(), data.encode(), hashlib.sha1).digest())
    return bool(token) and hmac.compare_digest(mac.decode(), signature or "")


def is_stop(text: str) -> bool:
    return (text or "").strip().lower().strip(".!") in STOP_WORDS
