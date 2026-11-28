"""ADR-048: Meta + TikTok ads. Drafts are created paused; turning one on is only possible
inside an active Launch that lists the platform; a stopped Launch pauses its ads.
Meta's and TikTok's networks are faked with their documented shapes."""

import json
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs

import httpx
import pytest
from conftest import auth, run_state
from sqlalchemy import select, text, update

from jackson_api import connections as C
from jackson_api.connectors import ads as A
from jackson_api.models import AdCampaign, Launch
from jackson_api.worker import Worker

MT, TT = "EAA" + "m" * 40, "tt" + "k" * 40


class FakeAds:
    def __init__(self):
        self.calls = []

    def meta(self):
        return [(c.url.path, parse_qs(c.content.decode())) for c in self.calls
                if c.url.host == "graph.facebook.com" and c.method == "POST"]

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.calls.append(req)
        p = req.url.path
        if req.url.host == "graph.facebook.com":
            if req.method == "GET" and p.endswith("/insights"):
                return httpx.Response(200, json={"data": [{
                    "spend": "12.34", "impressions": "1500", "clicks": "40",
                    "actions": [{"action_type": "link_click", "value": "37"}]}]})
            if req.method == "GET" and p == "/v27.0/v555":
                return httpx.Response(200, json={"status": {"video_status": "ready"}})
            if req.method == "GET":
                return httpx.Response(200, json={"name": "Steady Ads", "account_status": 1,
                                                 "currency": "USD", "amount_spent": "0"})
            form = parse_qs(req.content.decode())
            assert form["access_token"] == [MT]
            if p.endswith("/campaigns"):
                assert form["status"] == ["PAUSED"]
                return httpx.Response(200, json={"id": "c111"})
            if p.endswith("/adsets"):
                assert form["status"] == ["PAUSED"] and form["destination_type"] == ["WEBSITE"]
                t = json.loads(form["targeting"][0])
                assert t["targeting_automation"] == {"advantage_audience": 0}
                return httpx.Response(200, json={"id": "s222"})
            if p.endswith("/adcreatives"):
                spec = json.loads(form["object_story_spec"][0])
                assert spec["page_id"] == "PAGE1"
                return httpx.Response(200, json={"id": "cr333"})
            if p.endswith("/ads"):
                assert form["status"] == ["PAUSED"]
                assert json.loads(form["creative"][0]) == {"creative_id": "cr333"}
                return httpx.Response(200, json={"id": "ad444"})
            if p.endswith("/advideos"):
                return httpx.Response(200, json={"id": "v555"})
            return httpx.Response(200, json={"success": True})
        if req.url.host == "business-api.tiktok.com":
            assert req.headers["access-token"] == TT
            if p.endswith("/advertiser/info/"):
                return httpx.Response(200, json={"code": 0, "data": {"list": [
                    {"name": "Steady TT", "status": "STATUS_ENABLE", "currency": "USD"}]}})
            if p.endswith("/campaign/create/"):
                assert json.loads(req.content)["operation_status"] == "DISABLE"
                return httpx.Response(200, json={"code": 0, "data": {"campaign_id": 1800}})
            if p.endswith("/adgroup/create/"):
                b = json.loads(req.content)
                assert b["operation_status"] == "DISABLE" and b["location_ids"] == ["6252001"]
                return httpx.Response(200, json={"code": 0, "data": {"adgroup_id": 1900}})
            if p.endswith("/file/video/ad/upload/"):
                return httpx.Response(200, json={"code": 0, "data": [{"video_id": "vtt"}]})
            if p.endswith("/file/image/ad/upload/"):
                return httpx.Response(200, json={"code": 0, "data": {"image_id": "itt"}})
            if p.endswith("/ad/create/"):
                cr = json.loads(req.content)["creatives"][0]
                assert cr["video_id"] == "vtt" and cr["image_ids"] == ["itt"]
                return httpx.Response(200, json={"code": 0, "data": {"ad_ids": ["2000"]}})
            if p.endswith("/report/integrated/get/"):
                return httpx.Response(200, json={"code": 0, "data": {"list": [
                    {"dimensions": {"campaign_id": "1800"},
                     "metrics": {"spend": "5.50", "impressions": "900", "clicks": "21",
                                 "conversion": "2"}}]}})
            return httpx.Response(200, json={"code": 0, "data": {}})
        return httpx.Response(404, json={"error": {"message": "nope"}})


