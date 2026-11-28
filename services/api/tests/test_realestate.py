"""ADR-046: real-estate research via BatchData. Comps cost approved first; skip tracing is
personal data and always the owner's decision. BatchData's network is faked."""

import json
import uuid

import httpx
import pytest
from conftest import auth, run_state
from sqlalchemy import select

from jackson_api.connectors import realestate as RE
from jackson_api.models import Approval, Artifact
from jackson_api.realestate import summarize
from jackson_api.worker import Worker

TOKEN = "b" * 40
ADDR = {"street": "41028 N Congressional Dr", "city": "Phoenix", "state": "AZ",
        "zip": "85086"}


class FakeBatch:
    def __init__(self):
        self.calls = []

    def bodies(self, path):
        return [json.loads(c.content) for c in self.calls if c.url.path.endswith(path)]

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.calls.append(req)
        assert req.headers["authorization"] == f"Bearer {TOKEN}"
        if req.url.path == "/api/v1/property/search":
            return httpx.Response(200, json={"status": {"code": 200}, "results": {"properties": [
                {"address": {"street": "1 A St", "city": "Phoenix", "state": "AZ"},
                 "building": {"bedroomCount": 3, "livingAreaSquareFeet": 1500},
                 "sale": {"lastSale": {"price": 300000, "saleDate": "2026-06-01"}}},
                {"address": {"street": "2 B St"}, "building": {"livingAreaSquareFeet": 2000},
                 "sale": {"lastSale": {"price": 500000}}},
                {"address": {"street": "3 C St"}}]}})
        if req.url.path == "/api/v1/property/skip-trace":
            return httpx.Response(200, json={"results": {"persons": [
                {"name": {"first": "Pat", "last": "Owner"}, "emails": [{"email": "p@x.com"}],
                 "phoneNumbers": [{"number": "6025550100", "type": "Mobile", "dnc": False},
                                  {"number": "6025550101", "type": "Land Line", "dnc": True}],
                 "dnc": False, "litigator": False}],
                "meta": {"results": {"requestCount": 1, "matchCount": 1}}}})
        return httpx.Response(404, json={"status": {"message": "not found"}})


@pytest.fixture()
def live(monkeypatch):
    fake = FakeBatch()
    monkeypatch.setenv("BATCHDATA_API_TOKEN", TOKEN)
    monkeypatch.setenv("BATCHDATA_CENTS_PER_RECORD", "3")
    monkeypatch.setattr(RE, "_new_client",
                        lambda: httpx.Client(transport=httpx.MockTransport(fake)))
    return fake


@pytest.fixture()
def alice(make_token, world):
    return auth(make_token, world.alice)


def approve_and_run(client, world, alice, maker, owner_db, run_id, category):
    w = Worker(maker, retry_base_seconds=0)
    w.run_once()
    ap = owner_db.scalar(select(Approval).where(Approval.run_id == uuid.UUID(run_id)))
    assert ap is not None and ap.category == category
    client.post(f"/tenants/{world.tenant_a}/approvals/{ap.id}/decision", headers=alice,
                json={"decision": "approve"})
    w.run_once()
    run, tasks = run_state(owner_db, run_id)
    assert run.status == "succeeded", tasks[0].error
    return ap, tasks[0].output


def test_summary_uses_only_real_numbers():
    s = summarize([{"last_sale_price": 300000, "sqft": 1500}, {"last_sale_price": 500000,
                                                               "sqft": 2000}, {}])
    assert s == {"comps": 3, "with_sale_price": 2, "median_sale_price": 400000,
                 "median_price_per_sqft": 225.0}
    assert summarize([])["median_sale_price"] is None
    with pytest.raises(Exception, match="street"):
        RE.address({"city": "Phoenix"})


def test_connection_check_needs_the_price(client, world, alice, monkeypatch):
    monkeypatch.setenv("BATCHDATA_API_TOKEN", TOKEN)
    monkeypatch.setenv("BATCHDATA_CENTS_PER_RECORD", "3")
    r = client.post(f"/tenants/{world.tenant_a}/admin/connectors/realestate/test", headers=alice)
    assert r.json()["ok"] and r.json()["result"]["price_per_record_cents"] == 3
    assert TOKEN not in r.text


def test_comps_cost_first_then_cited_results(client, world, alice, maker, owner_db, drain,
                                             live):
    r = client.post(f"/tenants/{world.tenant_a}/realestate/comps", headers=alice,
                    json={"address": ADDR, "take": 10, "idempotency_key": uuid.uuid4().hex})
    assert r.status_code == 202, r.text
    assert r.json()["estimate_cents"] == 30
    ap, out = approve_and_run(client, world, alice, maker, owner_db, r.json()["run_id"], "spend")
    assert "est. $0.30" in ap.description
    body = live.bodies("/property/search")[0]
    assert body["searchCriteria"]["compAddress"] == ADDR and body["options"]["take"] == 10
    assert out["comps"] == 3 and out["median_sale_price"] == 400000 and out["cost_cents"] == 9
    art = owner_db.get(Artifact, uuid.UUID(out["artifact_id"]))
    assert art.meta["type"] == "property_comps"


def test_skip_trace_is_sensitive_and_marks_dnc(client, world, alice, maker, owner_db, drain,
                                               live):
    r = client.post(f"/tenants/{world.tenant_a}/realestate/skip-trace", headers=alice,
                    json={"address": ADDR, "idempotency_key": uuid.uuid4().hex})
    Worker(maker, retry_base_seconds=0).run_once()
    assert live.bodies("/skip-trace") == []  # nothing looked up before approval
    w = Worker(maker, retry_base_seconds=0)
    run_id = r.json()["run_id"]
    for category in ("sensitive_data", "spend"):  # the personal data, then its cost
        ap = owner_db.scalar(select(Approval).where(Approval.run_id == uuid.UUID(run_id),
                                                    Approval.status == "pending"))
        assert ap.category == category, ap.category
        if category == "sensitive_data":
            assert "Personal data" in ap.description
        assert live.bodies("/skip-trace") == []
        client.post(f"/tenants/{world.tenant_a}/approvals/{ap.id}/decision", headers=alice,
                    json={"decision": "approve"})
        w.run_once()
        owner_db.expire_all()
    run, tasks = run_state(owner_db, run_id)
    assert run.status == "succeeded", tasks[0].error
    out = tasks[0].output
    assert out["people"] == 1 and out["phones_ok_to_call"] == 1 and out["cost_cents"] == 3
    art = owner_db.get(Artifact, uuid.UUID(out["artifact_id"]))
    assert art.kind == "sensitive" and art.meta["personal_data"] is True


def test_not_connected(client, world, alice, monkeypatch):
    monkeypatch.delenv("BATCHDATA_API_TOKEN", raising=False)
    r = client.post(f"/tenants/{world.tenant_a}/realestate/comps", headers=alice,
                    json={"address": ADDR, "idempotency_key": uuid.uuid4().hex})
    assert r.status_code == 409 and "BATCHDATA_API_TOKEN" in r.text
