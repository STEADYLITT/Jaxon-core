"""Phase 09 acceptance: memory with provenance (spec vol. 01), real Postgres.

Spec vol. 06 acceptance covered here: "memory cites source".
"""

import json
import uuid

import anthropic
import httpx2
import pytest
from conftest import auth, run_state
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from jackson_api.db import bind_tenant
from jackson_api.memory import purge_expired, recall, write_memory
from jackson_api.models import AuditEvent, MemoryRecord
from jackson_api.models_ai import ModelRouter
from jackson_api.models_ai.anthropic_provider import AnthropicProvider
from jackson_api.worker import TaskResult, Worker


@pytest.fixture()
def alice(make_token, world):
    return auth(make_token, world.alice)


def mem(client, world, headers, **kw):
    body = {"kind": "business_fact", "epistemic": "fact",
            "content": "Steady Trades University sells a $97 monthly membership via Whop"}
    body.update(kw)
    return client.post(f"/tenants/{world.tenant_a}/memory", json=body, headers=headers)


def search(client, world, headers, q=None, **params):
    if q:
        params["q"] = q
    return client.get(f"/tenants/{world.tenant_a}/memory", params=params, headers=headers)


# --- Provenance and recall ----------------------------------------------------------------

def test_owner_memory_has_provenance_and_is_recalled_with_citation(client, world, alice):
    r = mem(client, world, alice, key=f"stu.price.{uuid.uuid4().hex[:6]}")
    assert r.status_code == 201, r.text
    m = r.json()
    assert m["source_type"] == "owner" and m["status"] == "active"
    assert m["citation"]["memory_id"] == m["id"] and m["citation"]["recorded_at"]
    hits = search(client, world, alice, "membership price Whop").json()
    assert m["id"] in [h["id"] for h in hits]


def test_recall_ranks_relevant_first_and_filters_kind(client, world, alice):
    tag = uuid.uuid4().hex[:8]
    mem(client, world, alice, content=f"Shopify store {tag} best seller is a phone grip")
    mem(client, world, alice, kind="lesson", epistemic="inference",
        content=f"Ads for {tag} underperformed on Facebook")
    hits = search(client, world, alice, f"{tag} best seller").json()
    assert "best seller" in hits[0]["content"]
    lessons = search(client, world, alice, tag, kind=["lesson"]).json()
    assert [h["kind"] for h in lessons] == ["lesson"]


def test_fact_inference_hypothesis_are_distinct(client, world, alice):
    for e in ("fact", "inference", "hypothesis"):
        assert mem(client, world, alice, epistemic=e, content=f"claim as {e}").json()[
            "epistemic"] == e
    assert mem(client, world, alice, epistemic="rumor").status_code == 422


# --- Conflicts, confirmation, correction ------------------------------------------------------

def test_low_impact_conflict_supersedes(client, world, alice):
    key = f"pref.tone.{uuid.uuid4().hex[:6]}"
    a = mem(client, world, alice, kind="preference", key=key,
            content="Keep reports short").json()
    b = mem(client, world, alice, kind="preference", key=key,
            content="Reports: bullets only").json()
    assert b["status"] == "active" and b["supersedes_id"] == a["id"]
    detail = client.get(f"/tenants/{world.tenant_a}/memory/{a['id']}", headers=alice).json()
    assert detail["memory"]["status"] == "superseded"
    assert [h["id"] for h in detail["history"]] == [a["id"], b["id"]]


def test_same_content_is_not_duplicated(client, world, alice):
    key = f"dup.{uuid.uuid4().hex[:6]}"
    a = mem(client, world, alice, key=key).json()
    b = mem(client, world, alice, key=key).json()
    assert a["id"] == b["id"]


def test_high_impact_conflict_needs_confirmation(client, world, alice):
    key = f"stu.price.{uuid.uuid4().hex[:6]}"
    old = mem(client, world, alice, key=key, impact="high",
              content="STU membership is $97/month").json()
    new = mem(client, world, alice, key=key, impact="high",
              content="STU membership is $147/month").json()
    assert new["status"] == "pending_confirmation"
    hits = [h["id"] for h in search(client, world, alice, "STU membership month").json()]
    assert old["id"] in hits and new["id"] not in hits  # unconfirmed fact isn't recalled
    pend = client.get(f"/tenants/{world.tenant_a}/memory/pending", headers=alice).json()
    assert new["id"] in [p["id"] for p in pend]
    r = client.post(f"/tenants/{world.tenant_a}/memory/{new['id']}/confirm",
                    json={"accept": True}, headers=alice)
    assert r.json()["status"] == "active"
    hits = [h["id"] for h in search(client, world, alice, "STU membership month").json()]
    assert new["id"] in hits and old["id"] not in hits