@pytest.fixture()
def live(monkeypatch):
    fake = FakeAds()
    for k, v in {"META_ACCESS_TOKEN": MT, "META_AD_ACCOUNT_ID": "12345", "META_PAGE_ID": "PAGE1",
                 "TIKTOK_IDENTITY_ID": "ID1",
                 "TIKTOK_ACCESS_TOKEN": TT, "TIKTOK_ADVERTISER_ID": "777"}.items():
        monkeypatch.setenv(k, v)
    mk = lambda: httpx.Client(transport=httpx.MockTransport(fake))  # noqa: E731
    monkeypatch.setattr(A, "_new_client", mk)
    monkeypatch.setattr(C, "_new_client", mk)
    return fake


@pytest.fixture()
def alice(make_token, world):
    return auth(make_token, world.alice)


def launch(owner_db, world, ads=True, platforms=("meta",), status="active", days=7,
           daily=5_000, total=20_000):
    uid = owner_db.scalar(text("SELECT id FROM users WHERE auth_subject = :s"),
                          {"s": world.alice})
    now = datetime.now(UTC)
    la = Launch(tenant_id=world.tenant_a, business_id=world.biz_a, title="Course ads",
                status=status, total_budget_cents=total, daily_cap_cents=daily,
                channels={"ads": ads, "ad_platforms": list(platforms)}, kill_rules={},
                starts_at=now - timedelta(hours=1), ends_at=now + timedelta(days=days),
                approved_by=uid, approved_at=now)
    owner_db.add(la)
    owner_db.commit()
    return la


def draft(client, world, alice, maker, owner_db, **kw):
    body = {"platform": "meta", "name": "AI for Traders - traffic", "daily_budget_cents": 2000,
            "copy": "Learn a research routine with AI. Education only.",
            "business_id": str(world.biz_a), "idempotency_key": uuid.uuid4().hex, **kw}
    r = client.post(f"/tenants/{world.tenant_a}/ads/drafts", headers=alice, json=body)
    assert r.status_code == 202, r.text
    Worker(maker, retry_base_seconds=0).run_once()
    run, tasks = run_state(owner_db, r.json()["run_id"])
    return run, tasks[0]


def activate(client, world, alice, maker, owner_db, aid, launch_id):
    r = client.post(f"/tenants/{world.tenant_a}/ads/{aid}/activate", headers=alice,
                    json={"launch_id": str(launch_id), "idempotency_key": uuid.uuid4().hex})
    if r.status_code != 202:
        return r, None
    Worker(maker, retry_base_seconds=0).run_once()
    return r, run_state(owner_db, r.json()["run_id"])


def add_creative(client, world, alice, maker, owner_db, aid, **kw):
    body = {"link": "https://steadytrades.example/course", "headline": "AI for Traders",
            "text": "A research routine with AI. Education only.",
            "image_url": "https://cdn.example/ad.jpg", "idempotency_key": uuid.uuid4().hex, **kw}
    r = client.post(f"/tenants/{world.tenant_a}/ads/{aid}/creative", headers=alice, json=body)
    assert r.status_code == 202, r.text
    Worker(maker, retry_base_seconds=0).run_once()
    run, tasks = run_state(owner_db, r.json()["run_id"])
    return run, tasks[0]


