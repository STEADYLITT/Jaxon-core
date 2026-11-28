"""ADR-043: web research via Apify. Cost cap approved first, every result cited, Maps
places become lawful leads (public business listings). Apify's network is faked."""

import json
import uuid

import httpx
import pytest
from conftest import auth, run_state
from sqlalchemy import select

from jackson_api import connections as C
from jackson_api.connectors import webresearch as R
from jackson_api.models import Approval, Artifact, Contact
from jackson_api.worker import Worker

TOKEN = "apify_api_" + "t" * 30


class FakeApify:
    def __init__(self):
        self.runs = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        assert req.headers["authorization"] == f"Bearer {TOKEN}"
        p = req.url.path
        if p == "/v2/users/me":
            return httpx.Response(200, json={"data": {"username": "steady", "plan": {
                "id": "FREE", "maxMonthlyUsageUsd": 5}}})
        if p.endswith("/run-sync-get-dataset-items"):
            self.runs.append((p, dict(req.url.params), json.loads(req.content)))
            if "google-search" in p:
                return httpx.Response(201, json=[{"searchQuery": {"term": "ai trading course"},
                    "organicResults": [
                        {"title": "Course A", "url": "https://a.example/c", "position": 1,
                         "description": "Learn"},
                        {"title": "No link", "url": "", "position": 2}]}])
            if "crawler-google-places" in p:
                return httpx.Response(201, json=[
                    {"title": "Austin Roofing Co", "categoryName": "Roofing contractor",
                     "address": "1 Main St, Austin, TX", "website": "https://roof.example",
                     "phone": "+1 512-555-0100", "totalScore": 4.6, "reviewsCount": 88,
                     "url": "https://www.google.com/maps/place/?q=place_id:abc"},
                    {"title": "No URL Roofing"}])
        return httpx.Response(404, json={"error": {"message": "nope"}})


@pytest.fixture()
def live(monkeypatch):
    fake = FakeApify()
    monkeypatch.setenv("APIFY_TOKEN", TOKEN)
    mk = lambda: httpx.Client(transport=httpx.MockTransport(fake))  # noqa: E731
    monkeypatch.setattr(R, "_new_client", mk)
    monkeypatch.setattr(C, "_new_client", mk)
    return fake


@pytest.fixture()
def alice(make_token, world):
    return auth(make_token, world.alice)


def ask(client, world, alice, **body):
    return client.post(f"/tenants/{world.tenant_a}/research/web", headers=alice,
                       json={"idempotency_key": uuid.uuid4().hex, **body})


def approve_and_run(client, world, alice, maker, owner_db, run_id):
    w = Worker(maker, retry_base_seconds=0)
    w.run_once()
    ap = owner_db.scalar(select(Approval).where(Approval.run_id == uuid.UUID(run_id)))
    assert ap is not None and ap.category == "spend"
    assert client.post(f"/tenants/{world.tenant_a}/approvals/{ap.id}/decision", headers=alice,
                       json={"decision": "approve"}).status_code == 200
    w.run_once()
    return ap


def test_inputs_and_caps():
    assert R.build_input("search", ["a", "b"], items=40)["queries"] == "a\nb"
    assert R.build_input("places", ["roofer"], "Austin, TX", 30)["maxCrawledPlacesPerSearch"] == 30
    with pytest.raises(Exception, match="location"):
        R.build_input("places", ["roofer"])
    assert R.estimate_cents("places", 200) == 30 and R.estimate_cents("search", 1) == 5


def test_connection_test(client, world, alice, live):
    r = client.post(f"/tenants/{world.tenant_a}/admin/connectors/research/test", headers=alice)
    assert r.json()["ok"] and r.json()["result"]["account"] == "steady" and TOKEN not in r.text


def test_search_waits_for_the_cap_then_keeps_only_cited_results(client, world, alice, maker,
                                                                owner_db, drain, live):
    r = ask(client, world, alice, kind="search", terms=["ai trading course"], items=10)
    assert r.status_code == 202, r.text
    run_id = r.json()["run_id"]
    Worker(maker, retry_base_seconds=0).run_once()
    assert live.runs == []  # nothing runs before approval
    ap = approve_and_run(client, world, alice, maker, owner_db, run_id)
    assert "capped at $0.05" in ap.description
    run, tasks = run_state(owner_db, run_id)
    assert run.status == "succeeded", tasks[0].error
    path, params, body = live.runs[0]
    assert params["maxTotalChargeUsd"] == "0.05" and body["queries"] == "ai trading course"
    out = tasks[0].output
    assert out["results"] == 1
    art = owner_db.get(Artifact, uuid.UUID(out["artifact_id"]))
    assert art.kind == "research" and art.meta["results"] == 1


def test_places_become_lawful_leads_once(client, world, alice, maker, owner_db, drain, live):
    for _ in range(2):
        r = ask(client, world, alice, kind="places", terms=["roofer"], location="Austin, TX",
                items=20, business_id=str(world.biz_a))
        approve_and_run(client, world, alice, maker, owner_db, r.json()["run_id"])
    leads = owner_db.scalars(select(Contact).where(Contact.tenant_id == world.tenant_a,
                                                   Contact.name == "Austin Roofing Co")).all()
    assert len(leads) == 1  # deduped on the second run
    c = leads[0]
    assert c.source_type == "public_business_listing" and c.lawful_basis == "legitimate_interest"
    assert c.source_ref.startswith("https://www.google.com/maps/") and c.email is None
    assert c.stage == "new" and c.business_id == world.biz_a


def test_no_token_refuses_with_the_variable_name(client, world, alice, monkeypatch):
    monkeypatch.delenv("APIFY_TOKEN", raising=False)
    r = ask(client, world, alice, kind="trends", terms=["ai course"])
    assert r.status_code == 409 and "APIFY_TOKEN" in r.text
