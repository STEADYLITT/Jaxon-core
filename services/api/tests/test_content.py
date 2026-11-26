"""Phase 16: content + virtual influencer workflow (spec vol. 03).

Spec vol. 06 acceptance covered here: "content remains draft until approved".
"""

import json
import uuid
from datetime import UTC, datetime, timedelta

import anthropic
import httpx2
import pytest
from conftest import auth, events, run_state
from sqlalchemy import text, update
from sqlalchemy.exc import DBAPIError

from jackson_api.content import review
from jackson_api.models import ContentItem, Task
from jackson_api.models_ai import ModelRouter
from jackson_api.models_ai.anthropic_provider import AnthropicProvider
from jackson_api.worker import Worker

NOVA = {"id": "p", "name": "Nova Vale", "kind": "virtual_influencer", "fictional": True,
        "brand_bible": {}, "disclosure": "AI-generated character",
        "do_not_impersonate": ["Taylor Swift"]}


def item(**kw):
    base = {"channel": "instagram", "title": "Morning routine", "hashtags": ["trading"],
            "body": "Charts before coffee. AI-generated character", "sponsored": False,
            "assets": []}
    base.update(kw)
    return base


# --- Review rules -------------------------------------------------------------------------------

def test_clean_disclosed_post_passes():
    assert review(item(), NOVA)["passed"] is True


@pytest.mark.parametrize("kw,needle", [
    ({"body": "Charts before coffee."}, "missing AI disclosure"),
    ({"body": "Hi, I'm Taylor Swift! AI-generated character"}, "impersonates"),
    ({"body": "Join 10,000 reviews of happy traders. AI-generated character"}, "review"),
    ({"body": "Guaranteed profit every week. AI-generated character"}, "guaranteed"),
    ({"sponsored": True}, "#ad"),
    ({"channel": "x", "body": "x" * 300 + " AI-generated character"}, "280"),
    ({"hashtags": [f"t{i}" for i in range(31)]}, "hashtags"),
    ({"assets": [{"ref": "photo.jpg", "rights": "found online"}]}, "cleared rights"),
    ({"assets": [{"ref": "song.mp3", "rights": "licensed"}]}, "proof of license"),
])
def test_review_catches_problems(kw, needle):
    rv = review(item(**kw), NOVA)
    assert rv["passed"] is False and any(needle in p for p in rv["problems"]), rv["problems"]


def test_real_likeness_needs_consent():
    p = {**NOVA, "brand_bible": {"real_person_likeness": True}}
    assert "consent" in " ".join(review(item(), p)["problems"])


# --- API: personas, approval, scheduling -------------------------------------------------------

@pytest.fixture()
def alice(make_token, world):
    return auth(make_token, world.alice)


def persona(client, world, alice, **kw):
    body = {"name": f"Nova {uuid.uuid4().hex[:4]}", "kind": "virtual_influencer",
            "bio": "Fictional trading coach", "do_not_impersonate": ["Taylor Swift"]}
    body.update(kw)
    return client.post(f"/tenants/{world.tenant_a}/personas", json=body, headers=alice)


def content(client, world, alice, pid, **kw):
    body = {"channel": "instagram", "title": "Morning routine", "persona_id": pid,
            "body": "Charts before coffee. AI-generated character", "hashtags": ["trading"]}
    body.update(kw)
    r = client.post(f"/tenants/{world.tenant_a}/content", json=body, headers=alice)
    assert r.status_code == 201, r.text
    return r.json()


def test_virtual_influencer_must_be_fictional(client, world, alice, owner_db):
    assert persona(client, world, alice, fictional=False).status_code == 422
    with pytest.raises(DBAPIError, match="ck_persona_fictional"):
        owner_db.execute(text("INSERT INTO personas (id, tenant_id, name, kind, fictional, bio, "
                              "brand_bible, disclosure, do_not_impersonate, status) VALUES "
                              "(gen_random_uuid(), :t, 'x', 'virtual_influencer', false, '', "
                              "'{}', 'AI', '[]', 'active')"), {"t": world.tenant_a})
    owner_db.rollback()


