"""Real-estate data via BatchData: comps and skip tracing, for research only (ADR-046).

Why BatchData and not PropStream: PropStream has no public, self-serve developer API.
BatchData documents REST endpoints for comps, property lookup and skip trace with one token.

Checked against developer.batchdata.com (2026-11-27):
- base https://api.batchdata.com/api/v1, `Authorization: Bearer <BATCHDATA_API_TOKEN>`
- POST /property/search {searchCriteria: {query, compAddress: {street, city, state, zip}},
  options: {useDistance, distanceMiles, useBedrooms, useArea, useYearBuilt, take, skip}}
  -> comparable properties near the subject (each returned record is billed)
- POST /property/skip-trace {requests: [{propertyAddress: {street, city, state, zip}}]}
  -> results.persons[] {name, emails, phoneNumbers [{number, type, dnc}], dnc, litigator};
  results.meta.results.matchCount is what's billed. TCPA-listed phones are dropped by
  default.
- No free test endpoint exists, so the connection "test" only checks the token is set and
  well-formed; the first real search is the proof.

Rules:
- Research and simulation only. Nothing here contacts anyone.
- Skip tracing returns personal contact data. Each run needs the owner's approval, and
  rows are kept only for the deal they were pulled for. Numbers on the Do-Not-Call list or
  flagged as litigators are marked and never put on a call list.
- Each call is a SPEND estimated from BATCHDATA_CENTS_PER_RECORD (owner's plan price;
  required, since BatchData prices by plan) and approved first.
"""

import os

import httpx

from ..worker_errors import NonRetryableError

API = "https://api.batchdata.com/api/v1"
MAX_COMPS = 25


def configured() -> bool:
    return bool(os.environ.get("BATCHDATA_API_TOKEN"))


def cents_per_record() -> int:
    v = os.environ.get("BATCHDATA_CENTS_PER_RECORD")
    if not v or not v.isdigit():
        raise NonRetryableError("set BATCHDATA_CENTS_PER_RECORD (your BatchData plan's price "
                                "per record, in cents) so Jackson can show the cost first")
    return int(v)


def address(a: dict) -> dict:
    out = {k: str(a.get(k) or "").strip()[:120] for k in ("street", "city", "state", "zip")}
    if not out["street"] or not (out["zip"] or (out["city"] and out["state"])):
        raise NonRetryableError("an address needs street + zip, or street + city + state")
    return {k: v for k, v in out.items() if v}


def _new_client() -> httpx.Client:
    return httpx.Client(timeout=60)


def _check(r: httpx.Response, what: str) -> dict:
    if r.status_code == 429 or r.status_code >= 500:
        r.raise_for_status()
    try:
        body = r.json()
    except ValueError:
        body = {}
    if r.status_code >= 400:
        st = body.get("status") if isinstance(body, dict) else None
        msg = (st or {}).get("message") if isinstance(st, dict) else body.get("message")
        raise NonRetryableError(f"BatchData refused the {what}: {str(msg or r.status_code)[:300]}")
    return body


def _prop(p: dict) -> dict:
    a = p.get("address") or {}
    b = p.get("building") or {}
    s = p.get("sale") or {}
    v = p.get("valuation") or {}
    last = s.get("lastSale") or s
    return {"address": ", ".join(str(a.get(k)) for k in ("street", "city", "state", "zip")
                                 if a.get(k)),
            "bedrooms": b.get("bedroomCount") or b.get("bedrooms"),
            "bathrooms": b.get("bathroomCount") or b.get("bathrooms"),
            "sqft": b.get("livingAreaSquareFeet") or b.get("totalBuildingAreaSquareFeet"),
            "year_built": b.get("yearBuilt"),
            "last_sale_price": last.get("price") or last.get("salePrice"),
            "last_sale_date": last.get("saleDate") or last.get("lastSaleDate"),
            "estimated_value": v.get("estimatedValue"), "source": "BatchData"}


class BatchData:
    def __init__(self, token: str, client: httpx.Client | None = None):
        self._h = {"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                   "Accept": "application/json"}
        self._client = client or _new_client()

    def comps(self, subject: dict, miles: float = 1.0, take: int = 10) -> list[dict]:
        sub = address(subject)
        body = _check(self._client.post(f"{API}/property/search", headers=self._h, json={
            "searchCriteria": {"query": f"{sub.get('city', '')}, {sub.get('state', '')}"
                               .strip(", ") or sub.get("zip", ""), "compAddress": sub},
            "options": {"useDistance": True, "distanceMiles": max(0.25, min(5.0, miles)),
                        "useBedrooms": True, "useArea": True, "useYearBuilt": True,
                        "skip": 0, "take": max(1, min(MAX_COMPS, take))}}), "comps search")
        props = ((body.get("results") or {}).get("properties")) or []
        return [_prop(p) for p in props if isinstance(p, dict)]

    def skip_trace(self, prop: dict) -> dict:
        body = _check(self._client.post(f"{API}/property/skip-trace", headers=self._h, json={
            "requests": [{"propertyAddress": address(prop)}]}), "skip trace")
        res = body.get("results") or {}
        people = []
        for p in res.get("persons") or []:
            name = p.get("name") or {}
            people.append({
                "name": " ".join(x for x in (name.get("first"), name.get("last")) if x),
                "phones": [{"number": ph.get("number"), "type": ph.get("type"),
                            "dnc": bool(ph.get("dnc")), "reachable": ph.get("reachable")}
                           for ph in p.get("phoneNumbers") or []],
                "emails": [e.get("email") for e in p.get("emails") or [] if e.get("email")],
                "dnc": bool(p.get("dnc")), "litigator": bool(p.get("litigator"))})
        matched = ((res.get("meta") or {}).get("results") or {}).get("matchCount")
        return {"people": people, "matched": int(matched or (1 if people else 0))}


def client() -> BatchData:
    if not configured():
        raise NonRetryableError("real-estate data isn't connected: set BATCHDATA_API_TOKEN")
    return BatchData(os.environ["BATCHDATA_API_TOKEN"])
