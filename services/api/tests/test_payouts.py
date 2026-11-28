"""ADR-057: clip payouts (and creator earnings) recorded exactly as the source reports them,
once each, and counted as real revenue. Nothing is estimated."""

import uuid

import httpx
import pytest
from conftest import auth, run_state
from sqlalchemy import func, select

from jackson_api.connectors import payouts as P
from jackson_api.lines import kpis
from jackson_api.models import Business, PayoutLine, RevenueEntry
from jackson_api.worker import Worker

CSV = ("Payout ID,Clip URL,Platform,Campaign,Views,Payout,Date\n"
       "po_1,https://tiktok.com/@s/video/1,TikTok,GTA 6 Clips,120000,36.00,2026-11-03\n"
       "po_2,https://youtube.com/shorts/2,YouTube,GTA 6 Clips,40000,$12.50,2026-11-05\n"
       "po_3,https://tiktok.com/@s/video/3,TikTok,GTA 6 Clips,9000,,2026-11-06\n")


def test_rows_are_read_as_reported_and_unknown_amounts_are_skipped():
    rows, skipped = P.normalize(P.parse_csv(CSV), "clips")
    assert skipped == 1  # po_3 has no amount: never guessed
    assert [r["amount_cents"] for r in rows] == [3600, 1250]
    assert rows[0]["views"] == 120000 and rows[0]["platform"] == "tiktok"
    assert rows[1]["period"] == "2026-11" and rows[1]["reference"] == "po_2"
    again, _ = P.normalize([{"clip_url": "https://x.com/c/9", "amount_cents": 500}], "clips")
    same, _ = P.normalize([{"clip_url": "https://x.com/c/9", "amount_cents": 500}], "clips")
    assert again[0]["reference"].startswith("h:") and again[0]["reference"] == same[0][
        "reference"]  # no id: a stable hash, so a re-import never double counts
    neg, sk = P.normalize([{"url": "u", "amount": -3}], "clips")
    assert neg == [] and sk == 1


@pytest.fixture()
def alice(make_token, world):
    return auth(make_token, world.alice)


def test_upload_records_once_counts_revenue_and_fills_the_kpis(client, world, alice, maker,
                                                               owner_db, drain, monkeypatch):
    monkeypatch.delenv("CLIP_PAYOUTS_SOURCE", raising=False)
    k = {x["name"]: x for x in kpis(owner_db, world.tenant_a, "clips")}
    assert k["Payouts this month"]["value"] is None
    assert k["Payouts this month"]["note"] == "needs CLIP_PAYOUTS_SOURCE"
    owner_db.add(Business(tenant_id=world.tenant_a, name="Clip Farm", kind="media",
                          line="clips"))
    owner_db.commit()
    w = Worker(maker, retry_base_seconds=0)
    for _ in range(2):  # the same statement twice: recorded once
        r = client.post(f"/tenants/{world.tenant_a}/payouts/clips/import", headers=alice,
                        json={"csv": CSV, "idempotency_key": uuid.uuid4().hex})
        assert r.status_code == 202, r.text
        w.run_once()
        run, tasks = run_state(owner_db, r.json()["run_id"])
        assert run.status == "succeeded", tasks[0].error
    out = tasks[0].output
    assert out["new"] == 0 and out["duplicates"] == 2 and out["skipped"] == 1
    owner_db.expire_all()
    lines = owner_db.scalars(select(PayoutLine).where(
        PayoutLine.tenant_id == world.tenant_a)).all()
    assert len(lines) == 2 and all(x.business_id for x in lines)
    rev = owner_db.scalars(select(RevenueEntry).where(
        RevenueEntry.tenant_id == world.tenant_a, RevenueEntry.source == "import")).all()
    assert sorted(x.amount_cents for x in rev) == [1250, 3600]
    assert {x.reference for x in rev} == {"clips:po_1", "clips:po_2"}
    from datetime import UTC, datetime
    this_month = datetime.now(UTC).strftime("%Y-%m") == "2026-11"
    k = {x["name"]: x for x in kpis(owner_db, world.tenant_a, "clips")}
    assert k["Payouts this month"]["value"] == (48.5 if this_month else 0)
    assert k["Views paid on this month"]["value"] == (160000 if this_month else 0)
    kb = {x["name"]: x for x in kpis(owner_db, world.tenant_b, "clips")}
    assert kb["Payouts this month"]["value"] is None  # other tenant: nothing
    assert owner_db.scalar(select(func.count()).select_from(PayoutLine).where(
        PayoutLine.tenant_id == world.tenant_b)) == 0