def test_rejecting_a_conflict_keeps_the_original(client, world, alice):
    key = f"k.{uuid.uuid4().hex[:6]}"
    old = mem(client, world, alice, key=key, impact="high", content="Refund window 30 days").json()
    new = mem(client, world, alice, key=key, impact="high", content="Refund window 7 days").json()
    client.post(f"/tenants/{world.tenant_a}/memory/{new['id']}/confirm",
                json={"accept": False}, headers=alice)
    detail = client.get(f"/tenants/{world.tenant_a}/memory/{old['id']}", headers=alice).json()
    assert detail["memory"]["status"] == "active"


def test_correction_creates_a_new_version(client, world, alice):
    a = mem(client, world, alice, content="CSW Trades launched in 2023").json()
    r = client.post(f"/tenants/{world.tenant_a}/memory/{a['id']}/correct",
                    json={"content": "CSW Trades launched in 2024"}, headers=alice)
    assert r.status_code == 200
    b = r.json()
    assert b["supersedes_id"] == a["id"] and b["source_detail"]["correction_of"] == a["id"]
    again = client.post(f"/tenants/{world.tenant_a}/memory/{a['id']}/correct",
                        json={"content": "x"}, headers=alice)
    assert again.status_code == 409  # only the latest version can be corrected


# --- Export, deletion, retention, scope ---------------------------------------------------------

def test_delete_removes_every_version_and_audit_keeps_no_content(
        client, world, alice, owner_db):
    secret = f"private-detail-{uuid.uuid4().hex}"
    a = mem(client, world, alice, content=f"v1 {secret}").json()
    b = client.post(f"/tenants/{world.tenant_a}/memory/{a['id']}/correct",
                    json={"content": f"v2 {secret}"}, headers=alice).json()
    r = client.delete(f"/tenants/{world.tenant_a}/memory/{b['id']}", headers=alice)
    assert sorted(r.json()["deleted"]) == sorted([a["id"], b["id"]])
    owner_db.expire_all()
    assert owner_db.scalar(text("SELECT count(*) FROM memory_records WHERE content LIKE :s"),
                           {"s": f"%{secret}%"}) == 0
    audits = owner_db.scalars(select(AuditEvent).where(
        AuditEvent.tenant_id == world.tenant_a)).all()
    assert all(secret not in json.dumps(e.data) for e in audits)


def test_export_is_owner_only_and_complete(client, world, alice, make_token):
    a = mem(client, world, alice, content=f"export me {uuid.uuid4().hex}").json()
    r = client.get(f"/tenants/{world.tenant_a}/memory/export", headers=alice)
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    assert a["id"] in [m["id"] for m in r.json()]
    carol = auth(make_token, world.carol)
    assert client.get(f"/tenants/{world.tenant_a}/memory/export",
                      headers=carol).status_code == 403
    assert client.delete(f"/tenants/{world.tenant_a}/memory/{a['id']}",
                         headers=carol).status_code == 403


def test_owner_only_memory_hidden_from_viewers_and_agents(client, world, alice, make_token,
                                                          maker, owner_db, drain):
    tag = uuid.uuid4().hex[:8]
    priv = mem(client, world, alice, kind="preference", access_scope="owner_only",
               content=f"{tag} personal cash reserve target 50k").json()
    carol = auth(make_token, world.carol)
    assert priv["id"] not in [h["id"] for h in search(client, world, carol, tag).json()]
    assert client.get(f"/tenants/{world.tenant_a}/memory/{priv['id']}",
                      headers=carol).status_code == 404
    assert priv["id"] in [h["id"] for h in search(client, world, alice, tag).json()]
    with maker() as db:  # agent recall path
        assert priv["id"] not in [str(r.id) for r in recall(db, world.tenant_a, tag)]


def test_expired_memories_are_not_recalled_and_get_purged(world, app_session, owner_db):
    bind_tenant(app_session, world.tenant_a)
    tag = uuid.uuid4().hex[:8]
    rec = write_memory(app_session, tenant_id=world.tenant_a, kind="project_state",
                       epistemic="fact", content=f"{tag} temporary note", source_type="owner",
                       retain_days=1)
    rid = rec.id
    app_session.commit()
    owner_db.execute(text("UPDATE memory_records SET retain_until = now() - interval '1 second'"
                          " WHERE id = :i"), {"i": rid})
    owner_db.commit()
    bind_tenant(app_session, world.tenant_a)
    assert recall(app_session, world.tenant_a, tag) == []
    assert purge_expired(app_session) >= 1
    app_session.commit()
    assert owner_db.get(MemoryRecord, rid) is None


