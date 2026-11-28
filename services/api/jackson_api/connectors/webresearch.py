"""Web research via Apify: Google search results, Google Trends and Google Maps places
(local business leads) (ADR-043).

Checked against docs.apify.com and each Actor's public input schema (2026-11-27):
- auth `Authorization: Bearer <APIFY_TOKEN>`
- GET https://api.apify.com/v2/users/me -> {data: {username, plan: {...}}} (connection test)
- POST /v2/acts/{username~actor}/run-sync-get-dataset-items?timeout=&maxTotalChargeUsd=
  with the Actor input as JSON -> the dataset items (JSON array). HTTP 408 after 300 s.
  maxTotalChargeUsd caps what one run may cost.
- apify~google-search-scraper: {queries: "one per line", maxPagesPerQuery, countryCode}
  -> [{searchQuery: {term}, organicResults: [{title, url, description, position}]}]
  (from $1.80 per 1,000 result pages)
- apify~google-trends-scraper: {searchTerms: [...], timeRange: "today 3-m", geo}
  (from $3.00 per 1,000 results)
- compass~crawler-google-places: {searchStringsArray: [...], locationQuery,
  maxCrawledPlacesPerSearch} -> [{title, categoryName, address, website, phone,
  totalScore, reviewsCount, url}] (from $1.50 per 1,000 places)

Every run is a SPEND capped by maxTotalChargeUsd and approved first (or covered by a Launch
with budget left). Every result keeps its source URL. Without APIFY_TOKEN research falls
back to the links in the command (the existing research task); nothing is invented.
"""

import math
import os

import httpx

from ..worker_errors import NonRetryableError

API = "https://api.apify.com/v2"
ACTORS = {
    "search": ("apify~google-search-scraper", 1.80, "result pages"),
    "trends": ("apify~google-trends-scraper", 3.00, "results"),
    "places": ("compass~crawler-google-places", 1.50, "places"),
}
MAX_ITEMS = 200  # per run
MIN_CHARGE_CENTS = 5  # Actor start overhead, rounded up


def configured() -> bool:
    return bool(os.environ.get("APIFY_TOKEN"))


def estimate_cents(kind: str, items: int) -> int:
    """Upper bound for one run; also sent to Apify as maxTotalChargeUsd."""
    _, per_1k, _ = ACTORS[kind]
    return max(MIN_CHARGE_CENTS, math.ceil(items * per_1k / 10))  # $/1k items -> cents


def build_input(kind: str, terms: list[str], location: str = "", items: int = 20,
                country: str = "us") -> dict:
    terms = [t.strip()[:200] for t in terms if t and t.strip()][:10]
    if not terms:
        raise NonRetryableError("give at least one search term")
    items = max(1, min(int(items), MAX_ITEMS))
    if kind == "search":
        return {"queries": "\n".join(terms), "countryCode": country.lower()[:2],
                "maxPagesPerQuery": max(1, min(3, math.ceil(items / 10 / len(terms))))}
    if kind == "trends":
        return {"searchTerms": terms, "timeRange": "today 3-m",
                "geo": country.upper()[:2] if country else "", "maxItems": items}
    if kind == "places":
        if not location.strip():
            raise NonRetryableError("places search needs a location, e.g. 'Austin, TX, USA'")
        return {"searchStringsArray": terms, "locationQuery": location.strip()[:200],
                "maxCrawledPlacesPerSearch": max(1, items // len(terms)), "language": "en"}
    raise NonRetryableError(f"kind must be one of {sorted(ACTORS)}")


def normalize(kind: str, items: list) -> list[dict]:
    """Every row keeps where it came from; rows with no source URL are dropped."""
    out = []
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict):
            continue
        if kind == "search":
            term = (it.get("searchQuery") or {}).get("term")
            for r in it.get("organicResults") or []:
                if str(r.get("url", "")).startswith("http"):
                    out.append({"term": term, "title": r.get("title"), "url": r["url"],
                                "snippet": r.get("description"), "rank": r.get("position")})
        elif kind == "trends":
            term = it.get("searchTerm") or it.get("inputUrlOrTerm")
            series = it.get("interestOverTime_timelineData") or []
            vals = [int((p.get("value") or [0])[0]) for p in series
                    if isinstance(p, dict) and p.get("value")]
            out.append({"term": term, "url": "https://trends.google.com/trends/explore?q="
                        + str(term or "").replace(" ", "+"),
                        "points": len(vals), "latest": vals[-1] if vals else None,
                        "average": round(sum(vals) / len(vals), 1) if vals else None,
                        "rising": [q.get("query") for q in (it.get("relatedQueries_rising")
                                                             or [])[:10] if isinstance(q, dict)]})
        elif kind == "places":
            if str(it.get("url", "")).startswith("http"):
                out.append({"name": it.get("title"), "category": it.get("categoryName"),
                            "address": it.get("address"), "website": it.get("website"),
                            "phone": it.get("phone"), "rating": it.get("totalScore"),
                            "reviews": it.get("reviewsCount"), "url": it["url"]})
    return out


def _new_client() -> httpx.Client:
    return httpx.Client(timeout=330)


class Apify:
    def __init__(self, token: str, client: httpx.Client | None = None):
        self._h = {"Authorization": f"Bearer {token}"}
        self._client = client or _new_client()

    def me(self) -> dict:
        r = self._client.get(f"{API}/users/me", headers=self._h)
        if r.status_code != 200:
            raise NonRetryableError(f"Apify refused the token: HTTP {r.status_code}")
        return (r.json() or {}).get("data") or {}

    def run(self, kind: str, actor_input: dict, cap_cents: int) -> list:
        actor = ACTORS[kind][0]
        r = self._client.post(f"{API}/acts/{actor}/run-sync-get-dataset-items",
                              params={"timeout": 280, "maxTotalChargeUsd": f"{cap_cents / 100:.2f}",
                                      "format": "json", "clean": "true"},
                              headers=self._h, json=actor_input)
        if r.status_code == 429 or r.status_code >= 500 or r.status_code == 408:
            r.raise_for_status()  # retryable
        if r.status_code >= 400:
            try:
                msg = (r.json().get("error") or {}).get("message")
            except ValueError:
                msg = None
            raise NonRetryableError(f"Apify refused the run: {str(msg or r.status_code)[:300]}")
        body = r.json()
        return body if isinstance(body, list) else []


def client() -> Apify:
    if not configured():
        raise NonRetryableError("web research isn't connected: set APIFY_TOKEN in Railway")
    return Apify(os.environ["APIFY_TOKEN"])