def test_pull_from_the_feed_and_the_connection_test(client, world, alice, maker, owner_db,
                                                    drain, monkeypatch):
    monkeypatch.setenv("CLIP_PAYOUTS_SOURCE", "https://payouts.example/clips.json")
    monkeypatch.setenv("CLIP_PAYOUTS_TOKEN", "tok-" + "c" * 20)

    def handler(req):
        assert req.headers["authorization"] == "Bearer tok-" + "c" * 20
        return httpx.Response(200, json={"payouts": [
            {"id": "f1", "url": "https://tiktok.com/v/7", "views": 5000, "amount": 2.5,
             "paid_at": "2026-11-10T12:00:00Z"}]})
    mk = lambda: httpx.Client(transport=httpx.MockTransport(handler))  # noqa: E731
    monkeypatch.setattr(P, "_new_client", mk)
    before = owner_db.scalar(select(func.count()).select_from(PayoutLine))
    t = client.post(f"/tenants/{world.tenant_a}/admin/connectors/clip_payouts/test",
                    headers=alice)
    assert t.json()["ok"], t.text
    assert t.json()["result"]["usable"] == 1 and "c" * 20 not in t.text
    assert owner_db.scalar(select(func.count()).select_from(PayoutLine)) == before  # test: none
    r = client.post(f"/tenants/{world.tenant_a}/payouts/clips/pull", headers=alice,
                    json={"idempotency_key": uuid.uuid4().hex})
    assert r.status_code == 202, r.text
    Worker(maker, retry_base_seconds=0).run_once()
    run, tasks = run_state(owner_db, r.json()["run_id"])
    assert run.status == "succeeded", tasks[0].error
    assert tasks[0].output["source"] == "feed" and tasks[0].output["amount_cents"] == 250


def test_pull_without_a_feed_is_refused_and_import_mode_is_mock_free(client, world, alice,
                                                                     monkeypatch):
    monkeypatch.setenv("CLIP_PAYOUTS_SOURCE", "import")
    r = client.post(f"/tenants/{world.tenant_a}/payouts/clips/pull", headers=alice,
                    json={"idempotency_key": uuid.uuid4().hex})
    assert r.status_code == 409 and "CLIP_PAYOUTS_SOURCE" in r.text
    t = client.post(f"/tenants/{world.tenant_a}/admin/connectors/clip_payouts/test",
                    headers=alice)
    assert t.json()["ok"] and t.json()["result"]["mode"] == "import"


def test_creator_earnings_per_creator_and_mock_until_configured(client, world, alice, maker,
                                                                owner_db, drain, monkeypatch):
    monkeypatch.delenv("CREATOR_EARNINGS_SOURCE", raising=False)
    k = {x["name"]: x for x in kpis(owner_db, world.tenant_a, "creators")}
    assert k["Creator earnings this month"]["value"] is None
    assert k["Creator earnings this month"]["note"] == "needs CREATOR_EARNINGS_SOURCE"
    t = client.post(f"/tenants/{world.tenant_a}/admin/connectors/creator_earnings/test",
                    headers=alice)
    assert t.status_code == 409 or not t.json().get("ok")  # MOCK: nothing to test
    from datetime import UTC, datetime
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    rows = [{"creator": "Creator X", "platform": "Fanvue", "net_earnings": "1,240.00",
             "date": today, "transaction_id": "fv-1"},
            {"creator": "Creator X", "platform": "Fanvue", "net_earnings": "60", "date": today,
             "transaction_id": "fv-2"},
            {"creator": "Creator Y", "platform": "OnlyFans", "net_earnings": "300",
             "date": today, "transaction_id": "of-1"},
            {"creator": "Creator Z", "platform": "OnlyFans", "date": today}]  # no amount
    r = client.post(f"/tenants/{world.tenant_a}/payouts/creators/import",
                    headers=alice, json={"rows": rows, "idempotency_key": uuid.uuid4().hex})
    assert r.status_code == 202, r.text
    Worker(maker, retry_base_seconds=0).run_once()
    run, tasks = run_state(owner_db, r.json()["run_id"])
    assert run.status == "succeeded", tasks[0].error
    assert tasks[0].kind == "creator_earnings_import" and tasks[0].output["skipped"] == 1
    k = {x["name"]: x for x in kpis(owner_db, world.tenant_a, "creators")}
    assert k["Creator earnings this month"]["value"] == 1600.0
    assert k["Creators paid this month"]["value"] == 2
    ka = {x["name"]: x for x in kpis(owner_db, world.tenant_b, "creators")}
    assert ka["Creator earnings this month"]["value"] is None  # other tenant: nothing