def test_approval_requires_a_passing_review(client, world, alice):
    pid = persona(client, world, alice).json()["id"]
    bad = content(client, world, alice, pid, body="Charts before coffee.")
    assert bad["status"] == "draft" and not bad["review"]["passed"]
    r = client.post(f"/tenants/{world.tenant_a}/content/{bad['id']}/approve", headers=alice)
    assert r.status_code == 409 and "disclosure" in str(r.json())
    good = content(client, world, alice, pid)
    assert good["status"] == "in_review"
    ok = client.post(f"/tenants/{world.tenant_a}/content/{good['id']}/approve", headers=alice)
    assert ok.json()["status"] == "approved" and ok.json()["approved_by"]


def test_editing_clears_approval(client, world, alice):
    pid = persona(client, world, alice).json()["id"]
    c = content(client, world, alice, pid)
    client.post(f"/tenants/{world.tenant_a}/content/{c['id']}/approve", headers=alice)
    e = client.patch(f"/tenants/{world.tenant_a}/content/{c['id']}", headers=alice,
                     json={"body": "New text. AI-generated character"}).json()
    assert e["status"] == "in_review" and e["approved_by"] is None


def test_viewer_cannot_approve(client, world, alice, make_token):
    pid = persona(client, world, alice).json()["id"]
    c = content(client, world, alice, pid)
    r = client.post(f"/tenants/{world.tenant_a}/content/{c['id']}/approve",
                    headers=auth(make_token, world.carol))
    assert r.status_code == 403


def schedule(client, world, alice, cid, when):
    return client.post(f"/tenants/{world.tenant_a}/content/{cid}/schedule", headers=alice,
                       json={"scheduled_for": when.isoformat(),
                             "idempotency_key": uuid.uuid4().hex})


def test_unapproved_content_cannot_be_scheduled(client, world, alice):
    pid = persona(client, world, alice).json()["id"]
    c = content(client, world, alice, pid)
    assert schedule(client, world, alice, c["id"], datetime.now(UTC)).status_code == 409


def test_scheduled_post_waits_for_its_time_then_publishes(client, world, alice, maker, owner_db,
                                                          drain):
    pid = persona(client, world, alice).json()["id"]
    c = content(client, world, alice, pid)
    client.post(f"/tenants/{world.tenant_a}/content/{c['id']}/approve", headers=alice)
    r = schedule(client, world, alice, c["id"], datetime.now(UTC) + timedelta(hours=2))
    run_id = r.json()["run_id"]
    w = Worker(maker, retry_base_seconds=0)
    assert w.run_once() is False  # not due yet
    owner_db.execute(update(Task).where(Task.run_id == uuid.UUID(run_id))
                     .values(next_attempt_at=datetime.now(UTC) - timedelta(seconds=1)))
    owner_db.commit()
    assert w.run_once() is True
    run, tasks = run_state(owner_db, run_id)
    assert run.status == "succeeded" and tasks[0].output["connector"] == "mock"
    got = client.get(f"/tenants/{world.tenant_a}/content/{c['id']}", headers=alice).json()
    assert got["status"] == "published" and got["external_post_id"].startswith("mock-post-")
    assert "content.published" in events(owner_db, run_id)
    appr = client.get(f"/tenants/{world.tenant_a}/approvals", headers=alice).json()
    assert any(a["run_id"] == run_id and a["status"] == "approved"
               and a["category"] == "public_post" for a in appr)  # the schedule click


def test_rejecting_a_scheduled_post_stops_it(client, world, alice, maker, owner_db, drain):
    pid = persona(client, world, alice).json()["id"]
    c = content(client, world, alice, pid)
    client.post(f"/tenants/{world.tenant_a}/content/{c['id']}/approve", headers=alice)
    run_id = schedule(client, world, alice, c["id"], datetime.now(UTC)).json()["run_id"]
    r = client.post(f"/tenants/{world.tenant_a}/content/{c['id']}/reject", headers=alice)
    assert r.json()["status"] == "rejected"
    Worker(maker, retry_base_seconds=0).run_once()
    run, tasks = run_state(owner_db, run_id)
    assert run.status == "failed" and "not approved" in (tasks[0].error or "")
    got = client.get(f"/tenants/{world.tenant_a}/content/{c['id']}", headers=alice).json()
    assert got["status"] == "rejected" and got["external_post_id"] is None