def test_memory_is_tenant_isolated(client, world, alice, make_token):
    tag = uuid.uuid4().hex[:8]
    a = mem(client, world, alice, content=f"{tag} tenant A only").json()
    bob = auth(make_token, world.bob)
    r = client.get(f"/tenants/{world.tenant_b}/memory", params={"q": tag}, headers=bob)
    assert r.json() == []
    assert client.get(f"/tenants/{world.tenant_b}/memory/{a['id']}",
                      headers=bob).status_code == 404
    assert client.get(f"/tenants/{world.tenant_a}/memory", headers=bob).status_code == 403


def test_other_tenants_business_rejected(client, world, alice):
    r = mem(client, world, alice, business_id=str(world.biz_b))
    assert r.status_code == 422


@pytest.mark.parametrize("stmt", ["UPDATE memory_records SET content = 'x'",
                                  "DELETE FROM memory_records"])
def test_agents_can_remember_but_never_rewrite_or_erase(maker, stmt):
    with maker() as db, pytest.raises(DBAPIError, match="permission denied"):
        db.execute(text(stmt))


# --- Agents: remember with provenance; planner cites sources ------------------------------------

def start(client, world, alice, objective):
    r = client.post(f"/tenants/{world.tenant_a}/commands", headers=alice, json={
        "type": "PLAN", "objective": objective, "business_id": str(world.biz_a),
        "idempotency_key": f"k-{uuid.uuid4().hex}"})
    return r.json()["id"]


def test_agent_memory_records_run_provenance(client, world, alice, maker, owner_db, drain):
    run_id = start(client, world, alice, "Research phone grip suppliers")

    def research(ctx):
        ctx.remember(kind="tool_result", epistemic="inference",
                     content="Supplier X quotes $1.80/unit at 500 MOQ (from their price page)")
        return TaskResult({"ok": True})

    Worker(maker, handlers={"plan": research}, retry_base_seconds=0).run_once()
    rec = owner_db.scalar(select(MemoryRecord).where(MemoryRecord.source_type == "run",
                                                     MemoryRecord.content.like("Supplier X%")))
    assert rec.source_ref.startswith(run_id) and rec.created_by_agent
    assert rec.source_detail["run_id"] == run_id and rec.epistemic == "inference"


@pytest.mark.parametrize("impact", ["high", "low"])
def test_agent_conflicts_always_wait_for_owner(client, world, alice, maker, owner_db, drain,
                                               impact):
    key = f"stu.price.{uuid.uuid4().hex[:6]}"
    original = mem(client, world, alice, key=key, impact=impact, content="STU is $97/month",
                   business_id=str(world.biz_a)).json()
    start(client, world, alice, "Update pricing knowledge")
    out = {}

    def h(ctx):
        out.update(ctx.remember(kind="business_fact", epistemic="fact", key=key,
                                impact=impact, content="STU is $47/month"))
        return TaskResult({})

    Worker(maker, handlers={"plan": h}, retry_base_seconds=0).run_once()
    assert out["status"] == "pending_confirmation"
    detail = client.get(f"/tenants/{world.tenant_a}/memory/{original['id']}",
                        headers=alice).json()
    assert detail["memory"]["status"] == "active"  # the agent did not overwrite it


def test_planner_cites_memory_sources(client, world, alice, maker, owner_db, drain):
    """Spec acceptance: memory cites source. The model cites a memory; the stored plan
    carries that memory's provenance. A made-up id is dropped."""
    tag = uuid.uuid4().hex[:8]
    m = mem(client, world, alice, business_id=str(world.biz_a),
            content=f"Phone grips {tag} sold 340 units last month at 62% margin").json()
    seen = {}

    def handler(request):
        body = json.loads(request.content)
        seen["prompt"] = body["messages"][0]["content"]
        plan = {"summary": "Double down on grips.", "steps": [], "assumptions": [],
                "risks": [], "evidence_needed": [],
                "memory_used": [m["id"], "00000000-0000-0000-0000-000000000000"]}
        return httpx2.Response(200, json={
            "id": "msg", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
            "content": [{"type": "text", "text": json.dumps(plan)}], "stop_reason": "end_turn",
            "stop_sequence": None, "usage": {"input_tokens": 10, "output_tokens": 10}})

    client_ = anthropic.Anthropic(api_key="test-key-not-real-0000", max_retries=0,
                                  http_client=anthropic.DefaultHttpxClient(
                                      transport=httpx2.MockTransport(handler)))
    run_id = start(client, world, alice, f"Should we restock phone grips {tag}?")
    Worker(maker, router=ModelRouter(providers={"anthropic": AnthropicProvider(client_)}),
           retry_base_seconds=0).run_once()
    _, tasks = run_state(owner_db, run_id)
    out = tasks[0].output
    assert m["id"] in seen["prompt"]  # the memory was offered to the model
    assert out["plan"]["memory_used"] == [m["id"]]  # fabricated id removed
    [cite] = out["memory_citations"]
    assert cite["memory_id"] == m["id"] and cite["source_type"] == "owner"
