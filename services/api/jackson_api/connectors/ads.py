"""Paid ads on Meta (Facebook/Instagram) and TikTok: drafts first, spend only under an
approved Launch (ADR-048).

Meta Marketing API v27.0 (developers.facebook.com, 2026-11-27):
- https://graph.facebook.com/v27.0, `access_token` = a System User token with ads_management
- GET  act_{id}?fields=name,account_status,currency,amount_spent      (connection test)
- POST act_{id}/campaigns {name, objective: OUTCOME_TRAFFIC|..., status: PAUSED,
  special_ad_categories: [] , is_adset_budget_sharing_enabled: false} -> {id}
- POST act_{id}/adsets {name, campaign_id, daily_budget (cents), billing_event: IMPRESSIONS,
  optimization_goal: LINK_CLICKS, targeting: {geo_locations: {countries: [..]}},
  status: PAUSED} -> {id}
- POST {object_id} {status: ACTIVE|PAUSED}                 -> {success: true}
TikTok Marketing API v1.3 (business-api.tiktok.com):
- https://business-api.tiktok.com/open_api/v1.3, header `Access-Token`
- GET  advertiser/info/?advertiser_ids=["id"]               (connection test)
- POST campaign/create/ {advertiser_id, campaign_name, objective_type: TRAFFIC,
  budget_mode: BUDGET_MODE_DAY, budget (dollars), operation_status: DISABLE}
  -> {code: 0, data: {campaign_id}}
- POST campaign/status/update/ {advertiser_id, campaign_ids: [..],
  operation_status: ENABLE|DISABLE}

Rules:
- Every campaign is created PAUSED (Meta) / DISABLE (TikTok): a draft that spends nothing.
- Turning one on is a SPEND: allowed only inside an ACTIVE Launch whose channels name the
  platform ("ads": ["meta"] / ["tiktok"]), with the daily budget inside the launch's daily
  cap and remaining budget. Nothing else can switch an ad on.
- Ad copy is claims-checked (no income promises, fake reviews, guaranteed results).
- Housing, credit, employment and financial-product ads need special_ad_categories on Meta;
  Jackson refuses to guess and asks Steady to set it.
"""

import json
import os
import time
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

import httpx

from ..worker_errors import NonRetryableError

META = "https://graph.facebook.com/v27.0"
TIKTOK = "https://business-api.tiktok.com/open_api/v1.3"
META_OBJECTIVES = ("OUTCOME_TRAFFIC", "OUTCOME_LEADS", "OUTCOME_SALES", "OUTCOME_AWARENESS",
                   "OUTCOME_ENGAGEMENT")
TIKTOK_OBJECTIVES = ("TRAFFIC", "REACH", "VIDEO_VIEWS", "ENGAGEMENT")
SPECIAL = ("HOUSING", "CREDIT", "EMPLOYMENT", "FINANCIAL_PRODUCTS_SERVICES",
           "ISSUES_ELECTIONS_POLITICS")


TIKTOK_LOCATIONS = {"US": "6252001", "CA": "6251999", "GB": "2635167", "AU": "2077456"}
CTAS = ("LEARN_MORE", "SHOP_NOW", "SIGN_UP", "ORDER_NOW")


def _cents(x) -> int:
    try:
        return int((Decimal(str(x)) * 100).quantize(Decimal(1)))
    except (InvalidOperation, TypeError, ValueError):
        return 0


def _int(x) -> int:
    try:
        return int(float(x))
    except (TypeError, ValueError):
        return 0


def meta_configured() -> bool:
    return bool(os.environ.get("META_ACCESS_TOKEN") and os.environ.get("META_AD_ACCOUNT_ID"))


def tiktok_configured() -> bool:
    return bool(os.environ.get("TIKTOK_ACCESS_TOKEN") and os.environ.get("TIKTOK_ADVERTISER_ID"))


def _act() -> str:
    a = os.environ["META_AD_ACCOUNT_ID"].strip()
    return a if a.startswith("act_") else f"act_{a}"


def _new_client() -> httpx.Client:
    return httpx.Client(timeout=30)


def _meta_check(r: httpx.Response, what: str) -> dict:
    if r.status_code == 429 or r.status_code >= 500:
        r.raise_for_status()
    try:
        body = r.json()
    except ValueError:
        body = {}
    if r.status_code >= 400 or "error" in body:
        err = body.get("error") or {}
        raise NonRetryableError(f"Meta refused the {what}: "
                                f"{str(err.get('message') or r.status_code)[:300]}")
    return body


