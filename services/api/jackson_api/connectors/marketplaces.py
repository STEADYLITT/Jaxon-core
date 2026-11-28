"""Amazon and eBay product research (ADR-053): real prices, sales ranks and fees, cited.

Amazon Selling Partner API (developer-docs.amazon.com/sp-api; no AWS request signing since
2023, only a Login with Amazon token):
- LWA: POST https://api.amazon.com/auth/o2/token grant_type=refresh_token, refresh_token,
  client_id, client_secret -> {access_token, expires_in}; header x-amz-access-token
- base https://sellingpartnerapi-na.amazon.com (North America), marketplace ATVPDKIKX0DER (US)
- GET /sellers/v1/marketplaceParticipations                        (connection test)
- GET /catalog/2022-04-01/items?keywords=&marketplaceIds=&includedData=summaries,salesRanks
  -> {items: [{asin, summaries: [{itemName, brand}], salesRanks: [{displayGroupRanks:
     [{title, rank}], classificationRanks: [{title, rank}]}]}]}
- GET /products/pricing/v0/items/{asin}/offers?MarketplaceId=&ItemCondition=New
  -> {payload: {Summary: {LowestPrices: [{fulfillmentChannel, LandedPrice: {Amount}}],
     BuyBoxPrices: [{LandedPrice: {Amount}}], TotalOfferCount}}}
- POST /products/fees/v0/items/{asin}/feesEstimate {FeesEstimateRequest: {MarketplaceId,
  IsAmazonFulfilled: true, PriceToEstimateFees: {ListingPrice: {CurrencyCode, Amount}},
  Identifier}} -> {payload: {FeesEstimateResult: {FeesEstimate: {TotalFeesEstimate:
  {Amount}}}}}
eBay Buy Browse API (developer.ebay.com):
- app token: POST https://api.ebay.com/identity/v1/oauth2/token (basic auth client id:secret)
  grant_type=client_credentials, scope=https://api.ebay.com/oauth/api_scope
- GET /buy/browse/v1/item_summary/search?q=&limit<=200 header X-EBAY-C-MARKETPLACE-ID
  -> {total, itemSummaries: [{itemId, title, price: {value, currency}, condition,
     itemWebUrl}]}. These are ACTIVE listings (asking prices), not sold prices.
Read-only and free (API rate limits only). Nothing is listed or bought.
"""

import os
import statistics
import time
from decimal import Decimal, InvalidOperation

import httpx

from ..worker_errors import NonRetryableError

LWA = "https://api.amazon.com/auth/o2/token"
SP = "https://sellingpartnerapi-na.amazon.com"
US = "ATVPDKIKX0DER"
EBAY_TOKEN = "https://api.ebay.com/identity/v1/oauth2/token"
EBAY = "https://api.ebay.com/buy/browse/v1"


def amazon_configured() -> bool:
    return all(os.environ.get(v) for v in ("AMAZON_SP_CLIENT_ID", "AMAZON_SP_CLIENT_SECRET",
                                           "AMAZON_SP_REFRESH_TOKEN"))


def ebay_configured() -> bool:
    return bool(os.environ.get("EBAY_CLIENT_ID") and os.environ.get("EBAY_CLIENT_SECRET"))


def _new_client() -> httpx.Client:
    return httpx.Client(timeout=30)


def cents(x) -> int | None:
    try:
        return int((Decimal(str(x)) * 100).quantize(Decimal(1)))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _check(r: httpx.Response, who: str, what: str) -> dict:
    if r.status_code == 429 or r.status_code >= 500:
        r.raise_for_status()
    try:
        body = r.json()
    except ValueError:
        body = {}
    if r.status_code >= 400:
        errs = body.get("errors") if isinstance(body, dict) else None
        msg = (errs[0].get("message") if isinstance(errs, list) and errs and
               isinstance(errs[0], dict) else None) or body.get("error_description") or \
            body.get("message")
        raise NonRetryableError(f"{who} refused the {what}: {str(msg or r.status_code)[:300]}")
    return body if isinstance(body, dict) else {"data": body}


