"""ADR-058: follower counts exactly as YouTube, Instagram and TikTok report them, one
reading per account per day; growth only from two real readings."""

import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from conftest import auth, run_state
from sqlalchemy import select

from jackson_api.connectors import analytics as A
from jackson_api.followers import daily_sweep, growth, record
from jackson_api.lines import kpis
from jackson_api.models import FollowerSnapshot
from jackson_api.worker import Worker

YT_KEY, META, TT = "yt-" + "k" * 30, "EAA" + "m" * 30, "act." + "t" * 30


class Platforms:
    def __init__(self, yt=1200, ig=5400, tt=880):
        self.n = {"yt": yt, "ig": ig, "tt": tt}
        self.calls = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.calls.append(req)
        if req.url.host == "www.googleapis.com":
            assert req.url.params["key"] == YT_KEY
            return httpx.Response(200, json={"items": [
                {"id": "UC1", "snippet": {"customUrl": "@steady"},
                 "statistics": {"subscriberCount": str(self.n["yt"])}},
                {"id": "UC2", "snippet": {"title": "hidden"},
                 "statistics": {"hiddenSubscriberCount": True}}]})
        if req.url.host == "graph.facebook.com":
            assert req.url.params["access_token"] == META
            return httpx.Response(200, json={"id": "1789", "username": "nova.ai",
                                             "followers_count": self.n["ig"]})
        if req.url.host == "open.tiktokapis.com":
            assert req.headers["authorization"] == f"Bearer {TT}"
            return httpx.Response(200, json={"data": {"user": {
                "open_id": "oid1", "username": "nova", "follower_count": self.n["tt"]}},
                "error": {"code": "ok", "message": ""}})
        return httpx.Response(404, json={"error": "unexpected"})


@pytest.fixture()
def platforms(monkeypatch):
    for k, v in {"PLATFORM_ANALYTICS": "youtube,instagram,tiktok", "YOUTUBE_API_KEY": YT_KEY,
                 "YOUTUBE_CHANNEL_IDS": "UC1,UC2", "META_ACCESS_TOKEN": META,
                 "INSTAGRAM_USER_IDS": "1789", "TIKTOK_DISPLAY_TOKEN": TT}.items():
        monkeypatch.setenv(k, v)
    fake = Platforms()
    monkeypatch.setattr(A, "_new_client",
                        lambda: httpx.Client(transport=httpx.MockTransport(fake)))
    return fake


def test_readings_skip_hidden_counts_and_name_missing_variables(platforms, monkeypatch):
    readings, errors = A.read_all()
    assert {(r["platform"], r["followers"]) for r in readings} == {
        ("youtube", 1200), ("instagram", 5400), ("tiktok", 880)}  # UC2 hides its count
    assert errors == {}
    monkeypatch.delenv("INSTAGRAM_USER_IDS")
    readings, errors = A.read_all()
    assert errors == {"instagram": "set INSTAGRAM_USER_IDS"} and len(readings) == 2
    assert not A.configured()


@pytest.fixture()
def alice(make_token, world):
    return auth(make_token, world.alice)


def test_snapshot_then_growth_from_two_real_days(client, world, alice, maker, owner_db,
                                                  drain, platforms, monkeypatch):
    k = {x["name"]: x for x in kpis(owner_db, world.tenant_a, "influencers")}
    assert k["Followers now"]["value"] is None
    assert k["Followers now"]["note"] == "first reading not taken yet"
    r = client.post(f"/tenants/{world.tenant_a}/analytics/followers/snapshot", headers=alice,
                    json={"idempotency_key": uuid.uuid4().hex})
    assert r.status_code == 202, r.text
    Worker(maker, retry_base_seconds=0).run_once()
    run, tasks = run_state(owner_db, r.json()["run_id"])
    assert run.status == "succeeded", tasks[0].error
    assert tasks[0].output["followers_now"] == 7480 and tasks[0].output["gained_30d"] is None
    k = {x["name"]: x for x in kpis(owner_db, world.tenant_a, "influencers")}
    assert k["Followers now"]["value"] == 7480
    assert k["Followers gained (30 days)"]["value"] is None  # one day: no growth claimed
    # a reading 10 days ago (as if the sweep had run then), then today's again
    owner_db.execute(FollowerSnapshot.__table__.update().where(
        FollowerSnapshot.tenant_id == world.tenant_a).values(
        taken_on=datetime.now(UTC).date() - timedelta(days=10)))
    owner_db.commit()
    platforms.n.update(yt=1300, ig=5350, tt=1000)
    assert world.tenant_a in daily_sweep(owner_db)  # opted in by the first snapshot
    owner_db.commit()
    assert world.tenant_b not in daily_sweep(owner_db)  # never tracked: not read
    g = growth(owner_db, world.tenant_a)
    assert g["followers_now"] == 7650 and g["gained"] == 100 + (-50) + 120
    r2 = client.get(f"/tenants/{world.tenant_a}/analytics/followers", headers=alice)
    assert r2.json()["gained"] == 170
    k = {x["name"]: x for x in kpis(owner_db, world.tenant_a, "influencers")}
    assert k["Followers gained (30 days)"]["value"] == 170
    kb = {x["name"]: x for x in kpis(owner_db, world.tenant_b, "influencers")}
    assert kb["Followers now"]["value"] is None  # other tenant: nothing


def test_same_day_reading_replaces_and_test_records_nothing(client, world, alice, owner_db,
                                                            platforms):
    record(owner_db, world.tenant_a, [{"platform": "tiktok", "account_id": "x",
                                       "handle": "a", "followers": 10}])
    record(owner_db, world.tenant_a, [{"platform": "tiktok", "account_id": "x",
                                       "handle": "a", "followers": 12}])
    owner_db.commit()
    rows = owner_db.scalars(select(FollowerSnapshot).where(
        FollowerSnapshot.tenant_id == world.tenant_a)).all()
    assert [r.followers for r in rows] == [12]
    t = client.post(f"/tenants/{world.tenant_a}/admin/connectors/analytics/test", headers=alice)
    assert t.json()["ok"], t.text
    assert len(t.json()["result"]["accounts"]) == 3 and META not in t.text and TT not in t.text
    assert len(owner_db.scalars(select(FollowerSnapshot).where(
        FollowerSnapshot.tenant_id == world.tenant_a)).all()) == 1


def test_not_configured_is_refused_with_the_variable_names(client, world, alice, monkeypatch):
    monkeypatch.delenv("PLATFORM_ANALYTICS", raising=False)
    r = client.post(f"/tenants/{world.tenant_a}/analytics/followers/snapshot", headers=alice,
                    json={"idempotency_key": uuid.uuid4().hex})
    assert r.status_code == 409 and "PLATFORM_ANALYTICS" in r.text