def _tiktok_check(r: httpx.Response, what: str) -> dict:
    if r.status_code == 429 or r.status_code >= 500:
        r.raise_for_status()
    try:
        body = r.json()
    except ValueError:
        body = {}
    if r.status_code >= 400 or body.get("code") not in (0, None):
        raise NonRetryableError(f"TikTok refused the {what}: "
                                f"{str(body.get('message') or r.status_code)[:300]}")
    return body.get("data") or {}


class MetaAds:
    platform = "meta"

    def __init__(self, token: str, client: httpx.Client | None = None, sleep=time.sleep):
        self._t = token
        self._client = client or _new_client()
        self._sleep = sleep

    def account(self) -> dict:
        b = _meta_check(self._client.get(f"{META}/{_act()}", params={
            "fields": "name,account_status,currency,amount_spent", "access_token": self._t}),
            "ad account lookup")
        return {"account": b.get("name"), "status": b.get("account_status"),
                "currency": b.get("currency"), "spent_to_date": b.get("amount_spent")}

    def draft(self, name: str, objective: str, daily_budget_cents: int, countries: list[str],
              special: list[str]) -> dict:
        if objective not in META_OBJECTIVES:
            raise NonRetryableError(f"Meta objective must be one of {META_OBJECTIVES}")
        c = _meta_check(self._client.post(f"{META}/{_act()}/campaigns", data={
            "name": name[:200], "objective": objective, "status": "PAUSED",
            "special_ad_categories": json.dumps(special),
            "is_adset_budget_sharing_enabled": "false", "access_token": self._t}), "campaign")
        s = _meta_check(self._client.post(f"{META}/{_act()}/adsets", data={
            "name": f"{name[:180]} - ad set", "campaign_id": c["id"],
            "daily_budget": str(int(daily_budget_cents)), "billing_event": "IMPRESSIONS",
            "optimization_goal": "LINK_CLICKS" if objective == "OUTCOME_TRAFFIC" else "REACH",
            "bid_strategy": "LOWEST_COST_WITHOUT_CAP", "destination_type": "WEBSITE",
            "targeting": json.dumps({"geo_locations": {"countries": countries},
                                     "targeting_automation": {"advantage_audience": 0}}),
            "status": "PAUSED", "access_token": self._t}), "ad set")
        return {"campaign_id": c["id"], "adset_id": s.get("id"), "status": "PAUSED"}

    def _video(self, url: str, timeout_s: float = 300) -> str:
        v = _meta_check(self._client.post(f"{META}/{_act()}/advideos", data={
            "file_url": url, "access_token": self._t}), "video upload")
        deadline = time.monotonic() + timeout_s
        while True:
            st = _meta_check(self._client.get(f"{META}/{v['id']}", params={
                "fields": "status", "access_token": self._t}), "video status")
            vs = (st.get("status") or {}).get("video_status")
            if vs == "ready":
                return v["id"]
            if vs == "error":
                raise NonRetryableError("Meta couldn't process the video")
            if time.monotonic() >= deadline:
                raise httpx.TimeoutException("Meta is still processing the video")
            self._sleep(5)

    def add_ad(self, name: str, adset_id: str, c: dict) -> dict:
        """One creative + one ad, created PAUSED (the campaign stays paused too)."""
        page = os.environ.get("META_PAGE_ID", "")
        if not page:
            raise NonRetryableError("set META_PAGE_ID (the Facebook Page the ads run as)")
        cta = {"type": c["cta"], "value": {"link": c["link"]}}
        if c.get("video_url"):
            spec = {"page_id": page, "video_data": {
                "video_id": self._video(c["video_url"]), "image_url": c["thumbnail_url"],
                "title": c["headline"], "message": c["text"], "call_to_action": cta}}
        else:
            spec = {"page_id": page, "link_data": {
                "picture": c["image_url"], "link": c["link"], "message": c["text"],
                "name": c["headline"], "description": c.get("description") or "",
                "call_to_action": cta}}
        cr = _meta_check(self._client.post(f"{META}/{_act()}/adcreatives", data={
            "name": f"{name[:180]} - creative", "object_story_spec": json.dumps(spec),
            "access_token": self._t}), "creative")
        ad = _meta_check(self._client.post(f"{META}/{_act()}/ads", data={
            "name": f"{name[:180]} - ad", "adset_id": adset_id,
            "creative": json.dumps({"creative_id": cr["id"]}), "status": "PAUSED",
            "access_token": self._t}), "ad")
        return {"creative_id": cr["id"], "ad_id": ad["id"]}

    def insights(self, campaign_id: str) -> dict:
        b = _meta_check(self._client.get(f"{META}/{campaign_id}/insights", params={
            "fields": "spend,impressions,clicks,actions", "date_preset": "maximum",
            "access_token": self._t}), "insights")
        row = (b.get("data") or [{}])[0] if b.get("data") else {}
        acts = {a.get("action_type"): a.get("value") for a in row.get("actions") or []}
        return {"spend_cents": _cents(row.get("spend")), "impressions": _int(row.get(
            "impressions")), "clicks": _int(row.get("clicks")),
            "results": _int(acts.get("link_click") or acts.get("landing_page_view")),
            "result_name": "link clicks"}

    def set_end_time(self, adset_id: str, ends_at) -> None:
        """Make Meta stop the ad set by itself when the Launch window ends."""
        _meta_check(self._client.post(f"{META}/{adset_id}", data={
            "end_time": ends_at.isoformat(), "access_token": self._t}), "end time")

    def set_status(self, ids: list[str], on: bool) -> None:
        for oid in ids:
            _meta_check(self._client.post(f"{META}/{oid}", data={
                "status": "ACTIVE" if on else "PAUSED", "access_token": self._t}),
                "status change")