class Amazon:
    def __init__(self, client_id: str, secret: str, refresh: str,
                 client: httpx.Client | None = None):
        self._cid, self._secret, self._refresh = client_id, secret, refresh
        self._client = client or _new_client()
        self._token, self._exp = None, 0.0

    def _h(self) -> dict:
        if not self._token or time.time() > self._exp - 60:
            b = _check(self._client.post(LWA, data={
                "grant_type": "refresh_token", "refresh_token": self._refresh,
                "client_id": self._cid, "client_secret": self._secret}), "Amazon", "login")
            self._token = b.get("access_token")
            self._exp = time.time() + int(b.get("expires_in") or 3600)
        return {"x-amz-access-token": self._token, "Accept": "application/json"}

    def ping(self) -> dict:
        b = _check(self._client.get(f"{SP}/sellers/v1/marketplaceParticipations",
                                    headers=self._h()), "Amazon", "seller lookup")
        mk = [((p.get("marketplace") or {}).get("countryCode")) for p in b.get("payload") or []]
        return {"marketplaces": [m for m in mk if m]}

    def search(self, keywords: str, limit: int = 10) -> list[dict]:
        b = _check(self._client.get(f"{SP}/catalog/2022-04-01/items", headers=self._h(), params={
            "keywords": keywords[:200], "marketplaceIds": US,
            "includedData": "summaries,salesRanks", "pageSize": max(1, min(limit, 20))}),
            "Amazon", "catalog search")
        out = []
        for it in b.get("items") or []:
            summ = (it.get("summaries") or [{}])[0]
            ranks = (it.get("salesRanks") or [{}])[0]
            top = (ranks.get("displayGroupRanks") or ranks.get("classificationRanks") or [{}])[0]
            out.append({"asin": it.get("asin"), "title": summ.get("itemName"),
                        "brand": summ.get("brand"), "rank": top.get("rank"),
                        "rank_category": top.get("title"),
                        "url": f"https://www.amazon.com/dp/{it.get('asin')}"})
        return out

    def price(self, asin: str) -> dict:
        b = _check(self._client.get(f"{SP}/products/pricing/v0/items/{asin}/offers",
                                    headers=self._h(), params={"MarketplaceId": US,
                                                               "ItemCondition": "New"}),
                   "Amazon", "offers")
        s = (b.get("payload") or {}).get("Summary") or {}
        bb = [cents((p.get("LandedPrice") or {}).get("Amount")) for p in
              s.get("BuyBoxPrices") or []]
        low = [cents((p.get("LandedPrice") or {}).get("Amount")) for p in
               s.get("LowestPrices") or []]
        bb, low = [x for x in bb if x], [x for x in low if x]
        return {"buy_box_cents": bb[0] if bb else None,
                "lowest_cents": min(low) if low else None,
                "offers": s.get("TotalOfferCount")}

    def fba_fees(self, asin: str, price_cents: int) -> int | None:
        b = _check(self._client.post(f"{SP}/products/fees/v0/items/{asin}/feesEstimate",
                                     headers=self._h(), json={"FeesEstimateRequest": {
            "MarketplaceId": US, "IsAmazonFulfilled": True, "Identifier": f"jackson-{asin}",
            "PriceToEstimateFees": {"ListingPrice": {"CurrencyCode": "USD",
                                                     "Amount": round(price_cents / 100, 2)}}}}),
            "Amazon", "fee estimate")
        fe = ((b.get("payload") or {}).get("FeesEstimateResult") or {}).get("FeesEstimate") or {}
        return cents((fe.get("TotalFeesEstimate") or {}).get("Amount"))


class Ebay:
    def __init__(self, client_id: str, secret: str, client: httpx.Client | None = None):
        self._auth = (client_id, secret)
        self._client = client or _new_client()
        self._token, self._exp = None, 0.0

    def _h(self) -> dict:
        if not self._token or time.time() > self._exp - 60:
            b = _check(self._client.post(EBAY_TOKEN, auth=self._auth, data={
                "grant_type": "client_credentials",
                "scope": "https://api.ebay.com/oauth/api_scope"}), "eBay", "app login")
            self._token = b.get("access_token")
            self._exp = time.time() + int(b.get("expires_in") or 7200)
        return {"Authorization": f"Bearer {self._token}",
                "X-EBAY-C-MARKETPLACE-ID": "EBAY_US"}

    def search(self, q: str, limit: int = 50) -> dict:
        b = _check(self._client.get(f"{EBAY}/item_summary/search", headers=self._h(), params={
            "q": q[:200], "limit": max(1, min(limit, 200))}), "eBay", "search")
        items = []
        for it in b.get("itemSummaries") or []:
            p = cents((it.get("price") or {}).get("value"))
            if p and str(it.get("itemWebUrl", "")).startswith("https://"):
                items.append({"title": it.get("title"), "price_cents": p,
                              "condition": it.get("condition"), "url": it["itemWebUrl"]})
        prices = sorted(i["price_cents"] for i in items)
        return {"total_listings": b.get("total"), "sampled": len(items),
                "median_asking_cents": int(statistics.median(prices)) if prices else None,
                "low_cents": prices[0] if prices else None,
                "high_cents": prices[-1] if prices else None, "examples": items[:10],
                "note": "active listings (asking prices), not sold prices"}


def amazon() -> Amazon:
    if not amazon_configured():
        raise NonRetryableError("set AMAZON_SP_CLIENT_ID, AMAZON_SP_CLIENT_SECRET and "
                                "AMAZON_SP_REFRESH_TOKEN in Railway")
    return Amazon(os.environ["AMAZON_SP_CLIENT_ID"], os.environ["AMAZON_SP_CLIENT_SECRET"],
                  os.environ["AMAZON_SP_REFRESH_TOKEN"])


def ebay() -> Ebay:
    if not ebay_configured():
        raise NonRetryableError("set EBAY_CLIENT_ID and EBAY_CLIENT_SECRET in Railway")
    return Ebay(os.environ["EBAY_CLIENT_ID"], os.environ["EBAY_CLIENT_SECRET"])