# --- Content remains draft until approved (database-enforced) -------------------------------------

def test_content_remains_draft_until_approved(client, world, alice, maker, owner_db):
    """Spec acceptance: content remains draft until approved. Even with direct database
    access, the worker (where agents run) can't approve, schedule or publish content."""
    pid = persona(client, world, alice).json()["id"]
    c = content(client, world, alice, pid)
    cid = uuid.UUID(c["id"])
    for status in ("approved", "scheduled", "published"):
        with maker() as db, pytest.raises(DBAPIError, match="ck_content_needs_approval"):
            db.execute(update(ContentItem).where(ContentItem.id == cid).values(status=status))
            db.flush()
    with maker() as db, pytest.raises(DBAPIError, match="permission denied"):
        db.execute(text("UPDATE content_items SET approved_by = NULL"))
    with maker() as db, pytest.raises(DBAPIError, match="row-level security"):
        db.add(ContentItem(tenant_id=world.tenant_a, channel="x", kind="post", body="b",
                           status="approved", review={}))
        db.flush()
    owner_db.expire_all()
    assert owner_db.get(ContentItem, cid).status == "in_review"


# --- Generation ---------------------------------------------------------------------------------

def fake_router(reply):
    def handler(request):
        return httpx2.Response(200, json={
            "id": "m", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
            "content": [{"type": "text", "text": json.dumps(reply)}], "stop_reason": "end_turn",
            "stop_sequence": None, "usage": {"input_tokens": 10, "output_tokens": 10}})
    c = anthropic.Anthropic(api_key="test-key-not-real-0000", max_retries=0,
                            http_client=anthropic.DefaultHttpxClient(
                                transport=httpx2.MockTransport(handler)))
    return ModelRouter(providers={"anthropic": AnthropicProvider(c)})


def test_generated_drafts_are_disclosed_and_reviewed_never_approved(client, world, alice, maker,
                                                                    owner_db, drain):
    pid = persona(client, world, alice).json()["id"]
    reply = {"items": [
        {"kind": "post", "title": "Risk first", "body": "Size small, live long.",
         "hashtags": ["trading"]},
        {"kind": "caption", "title": "Hype", "body": "Guaranteed profit, 5 stars!",
         "hashtags": []}]}
    r = client.post(f"/tenants/{world.tenant_a}/content/generate", headers=alice, json={
        "persona_id": pid, "brief": "Daily trading discipline tips", "count": 2})
    Worker(maker, router=fake_router(reply), retry_base_seconds=0).run_once()
    out = run_state(owner_db, r.json()["run_id"])[1][0].output
    assert [c["passed"] for c in out["created"]] == [True, False]
    items = [client.get(f"/tenants/{world.tenant_a}/content/{c['id']}", headers=alice).json()
             for c in out["created"]]
    assert "AI-generated character" in items[0]["body"]  # disclosure always appended
    assert {i["status"] for i in items} == {"in_review", "draft"}
    assert all(i["approved_by"] is None for i in items)


def test_generation_without_a_model_is_mock_and_creates_nothing(client, world, alice, maker,
                                                                owner_db, drain):
    pid = persona(client, world, alice).json()["id"]
    r = client.post(f"/tenants/{world.tenant_a}/content/generate", headers=alice,
                    json={"persona_id": pid, "brief": "anything at all"})
    Worker(maker, retry_base_seconds=0).run_once()
    out = run_state(owner_db, r.json()["run_id"])[1][0].output
    assert out["mode"] == "mock" and out["created"] == []


def test_content_is_tenant_isolated(client, world, alice, make_token):
    pid = persona(client, world, alice).json()["id"]
    c = content(client, world, alice, pid)
    bob = auth(make_token, world.bob)
    assert client.get(f"/tenants/{world.tenant_b}/content/{c['id']}",
                      headers=bob).status_code == 404
