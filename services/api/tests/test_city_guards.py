"""Phase 23, part 6: cost guard, failure siren, trace view and the read-only client view."""

import uuid
from dataclasses import replace

from conftest import auth, new_run
from sqlalchemy import select, text

from jackson_api.models import Agent, Business, Event
from jackson_api.models_ai import ModelRequest
from jackson_api.models_ai.mock_provider import MockProvider
from jackson_api.runs import create_run
from jackson_api.worker import TaskResult, Worker
from jackson_api.worker_errors import NonRetryableError


def agent_row(owner_db, world, key):
    owner_db.expire_all()
    return owner_db.scalar(select(Agent).where(Agent.tenant_id == world.tenant_a,
                                               Agent.key == key))


def letters(owner_db, world, kind):
    owner_db.expire_all()
    return owner_db.scalars(select(Event).where(
        Event.tenant_id == world.tenant_a, Event.type == "alert.raised")
        .order_by(Event.id)).all()


def test_cost_guard_pauses_the_agent_then_sends_a_red_letter(client, world, make_token, maker,
                                                             owner_db, drain, monkeypatch):
    h = auth(make_token, world.alice)
    real = MockProvider.complete
    monkeypatch.setattr(MockProvider, "complete",
                        lambda self, req, model: replace(real(self, req, model),
                                                         cost_usd_micros=50_000))  # $0.05

    def plan(ctx):
        ctx.call_model("fast", ModelRequest(system="s", user="u", max_tokens=10))
        return TaskResult({})

    w = Worker(maker, handlers={"plan": plan}, retry_base_seconds=0)
    w.city_heartbeat()
    a = agent_row(owner_db, world, "kind:plan")
    r = client.patch(f"/tenants/{world.tenant_a}/city/agents/{a.id}", headers=h,
                     json={"daily_limit_usd": 0.04})
    assert r.json()["daily_limit_usd"] == 0.04
    new_run(client, world, make_token)
    w.run_once()  # one $0.05 call: over the $0.04 limit
    a = agent_row(owner_db, world, "kind:plan")
    assert a.status == "paused"
    paused = owner_db.scalars(select(Event).where(Event.agent_id == a.id,
                                                  Event.type == "agent.paused")).one()
    assert paused.payload["reason"] == "cost guard"
    letter = [e for e in letters(owner_db, world, "cost_guard")
              if e.payload["kind"] == "cost_guard"][-1]
    assert letter.id > paused.id and letter.to_kind == "ceo"  # paused first, then the letter
    assert "over its $0.04 limit" in letter.payload["message"]
    s = client.get(f"/tenants/{world.tenant_a}/city/state", headers=h).json()
    assert any(x["payload"]["kind"] == "cost_guard" for x in s["inbox"]["letters"])
    new_run(client, world, make_token)
    assert w.run_once() is False  # no more spending until Steady resumes it
    assert client.patch(f"/tenants/{world.tenant_a}/city/agents/{a.id}",
                        headers=auth(make_token, world.carol),
                        json={"daily_limit_usd": 100}).status_code == 403


def _report_run(owner_db, world):
    uid = owner_db.scalar(text("SELECT id FROM users WHERE auth_subject=:s"), {"s": world.alice})
    run, _ = create_run(owner_db, world.tenant_a, uid, {
        "type": "BUILD", "objective": "report", "idempotency_key": uuid.uuid4().hex},
        first_task={"kind": "daily_report", "title": "Daily report", "tool_scope": []})
    owner_db.commit()
    return run.id


def test_three_failures_in_a_row_sound_the_siren_once(client, world, make_token, maker,
                                                      owner_db, drain):
    def broken(ctx):
        raise NonRetryableError("report source unreachable")

    w = Worker(maker, handlers={"daily_report": broken}, retry_base_seconds=0)
    w.city_heartbeat()
    for _ in range(4):
        _report_run(owner_db, world)
        w.run_once()
    sirens = [e for e in letters(owner_db, world, "") if e.payload["kind"] == "failure_siren"]
    assert len(sirens) == 1  # sounded at the 3rd failure, not again at the 4th
    assert "failed 3 times in a row" in sirens[0].payload["message"]
    s = client.get(f"/tenants/{world.tenant_a}/city/state",
                   headers=auth(make_token, world.alice)).json()
    finance = next(d["id"] for d in s["departments"] if d["key"] == "finance")
    assert s["sirens"] == [finance]  # the Finance building flashes red


def test_trace_from_an_event_back_to_the_command(client, world, make_token, maker, owner_db,
                                                 drain):
    r = client.post(f"/tenants/{world.tenant_a}/commands",
                    headers=auth(make_token, world.alice),
                    json={"type": "PLAN", "objective": "Write an Instagram post about grips",
                          "route": True, "idempotency_key": uuid.uuid4().hex})
    run_id = r.json()["id"]
    w = Worker(maker, retry_base_seconds=0)
    w.run_once()
    w.run_once()
    t = client.get(f"/tenants/{world.tenant_a}/city/trace/{run_id}",
                   headers=auth(make_token, world.alice)).json()
    assert t["command"]["text"] == "Write an Instagram post about grips"
    assert t["routing"][0]["payload"]["agent"] == "Content Writer"
    assert [s["agent"] for s in t["tasks"]] == ["Jaxon Planner", "Content Writer"]
    assert all(s["status"] == "succeeded" for s in t["tasks"])
    assert t["events"][0]["type"] == "command.issued"


def test_client_link_is_read_only_scoped_revocable(client, world, make_token, maker, owner_db,
                                                   drain, monkeypatch):
    monkeypatch.setenv("UNSUBSCRIBE_SECRET", "s" * 40)
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://jackson.example.com")
    h = auth(make_token, world.alice)
    other = Business(tenant_id=world.tenant_a, name="Other client", kind="agency")
    owner_db.add(other)
    owner_db.commit()
    for biz, text_ in ((world.biz_a, "Client A work"), (other.id, "Secret other work")):
        r = client.post(f"/tenants/{world.tenant_a}/commands", headers=h, json={
            "type": "PLAN", "objective": text_, "business_id": str(biz),
            "idempotency_key": uuid.uuid4().hex})
        assert r.status_code == 201, r.text
    w = Worker(maker, retry_base_seconds=0)
    w.city_heartbeat()
    w.run_once()
    w.run_once()
    r = client.post(f"/tenants/{world.tenant_a}/city/shares", headers=h,
                    json={"business_id": str(world.biz_a), "days": 7})
    assert r.status_code == 201, r.text
    share = r.json()
    assert share["url"].startswith("https://jackson.example.com/city?share=")
    s = client.get(f"/city/share/{share['token']}/state")  # no login: the link is the key
    assert s.status_code == 200
    body = s.text
    assert "Client A work" in body and "Secret other work" not in body
    j = s.json()
    assert j["client"]["read_only"] is True and j["inbox"]["approvals"] == []
    assert j["hud"]["spend_today_usd"] == 0 and j["hud"]["revenue_today_usd"] is None
    bad = share["token"][:-2] + ("AA" if not share["token"].endswith("AA") else "BB")
    assert client.get(f"/city/share/{bad}/state").status_code == 404
    client.post(f"/tenants/{world.tenant_a}/city/shares/{share['id']}/revoke", headers=h)
    assert client.get(f"/city/share/{share['token']}/state").status_code == 404
    assert client.post(f"/tenants/{world.tenant_a}/city/shares",
                       headers=auth(make_token, world.carol),
                       json={"business_id": str(world.biz_a)}).status_code == 403