class TikTokAds:
    platform = "tiktok"

    def __init__(self, token: str, advertiser: str, client: httpx.Client | None = None):
        self._h = {"Access-Token": token, "Content-Type": "application/json"}
        self.adv = advertiser
        self._client = client or _new_client()

    def account(self) -> dict:
        d = _tiktok_check(self._client.get(f"{TIKTOK}/advertiser/info/", headers=self._h,
                                           params={"advertiser_ids": json.dumps([self.adv])}),
                          "advertiser lookup")
        a = (d.get("list") or [{}])[0]
        return {"account": a.get("name"), "status": a.get("status"),
                "currency": a.get("currency")}

    def draft(self, name: str, objective: str, daily_budget_cents: int, countries: list[str],
              special: list[str]) -> dict:
        if objective not in TIKTOK_OBJECTIVES:
            raise NonRetryableError(f"TikTok objective must be one of {TIKTOK_OBJECTIVES}")
        d = _tiktok_check(self._client.post(f"{TIKTOK}/campaign/create/", headers=self._h,
                                            json={"advertiser_id": self.adv,
                                                  "campaign_name": name[:500],
                                                  "objective_type": objective,
                                                  "budget_mode": "BUDGET_MODE_DAY",
                                                  "budget": round(daily_budget_cents / 100, 2),
                                                  "operation_status": "DISABLE"}), "campaign")
        cid = str(d.get("campaign_id"))
        locs = []
        for c in countries:
            if c not in TIKTOK_LOCATIONS:
                raise NonRetryableError(f"TikTok targeting for {c} isn't mapped yet; use one of "
                                        f"{sorted(TIKTOK_LOCATIONS)}")
            locs.append(TIKTOK_LOCATIONS[c])
        g = _tiktok_check(self._client.post(f"{TIKTOK}/adgroup/create/", headers=self._h, json={
            "advertiser_id": self.adv, "campaign_id": cid, "adgroup_name": f"{name[:480]} - group",
            "promotion_type": "WEBSITE", "placement_type": "PLACEMENT_TYPE_AUTOMATIC",
            "location_ids": locs, "budget_mode": "BUDGET_MODE_DAY",
            "budget": round(daily_budget_cents / 100, 2), "schedule_type": "SCHEDULE_FROM_NOW",
            "schedule_start_time": datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S"),
            "optimization_goal": "CLICK", "billing_event": "CPC", "bid_type": "BID_TYPE_NO_BID",
            "pacing": "PACING_MODE_SMOOTH", "operation_status": "DISABLE"}), "ad group")
        return {"campaign_id": cid, "adset_id": str(g.get("adgroup_id")), "status": "DISABLE"}

    def _upload(self, kind: str, url: str) -> str:
        d = _tiktok_check(self._client.post(
            f"{TIKTOK}/file/{kind}/ad/upload/", headers={"Access-Token": self._h["Access-Token"]},
            data={"advertiser_id": self.adv, "upload_type": "UPLOAD_BY_URL",
                  f"{kind}_url": url}), f"{kind} upload")
        d = d[0] if isinstance(d, list) and d else d
        out = (d or {}).get(f"{kind}_id")
        if not out:
            raise NonRetryableError(f"TikTok returned no {kind} id")
        return str(out)

    def add_ad(self, name: str, adset_id: str, c: dict) -> dict:
        identity = os.environ.get("TIKTOK_IDENTITY_ID", "")
        if not identity:
            raise NonRetryableError("set TIKTOK_IDENTITY_ID (the identity your TikTok ads post "
                                    "as) in Railway")
        if not c.get("video_url"):
            raise NonRetryableError("TikTok ads need a video (video_url) and a cover image "
                                    "(thumbnail_url)")
        vid, cover = self._upload("video", c["video_url"]), self._upload("image",
                                                                         c["thumbnail_url"])
        d = _tiktok_check(self._client.post(f"{TIKTOK}/ad/create/", headers=self._h, json={
            "advertiser_id": self.adv, "adgroup_id": adset_id, "creatives": [{
                "ad_name": f"{name[:480]} - ad", "ad_format": "SINGLE_VIDEO",
                "identity_type": os.environ.get("TIKTOK_IDENTITY_TYPE") or "CUSTOMIZED_USER",
                "identity_id": identity, "video_id": vid, "image_ids": [cover],
                "ad_text": c["text"][:100], "call_to_action": c["cta"],
                "landing_page_url": c["link"]}]}), "ad")
        ids = d.get("ad_ids") or []
        return {"creative_id": vid, "ad_id": str(ids[0]) if ids else None}

    def insights(self, campaign_id: str) -> dict:
        d = _tiktok_check(self._client.get(f"{TIKTOK}/report/integrated/get/", headers=self._h,
                                           params={
            "advertiser_id": self.adv, "report_type": "BASIC", "service_type": "AUCTION",
            "data_level": "AUCTION_CAMPAIGN", "dimensions": json.dumps(["campaign_id"]),
            "metrics": json.dumps(["spend", "impressions", "clicks", "conversion"]),
            "query_lifetime": "true", "page_size": 1000}), "report")
        for row in d.get("list") or []:
            if str((row.get("dimensions") or {}).get("campaign_id")) == str(campaign_id):
                m = row.get("metrics") or {}
                return {"spend_cents": _cents(m.get("spend")),
                        "impressions": _int(m.get("impressions")),
                        "clicks": _int(m.get("clicks")), "results": _int(m.get("clicks")),
                        "conversions": _int(m.get("conversion")), "result_name": "clicks"}
        return {"spend_cents": 0, "impressions": 0, "clicks": 0, "results": 0,
                "result_name": "clicks"}  # no delivery reported yet

    def set_status(self, ids: list[str], on: bool) -> None:
        """ids = [campaign, ad group, (ad)]: the campaign and ad group gate delivery."""
        op = "ENABLE" if on else "DISABLE"
        _tiktok_check(self._client.post(f"{TIKTOK}/campaign/status/update/", headers=self._h,
                                        json={"advertiser_id": self.adv,
                                              "campaign_ids": ids[:1], "operation_status": op}),
                      "status change")
        if len(ids) > 1:
            _tiktok_check(self._client.post(f"{TIKTOK}/adgroup/status/update/",
                                            headers=self._h, json={
                "advertiser_id": self.adv, "adgroup_ids": ids[1:2], "operation_status": op}),
                "status change")


class MockAds:
    """MOCK: no campaign exists anywhere; ids say mock."""

    def __init__(self, platform: str):
        self.platform = platform

    def draft(self, name, objective, daily_budget_cents, countries, special) -> dict:
        return {"campaign_id": f"mock-{self.platform}", "adset_id": None, "status": "MOCK"}

    def add_ad(self, name, adset_id, c) -> dict:
        raise NonRetryableError(f"{self.platform} ads aren't connected: no creative was made")

    def insights(self, campaign_id) -> dict:
        raise NonRetryableError(f"{self.platform} ads aren't connected")

    def set_status(self, ids, on) -> None:
        raise NonRetryableError(f"{self.platform} ads aren't connected: nothing to switch on")


def backend(platform: str):
    if platform == "meta":
        return MetaAds(os.environ["META_ACCESS_TOKEN"]) if meta_configured() else MockAds("meta")
    if platform == "tiktok":
        return (TikTokAds(os.environ["TIKTOK_ACCESS_TOKEN"], os.environ["TIKTOK_ADVERTISER_ID"])
                if tiktok_configured() else MockAds("tiktok"))
    raise NonRetryableError("platform must be meta or tiktok")
