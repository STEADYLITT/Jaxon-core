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

import httpx

from ..worker_errors import NonRetryableError

META = "https://graph.facebook.com/v27.0"
TIKTOK = "https://business-api.tiktok.com/open_api/v1.3"
META_OBJECTIVES = ("OUTCOME_TRAFFIC", "OUTCOME_LEADS", "OUTCOME_SALES", "OUTCOME_AWARENESS",
                   "OUTCOME_ENGAGEMENT")
TIKTOK_OBJECTIVES = ("TRAFFIC", "REACH", "VIDEO_VIEWS", "ENGAGEMENT")
SPECIAL = ("HOUSING", "CREDIT", "EMPLOYMENT", "FINANCIAL_PRODUCTS_SERVICES",
           "ISSUES_ELECTIONS_POLITICS")


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

    def __init__(self, token: str, client: httpx.Client | None = None):
        self._t = token
        self._client = client or _new_client()

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
            "bid_strategy": "LOWEST_COST_WITHOUT_CAP",
            "targeting": json.dumps({"geo_locations": {"countries": countries}}),
            "status": "PAUSED", "access_token": self._t}), "ad set")
        return {"campaign_id": c["id"], "adset_id": s.get("id"), "status": "PAUSED"}

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
        return {"campaign_id": str(d.get("campaign_id")), "adset_id": None,
                "status": "DISABLE"}

    def set_status(self, ids: list[str], on: bool) -> None:
        _tiktok_check(self._client.post(f"{TIKTOK}/campaign/status/update/", headers=self._h,
                                        json={"advertiser_id": self.adv, "campaign_ids": ids,
                                              "operation_status": "ENABLE" if on else
                                              "DISABLE"}), "status change")


class MockAds:
    """MOCK: no campaign exists anywhere; ids say mock."""

    def __init__(self, platform: str):
        self.platform = platform

    def draft(self, name, objective, daily_budget_cents, countries, special) -> dict:
        return {"campaign_id": f"mock-{self.platform}", "adset_id": None, "status": "MOCK"}

    def set_status(self, ids, on) -> None:
        raise NonRetryableError(f"{self.platform} ads aren't connected: nothing to switch on")


def backend(platform: str):
    if platform == "meta":
        return MetaAds(os.environ["META_ACCESS_TOKEN"]) if meta_configured() else MockAds("meta")
    if platform == "tiktok":
        return (TikTokAds(os.environ["TIKTOK_ACCESS_TOKEN"], os.environ["TIKTOK_ADVERTISER_ID"])
                if tiktok_configured() else MockAds("tiktok"))
    raise NonRetryableError("platform must be meta or tiktok")
