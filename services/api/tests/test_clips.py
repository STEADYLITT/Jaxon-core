"""ADR-052: clip scouting on YouTube and Twitch. Every pick has its source, its numbers
and whether clipping it is allowed. The APIs are faked with their documented shapes."""

import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from conftest import auth, run_state

from jackson_api import connections as C
from jackson_api.connectors import clips as K
from jackson_api.lines import kpis
from jackson_api.worker import Worker

YT_KEY, TW_ID, TW_SECRET = "AIza" + "y" * 35, "tw" + "i" * 28, "tws" + "s" * 27
NOW = datetime.now(UTC)


class FakeClips:
    def __init__(self):
        self.calls = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.calls.append(req)
        h, p = req.url.host, req.url.path
        if h == "www.googleapis.com":
            assert req.url.params["key"] == YT_KEY
            if p == "/youtube/v3/search":
                assert req.url.params["order"] == "viewCount"
                return httpx.Response(200, json={"items": [{"id": {"videoId": "v1"}},
                                                           {"id": {"videoId": "v2"}}]})
            if p == "/youtube/v3/videos":
                if req.url.params["part"] == "id":
                    return httpx.Response(200, json={"items": [{"id": "dQw4w9WgXcQ"}]})
                pub = (NOW - timedelta(hours=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
                return httpx.Response(200, json={"items": [
                    {"id": "v1", "snippet": {"title": "GTA 6 trailer reaction",
                                             "channelTitle": "BigStreamer", "publishedAt": pub},
                     "statistics": {"viewCount": "50000", "likeCount": "900"},
                     "contentDetails": {"duration": "PT1H2M3S"},
                     "status": {"license": "youtube"}},
                    {"id": "v2", "snippet": {"title": "Free to use gameplay",
                                             "channelTitle": "OpenClips", "publishedAt": pub},
                     "statistics": {"viewCount": "1000"},
                     "contentDetails": {"duration": "PT10M"},
                     "status": {"license": "creativeCommon"}}]})
        if h == "id.twitch.tv":
            return httpx.Response(200, json={"access_token": "app-tok", "expires_in": 5000})
        if h == "api.twitch.tv":
            assert req.headers["client-id"] == TW_ID
            assert req.headers["authorization"] == "Bearer app-tok"
            if p == "/helix/games/top":
                return httpx.Response(200, json={"data": [{"name": "Grand Theft Auto V"}]})
            if p == "/helix/users":
                return httpx.Response(200, json={"data": [
                    {"id": "123", "login": "steadyfriend", "display_name": "SteadyFriend"}]})
            if p == "/helix/clips":
                assert req.url.params["broadcaster_id"] == "123"
                return httpx.Response(200, json={"data": [
                    {"id": "c1", "url": "https://clips.twitch.tv/c1", "title": "insane heist",
                     "view_count": 7000, "created_at": NOW.isoformat(), "duration": 28.4,
                     "creator_name": "fan1", "vod_offset": 3600}]})
        return httpx.Response(404, json={"error": {"message": "nope"}})


@pytest.fixture()
def live(monkeypatch):
    fake = FakeClips()
    monkeypatch.setenv("YOUTUBE_API_KEY", YT_KEY)
    monkeypatch.setenv("TWITCH_CLIENT_ID", TW_ID)
    monkeypatch.setenv("TWITCH_CLIENT_SECRET", TW_SECRET)
    monkeypatch.setenv("CLIP_ALLOWED_CREATORS", "steadyfriend")
    mk = lambda: httpx.Client(transport=httpx.MockTransport(fake))  # noqa: E731
    monkeypatch.setattr(K, "_new_client", mk)
    monkeypatch.setattr(C, "_new_client", mk)
    return fake


@pytest.fixture()
def alice(make_token, world):
    return auth(make_token, world.alice)


def test_durations_and_rights(monkeypatch):
    assert K.seconds("PT1H2M3S") == 3723 and K.seconds("PT45S") == 45 and K.seconds("") == 0
    monkeypatch.setenv("CLIP_ALLOWED_CREATORS", "Alice, bob")
    assert K.rights("BOB") == "allowed" and K.rights("eve") == "ask the creator first"
    assert K.rights("eve", "creativeCommon").startswith("allowed (Creative Commons")


def test_connection_tests(client, world, alice, live):
    for cid in ("youtube", "twitch"):
        r = client.post(f"/tenants/{world.tenant_a}/admin/connectors/{cid}/test", headers=alice)
        assert r.json()["ok"], r.text
        assert YT_KEY not in r.text and TW_SECRET not in r.text


def test_scout_ranks_allowed_picks_first_with_sources(client, world, alice, maker, owner_db,
                                                       drain, live):
    r = client.post(f"/tenants/{world.tenant_a}/clips/scout", headers=alice,
                    json={"youtube_query": "gta 6", "twitch_logins": ["SteadyFriend"],
                          "idempotency_key": uuid.uuid4().hex})
    assert r.status_code == 202, r.text
    Worker(maker, retry_base_seconds=0).run_once()
    run, tasks = run_state(owner_db, r.json()["run_id"])
    assert run.status == "succeeded", tasks[0].error
    out = tasks[0].output
    assert out["picks"] == 3 and out["allowed"] == 2
    top = out["top"]
    assert [t["url"] for t in top][:2] == ["https://clips.twitch.tv/c1",
                                           "https://www.youtube.com/watch?v=v2"]
    assert top[2]["rights"] == "ask the creator first" and top[2]["views"] == 50000
    k = {x["name"]: x for x in kpis(owner_db, world.tenant_a, "clips")}
    assert k["Clip scouting runs"]["value"] >= 1


def test_needs_a_source_and_keys(client, world, alice, monkeypatch):
    url = f"/tenants/{world.tenant_a}/clips/scout"
    assert client.post(url, headers=alice, json={"idempotency_key": uuid.uuid4().hex}
                       ).status_code == 422
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    r = client.post(url, headers=alice, json={"youtube_query": "x",
                                              "idempotency_key": uuid.uuid4().hex})
    assert r.status_code == 409 and "YOUTUBE_API_KEY" in r.text