def test_connection_tests(client, world, alice, live):
    for cid in ("meta_ads", "tiktok_ads"):
        r = client.post(f"/tenants/{world.tenant_a}/admin/connectors/{cid}/test", headers=alice)
        assert r.json()["ok"], r.text
        assert MT not in r.text and TT not in r.text


def test_drafts_are_paused_and_spend_nothing(client, world, alice, maker, owner_db, drain,
                                             live):
    run, t = draft(client, world, alice, maker, owner_db)
    assert run.status == "succeeded", t.error
    assert t.output["status"] == "PAUSED" and t.output["adset_id"] == "s222"
    (cp, cform), (sp, sform) = live.meta()
    assert cp == "/v27.0/act_12345/campaigns" and cform["special_ad_categories"] == ["[]"]
    assert sform["daily_budget"] == ["2000"] and sform["campaign_id"] == ["c111"]
    run, t = draft(client, world, alice, maker, owner_db, platform="tiktok",
                   objective="TRAFFIC")
    assert run.status == "succeeded" and t.output["status"] == "DISABLE"
    rows = owner_db.scalars(select(AdCampaign).where(AdCampaign.tenant_id == world.tenant_a))
    assert {r.status for r in rows} == {"draft"}


def test_income_claims_are_refused(client, world, alice, maker, owner_db, drain, live):
    run, t = draft(client, world, alice, maker, owner_db, copy="Make $500 a day trading")
    assert t.status == "failed" and "claims check" in t.error and live.meta() == []


def test_spend_only_inside_an_active_launch_for_that_platform(client, world, alice, maker,
                                                              owner_db, drain, live):
    _, t = draft(client, world, alice, maker, owner_db)
    aid = t.output["ad_campaign_id"]
    no_ad = launch(owner_db, world)
    r, (run, tasks) = activate(client, world, alice, maker, owner_db, aid, no_ad.id)
    assert tasks[0].status == "failed" and "creative" in tasks[0].error  # nothing to serve
    add_creative(client, world, alice, maker, owner_db, aid)
    proposed = launch(owner_db, world, status="proposed")
    r, _ = activate(client, world, alice, maker, owner_db, aid, proposed.id)
    assert r.status_code == 409  # not an approved, active Launch
    tiktok_only = launch(owner_db, world, platforms=("tiktok",))
    r, (run, tasks) = activate(client, world, alice, maker, owner_db, aid, tiktok_only.id)
    assert tasks[0].status == "failed" and "doesn't allow meta" in tasks[0].error
    small = launch(owner_db, world, daily=1_000, total=1_000)
    r, (run, tasks) = activate(client, world, alice, maker, owner_db, aid, small.id)
    assert run.status != "succeeded"  # $20/day doesn't fit a $10 daily cap
    before = len([c for c in live.meta() if c[1].get("status") == ["ACTIVE"]])
    assert before == 0
    ok = launch(owner_db, world)
    r, (run, tasks) = activate(client, world, alice, maker, owner_db, aid, ok.id)
    assert run.status == "succeeded", tasks[0].error
    posts = live.meta()
    assert any(f.get("end_time") for _, f in posts)  # stops by itself at the launch end
    assert [p for p, f in posts if f.get("status") == ["ACTIVE"]] == [
        "/v27.0/c111", "/v27.0/s222", "/v27.0/ad444"]
    owner_db.expire_all()
    assert owner_db.get(AdCampaign, uuid.UUID(aid)).status == "active"

    # The launch is killed: the next sweep pauses the ad.
    owner_db.execute(update(Launch).where(Launch.id == ok.id).values(status="killed"))
    owner_db.commit()
    Worker(maker, retry_base_seconds=0).sweep_launches()
    owner_db.expire_all()
    assert owner_db.get(AdCampaign, uuid.UUID(aid)).status == "paused"
    assert [p for p, f in live.meta() if f.get("status") == ["PAUSED"]][-3:] == [
        "/v27.0/c111", "/v27.0/s222", "/v27.0/ad444"]


