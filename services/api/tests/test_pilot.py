"""Phase 20: end-to-end pilot of the first vertical slice (spec vol. 06).

"Owner enters: Find a product opportunity and build a draft store. Research agent returns
evidence; finance calculates margin; planner requests approval for any paid action;
builder creates draft in sandbox; QA verifies; dashboard shows artifact, cost and run
history. No public launch required."

Everything goes through the HTTP API and the real worker against real PostgreSQL. The only
stand-ins are labeled: a local web page as the research source, a faked Claude reply, and
the mock Shopify store.
"""

import uuid

from conftest import auth, events, run_state
from test_opportunities import _no_proxy, fake_router, site  # noqa: F401 (fixtures)

from jackson_api.models import ShopProduct
from jackson_api.worker import Worker


def test_first_vertical_slice_end_to_end(client, world, make_token, maker, owner_db, drain,
                                         site, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setenv("ARTIFACT_DIR", str(tmp_path))
    alice = auth(make_token, world.alice)
    t, biz = world.tenant_a, str(world.biz_a)

    # 0. Owner setup: autonomy level 1 (drafts only), permit one research site + mock store
    assert client.put(f"/tenants/{t}/businesses/{biz}/autonomy", headers=alice,
                      json={"level": 1}).status_code == 200
    client.post(f"/tenants/{t}/tool-grants", headers=alice, json={
        "tool_name": "web.fetch", "config": {"allowed_domains": ["127.0.0.1"]}})
    client.post(f"/tenants/{t}/tool-grants", headers=alice, json={
        "tool_name": "shopify.drafts", "business_id": biz, "config": {"mode": "mock"}})

    # 1. Research agent returns evidence (sourced signals only)
    src = f"{site}/grips"
    tag = uuid.uuid4().hex[:6]
    reply = {"candidates": [{
        "title": f"MagGrip phone grip {tag}", "category": "phone accessories",
        "summary": "Popular grip.", "signals": [
            {"type": "review_count", "value": 4200, "source_url": src, "note": ""},
            {"type": "avg_price", "value": 24.99, "source_url": src, "note": ""},
            {"type": "competitor_count", "value": 30, "source_url": src, "note": ""}]}]}
    r = client.post(f"/tenants/{t}/opportunities/research", headers=alice, json={
        "query": "Find a product opportunity", "urls": [src], "business_id": biz,
        "idempotency_key": uuid.uuid4().hex})
    research_run = r.json()["run_id"]
    Worker(maker, router=fake_router(reply, {}), retry_base_seconds=0).run_once()
    run, tasks = run_state(owner_db, research_run)
    assert run.status == "succeeded", tasks[0].error
    opp_id = tasks[0].output["opportunities"][0]["id"]
    opp = client.get(f"/tenants/{t}/opportunities/{opp_id}", headers=alice).json()
    assert opp["signals"] and all(s["source_url"] == src for s in opp["signals"])

    # 2. Finance calculates margin
    econ = client.post(f"/tenants/{t}/opportunities/{opp_id}/economics", headers=alice, json={
        "price": 24.99, "unit_cost": 5, "outbound_shipping_per_order": 4, "cac": 6}).json()
    assert econ["economics"]["contribution_margin_pct"] > 0

    # 3. Build: at autonomy level 1 the sandbox change waits for the owner's approval
    r = client.post(f"/tenants/{t}/opportunities/{opp_id}/build-store", headers=alice,
                    json={"idempotency_key": uuid.uuid4().hex})
    build_run = r.json()["run_id"]
    w = Worker(maker, retry_base_seconds=0)
    w.run_once()
    assert run_state(owner_db, build_run)[0].status == "waiting_approval"
    assert owner_db.query(ShopProduct).filter(
        ShopProduct.opportunity_id == uuid.UUID(opp_id)).count() == 0  # nothing yet
    [ap] = [a for a in client.get(f"/tenants/{t}/approvals?status=pending",
                                  headers=alice).json() if a["run_id"] == build_run]
    assert ap["category"] == "sandbox_change"
    client.post(f"/tenants/{t}/approvals/{ap['id']}/decision", headers=alice,
                json={"decision": "approve", "note": "pilot: go ahead with the draft"})

    # 4. Builder creates the draft in the sandbox store; QA verifies it independently
    w.run_once()
    run, tasks = run_state(owner_db, build_run)
    assert run.status == "succeeded", [x.error for x in tasks]
    out = tasks[0].output
    assert out["qa_passed"] and out["draft"]["status"] == "DRAFT" and out["draft"]["verified"]
    assert out["draft"]["backend"] == "mock"
    assert "shopify.published" not in events(owner_db, build_run)  # no public launch

    # 5. Dashboard shows the artifact, the cost and the run history
    d = client.get(f"/tenants/{t}/dashboard", headers=alice).json()
    runs = {x["id"]: x for x in d["runs"]}
    assert runs[research_run]["status"] == "succeeded" and runs[build_run]["status"] == \
        "succeeded"
    assert runs[research_run]["model_cost_usd"] > 0  # the (faked) Claude call was metered
    assert any(a["id"] == out["report_artifact_id"] and a["type"] == "store_build_qa"
               for a in d["artifacts"])
    assert d["approvals"]["pending"] == 0
    assert d["portfolio"]["total"]["model_cost_usd"] > 0
    assert d["opportunities"]["top"][0]["has_economics"] is True
    assert {c["id"]: c["mode"] for c in d["connectors"]}["shopify"] == "mock"

    # 6. The daily report tells the story
    rep = client.get(f"/tenants/{t}/reports/daily/preview", headers=alice).json()
    assert len(rep["outcomes"]) >= 2 and rep["numbers"]["pending_approvals"] == 0

    # 7. And the owner can still stop everything at any point
    stop = client.post(f"/tenants/{t}/stop", headers=alice, json={"reason": "pilot done"})
    assert stop.status_code == 200
    assert client.post(f"/tenants/{t}/opportunities/{opp_id}/build-store", headers=alice,
                       json={"idempotency_key": uuid.uuid4().hex}).status_code == 423