def test_mock_drafts_can_never_go_live(client, world, alice, maker, owner_db, drain,
                                       monkeypatch):
    monkeypatch.delenv("META_ACCESS_TOKEN", raising=False)
    _, t = draft(client, world, alice, maker, owner_db)
    assert t.output["status"] == "MOCK" and "[MOCK]" in t.output["summary"]
    la = launch(owner_db, world)
    r, (run, tasks) = activate(client, world, alice, maker, owner_db,
                               t.output["ad_campaign_id"], la.id)
    assert tasks[0].status == "failed" and "MOCK" in tasks[0].error


def test_meta_creative_then_real_insights_and_budget_stop(client, world, alice, maker,
                                                          owner_db, drain, live):
    _, t = draft(client, world, alice, maker, owner_db)
    aid = t.output["ad_campaign_id"]
    run, ct = add_creative(client, world, alice, maker, owner_db, aid,
                           text="Get rich with this one trick")
    assert ct.status == "failed" and "claims check" in ct.error
    run, ct = add_creative(client, world, alice, maker, owner_db, aid)
    assert run.status == "succeeded", ct.error
    assert ct.output["ad_id"] == "ad444" and ct.output["status"] == "paused"
    la = launch(owner_db, world, total=1_200, daily=1_200, platforms=("meta",))
    owner_db.execute(update(AdCampaign).where(AdCampaign.id == uuid.UUID(aid))
                     .values(daily_budget_cents=1_000))
    owner_db.commit()
    r, (run, tasks) = activate(client, world, alice, maker, owner_db, aid, la.id)
    assert run.status == "succeeded", tasks[0].error
    changed = Worker(maker, retry_base_seconds=0).sweep_launches()  # $12.34 >= $12 budget
    assert changed == [(str(la.id), "completed")]
    owner_db.expire_all()
    c = owner_db.get(AdCampaign, uuid.UUID(aid))
    assert c.spend_cents_reported == 1234 and c.results == 37 and c.clicks == 40
    assert c.status == "paused"  # real spend used up the launch budget: the ad stops
    from jackson_api.launches import metrics
    assert metrics(owner_db, owner_db.get(Launch, la.id))["ad_spend_reported_cents"] == 1234
    listed = client.get(f"/tenants/{world.tenant_a}/ads", headers=alice).json()
    assert listed["campaigns"][0]["reported"]["spend_cents"] == 1234


def test_tiktok_video_ad_and_report(client, world, alice, maker, owner_db, drain, live):
    _, t = draft(client, world, alice, maker, owner_db, platform="tiktok", objective="TRAFFIC")
    assert t.output["adset_id"] == "1900"
    aid = t.output["ad_campaign_id"]
    run, ct = add_creative(client, world, alice, maker, owner_db, aid, image_url=None)
    assert ct.status == "failed" and "video" in ct.error
    run, ct = add_creative(client, world, alice, maker, owner_db, aid, image_url=None,
                           video_url="https://v3.fal.media/clip.mp4",
                           thumbnail_url="https://cdn.example/cover.jpg")
    assert run.status == "succeeded", ct.error and ct.output["ad_id"] == "2000"
    la = launch(owner_db, world, platforms=("tiktok",))
    r, (run, tasks) = activate(client, world, alice, maker, owner_db, aid, la.id)
    assert run.status == "succeeded", tasks[0].error
    paths = [c.url.path for c in live.calls]
    assert any(p.endswith("/adgroup/status/update/") for p in paths)
    r = client.post(f"/tenants/{world.tenant_a}/ads/{aid}/insights", headers=alice,
                    json={"idempotency_key": uuid.uuid4().hex})
    Worker(maker, retry_base_seconds=0).run_once()
    run, tasks = run_state(owner_db, r.json()["run_id"])
    assert run.status == "succeeded", tasks[0].error
    assert tasks[0].output["spend_cents"] == 550 and tasks[0].output["results"] == 21
