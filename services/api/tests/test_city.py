"""Phase 23 (ADR-037), part 1: the City's agent registry, emit(), heartbeats and the
wrapped runner. Every event here is written by real work; nothing is seeded."""

import uuid
from datetime import timedelta

import psycopg
import pytest
from conftest import auth, new_run, run_state
from sqlalchemy import create_engine, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from jackson_api import city
from jackson_api.models import Agent, Department, Event, Task
from jackson_api.policy import Action, Category
from jackson_api.worker import TaskResult, Worker


def city_events(owner_db, run_id=None, agent_id=None):
    owner_db.expire_all()
    q = select(Event).order_by(Event.id)
    if run_id:
        q = q.where(Event.run_id == uuid.UUID(str(run_id)))
    if agent_id:
        q = q.where(Event.agent_id == agent_id)
    return owner_db.scalars(q).all()


def agent(owner_db, world, key):
    owner_db.expire_all()
    return owner_db.scalar(select(Agent).where(Agent.tenant_id == world.tenant_a,
                                               Agent.key == key))


def test_a_real_command_writes_the_whole_trail(client, world, make_token, maker, owner_db,
                                               drain):
    run_id = new_run(client, world, make_token, objective="Find a phone grip to sell")
    Worker(maker, retry_base_seconds=0).run_once()  # the real planner, real (mock) model
    run, tasks = run_state(owner_db, run_id)
    assert run.status == "succeeded", tasks[0].error
    evs = city_events(owner_db, run_id)
    assert [e.type for e in evs] == ["command.issued", "task.created", "task.started",
                                     "tool.called", "task.completed"]
    issued, created, started, tool, done = evs
    assert (issued.from_kind, issued.to_kind) == ("ceo", "jaxon")
    assert issued.payload["text"] == "Find a phone grip to sell"
    assert (created.from_kind, created.to_kind) == ("jaxon", "jaxon")  # the planner is Jaxon
    assert tool.payload["tool"] == "model" and tool.payload["mode"] == "mock"
    assert {e.task_id for e in evs[1:]} == {tasks[0].id}
    a = agent(owner_db, world, "kind:plan")
    assert done.agent_id == a.id and a.status == "done" and a.current_task_id is None


def test_a_department_agent_gets_envelopes_both_ways(client, world, make_token, maker,
                                                     owner_db, drain):
    r = client.post(f"/tenants/{world.tenant_a}/content/generate",
                    headers=auth(make_token, world.alice),
                    json={"brief": "grip tips", "channel": "x", "count": 1,
                          "business_id": str(world.biz_a)})
    run_id = r.json()["run_id"]
    Worker(maker, retry_base_seconds=0).run_once()
    evs = {e.type: e for e in city_events(owner_db, run_id)}
    marketing = owner_db.scalar(select(Department.id).where(
        Department.tenant_id == world.tenant_a, Department.key == "marketing"))
    assert (evs["task.created"].from_kind, evs["task.created"].to_id) == ("jaxon",
                                                                         str(marketing))
    assert (evs["task.completed"].from_id, evs["task.completed"].to_kind) == (
        str(marketing), "jaxon")
    a = agent(owner_db, world, "kind:content_generate")
    assert a.name == "Content Writer" and a.department_id == marketing


def test_failures_and_approvals_set_real_statuses(client, world, make_token, maker, owner_db,
                                                  drain):
    def needs_ok(ctx):
        ctx.authorize(Action(Category.SPEND, "buy samples", 2_500, always_ask=True))
        return TaskResult({"ok": True})

    from jackson_api.worker_errors import NonRetryableError

    def boom_permanent(ctx):
        ctx.log("checking supplier list")
        raise NonRetryableError("supplier site down")

    alice = auth(make_token, world.alice)
    run_id = new_run(client, world, make_token)
    Worker(maker, handlers={"plan": needs_ok}, retry_base_seconds=0).run_once()
    a = agent(owner_db, world, "kind:plan")
    assert a.status == "waiting" and a.current_task_id is not None
    req = [e for e in city_events(owner_db, run_id) if e.type == "approval.requested"][0]
    assert (req.to_kind, req.payload["amount_cents"]) == ("ceo", 2_500)
    ap = req.payload["approval_id"]
    client.post(f"/tenants/{world.tenant_a}/approvals/{ap}/decision", headers=alice,
                json={"decision": "approve"})
    decided = [e for e in city_events(owner_db, run_id) if e.type == "approval.decided"][0]
    assert decided.from_kind == "ceo" and decided.payload["decision"] == "approved"
    Worker(maker, handlers={"plan": needs_ok}, retry_base_seconds=0).run_once()
    assert agent(owner_db, world, "kind:plan").status == "done"

    run2 = new_run(client, world, make_token)
    Worker(maker, handlers={"plan": boom_permanent}, retry_base_seconds=0).run_once()
    types = [e.type for e in city_events(owner_db, run2)]
    assert types[-2:] == ["agent.log", "task.failed"]
    a = agent(owner_db, world, "kind:plan")
    assert a.status == "failed"
    log = [e for e in city_events(owner_db, run2) if e.type == "agent.log"][0]
    assert log.payload["line"] == "checking supplier list"


def test_heartbeat_offline_and_back(client, world, make_token, maker, owner_db, drain):
    w = Worker(maker, retry_base_seconds=0)
    w.city_heartbeat()
    a = agent(owner_db, world, "kind:opportunity_research")
    assert a.last_heartbeat_at is not None
    # the worker stops: no heartbeats. 61s later the API's sweep turns everything gray.
    with Session(owner_db.get_bind()) as db:
        gone = city.sweep_offline(db, at=a.last_heartbeat_at + timedelta(seconds=61))
        db.commit()
    assert a.id in gone
    assert agent(owner_db, world, "kind:opportunity_research").status == "offline"
    off = city_events(owner_db, agent_id=a.id)[-1]
    assert off.type == "agent.offline"
    with Session(owner_db.get_bind()) as db:  # within 60s nothing changes
        assert city.sweep_offline(db, at=a.last_heartbeat_at + timedelta(seconds=30)) == []
    w.city_heartbeat()  # the worker is back
    assert agent(owner_db, world, "kind:opportunity_research").status == "idle"
    assert city_events(owner_db, agent_id=a.id)[-1].payload == {"back_online": True}


def test_running_agents_heartbeat_with_their_task(client, world, make_token, maker, owner_db,
                                                  drain):
    seen = {}

    def slow(ctx):
        seen["beats"] = w.city_heartbeat()  # what the 15s thread does, mid-task
        return TaskResult({})

    w = Worker(maker, handlers={"plan": slow}, retry_base_seconds=0)
    run_id = new_run(client, world, make_token)
    w.run_once()
    assert seen["beats"] == 1
    beat = [e for e in city_events(owner_db, run_id) if e.type == "agent.heartbeat"]
    assert len(beat) == 1 and beat[0].payload["task"].startswith("Plan:")


def test_only_real_agents_are_registered(client, world, maker, owner_db):
    Worker(maker).city_heartbeat()
    owner_db.expire_all()
    agents = owner_db.scalars(select(Agent).where(Agent.tenant_id == world.tenant_a)).all()
    assert {a.key for a in agents} >= {f"kind:{k}" for k in city.BUILT_IN}
    staffed = {a.department_id for a in agents}
    depts = {d.key: d.id for d in owner_db.scalars(select(Department).where(
        Department.tenant_id == world.tenant_a))}
    assert depts["fulfillment"] not in staffed and depts["support"] not in staffed
    assert depts["marketing"] in staffed


def test_worker_cannot_lift_an_owner_hold_and_events_are_append_only(world, maker, owner_db,
                                                                    database):
    Worker(maker).city_heartbeat()
    a = agent(owner_db, world, "kind:site_build")
    owner_db.execute(update(Agent).where(Agent.id == a.id).values(status="paused"))
    owner_db.commit()
    with maker() as db, pytest.raises(DBAPIError, match="agent_owner_hold"):
        db.execute(update(Agent).where(Agent.id == a.id).values(status="working"))
        db.commit()
    eng = create_engine(database["app_url"])
    with Session(eng) as db:
        db.execute(text("SELECT set_config('app.tenant_id', :t, false)"),
                   {"t": str(world.tenant_a)})
        with pytest.raises(DBAPIError, match="permission denied"):
            db.execute(text("UPDATE events SET type = 'fake'"))
        db.rollback()
        with pytest.raises(DBAPIError, match="permission denied"):
            db.execute(text("DELETE FROM events"))
    eng.dispose()
    with maker() as db, pytest.raises(DBAPIError, match="permission denied"):
        db.execute(text("UPDATE events SET type = 'fake'"))


def test_emit_announces_on_commit(world, owner_db, database):
    url = create_engine(database["owner_url"]).url.set(drivername="postgresql")
    with psycopg.connect(url.render_as_string(False), autocommit=True) as listen:
        listen.execute("LISTEN jackson_events")
        with Session(owner_db.get_bind()) as db:
            ev = city.emit(db, world.tenant_a, "agent.log", payload={"line": "hi"})
            db.commit()
            ev_id = ev.id
        got = next(listen.notifies(timeout=5))
        assert got.payload == f"{world.tenant_a}:{ev_id}"


def test_tasks_are_mapped_to_existing_tables_not_duplicated(client, world, make_token,
                                                            owner_db, drain):
    run_id = new_run(client, world, make_token)
    created = [e for e in city_events(owner_db, run_id) if e.type == "task.created"][0]
    assert owner_db.get(Task, created.task_id).run_id == uuid.UUID(run_id)


# --- Phase 2: snapshot, events query, live stream ----------------------------------------------

def test_snapshot_is_built_only_from_real_rows(client, world, make_token, maker, owner_db,
                                               drain):
    alice = auth(make_token, world.alice)
    w = Worker(maker, retry_base_seconds=0)
    w.city_heartbeat()
    run_id = new_run(client, world, make_token, objective="Snapshot me")
    w.run_once()
    s = client.get(f"/tenants/{world.tenant_a}/city/state", headers=alice).json()
    assert s["demo"] is False
    depts = {d["key"]: d for d in s["departments"]}
    assert depts["support"]["staffed"] is False and depts["marketing"]["staffed"] is True
    assert [f["name"] for f in depts["marketing"]["floors"]][:2] == ["Content", "Social"]
    planner = next(a for a in s["agents"] if a["key"] == "kind:plan")
    assert planner["id"] in s["jaxon"]["agents"]
    assert planner["status"] == "done" and planner["online"] is True
    assert planner["done_today"] >= 1 and planner["success_rate_7d"] is not None
    assert s["hud"]["revenue_today_usd"] is None  # no connected revenue source: hidden
    assert s["hud"]["agents_online"] == s["hud"]["agents_total"]
    assert s["ticker"][0]["id"] == s["last_event_id"]
    assert any(t["run_id"] == run_id and t["type"] == "task.completed" for t in s["ticker"])


def test_snapshot_shows_the_inbox_and_current_task_with_its_command(client, world, make_token,
                                                                    maker, owner_db, drain):
    seen = {}

    def waits(ctx):
        seen["state"] = client.get(f"/tenants/{world.tenant_a}/city/state",
                                   headers=auth(make_token, world.alice)).json()
        ctx.authorize(Action(Category.SPEND, "buy 3 samples", 4_200, always_ask=True))
        return TaskResult({})

    w = Worker(maker, handlers={"plan": waits}, retry_base_seconds=0)
    w.city_heartbeat()
    new_run(client, world, make_token, objective="Source samples")
    w.run_once()
    mid = next(a for a in seen["state"]["agents"] if a["key"] == "kind:plan")
    assert mid["status"] == "working"
    assert mid["current_task"]["command"] == "Source samples"  # trace back to Steady
    s = client.get(f"/tenants/{world.tenant_a}/city/state",
                   headers=auth(make_token, world.alice)).json()
    ap = [a for a in s["inbox"]["approvals"] if a["question"] == "buy 3 samples"]
    assert ap and ap[0]["amount_cents"] == 4_200 and ap[0]["agent_id"] == mid["id"]


def test_a_hard_refresh_rebuilds_the_same_state(client, world, make_token, maker, owner_db,
                                                drain):
    alice = auth(make_token, world.alice)
    Worker(maker).city_heartbeat()
    a = client.get(f"/tenants/{world.tenant_a}/city/state", headers=alice).json()
    b = client.get(f"/tenants/{world.tenant_a}/city/state", headers=alice).json()
    strip = lambda s: {k: v for k, v in s.items() if k != "generated_at"}  # noqa: E731
    assert strip(a) == strip(b)


def test_events_query_filters(client, world, make_token, maker, owner_db, drain):
    alice = auth(make_token, world.alice)
    run_id = new_run(client, world, make_token)
    Worker(maker, retry_base_seconds=0).run_once()
    evs = client.get(f"/tenants/{world.tenant_a}/city/events?run={run_id}",
                     headers=alice).json()
    task_id = evs[1]["task_id"]
    by_task = client.get(f"/tenants/{world.tenant_a}/city/events?task={task_id}",
                         headers=alice).json()
    assert {e["task_id"] for e in by_task} == {task_id}
    after = client.get(f"/tenants/{world.tenant_a}/city/events?since={evs[-2]['id']}"
                       f"&run={run_id}", headers=alice).json()
    assert [e["id"] for e in after] == [evs[-1]["id"]]
    agent_id = evs[2]["agent_id"]
    by_agent = client.get(f"/tenants/{world.tenant_a}/city/events?agent={agent_id}",
                          headers=alice).json()
    assert by_agent and {e["agent_id"] for e in by_agent} == {agent_id}


def test_stream_delivers_live_events_and_resumes(client, world, make_token, maker, owner_db,
                                                 drain):
    alice = auth(make_token, world.alice)
    run_id = new_run(client, world, make_token, objective="Stream me")
    Worker(maker, retry_base_seconds=0).run_once()
    first = client.get(f"/tenants/{world.tenant_a}/city/events?run={run_id}",
                       headers=alice).json()
    start = first[0]["id"] - 1
    with client.stream("GET", f"/tenants/{world.tenant_a}/city/stream?after={start}"
                              f"&timeout=1", headers=alice) as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        body = "".join(r.iter_text())
    ids = [int(line[4:]) for line in body.splitlines() if line.startswith("id: ")]
    assert ids[:len(first)] == [e["id"] for e in first]
    assert "event: command.issued" in body and "event: task.completed" in body
    with client.stream("GET", f"/tenants/{world.tenant_a}/city/stream?timeout=0.5",
                       headers={**alice, "Last-Event-ID": str(ids[-1])}) as r:
        rest = "".join(r.iter_text())
    assert f"id: {ids[0]}\n" not in rest  # resumed after the last id, no repeats


def test_stream_turns_a_dead_worker_gray_without_ui_action(client, world, make_token, maker,
                                                          owner_db, drain, monkeypatch):
    alice = auth(make_token, world.alice)
    Worker(maker).city_heartbeat()
    owner_db.execute(update(Agent).where(Agent.tenant_id == world.tenant_a)
                     .values(last_heartbeat_at=text("now() - interval '2 minutes'")))
    owner_db.commit()  # the worker died two minutes ago
    monkeypatch.setattr(city, "HEARTBEAT_S", 0)
    with client.stream("GET", f"/tenants/{world.tenant_a}/city/stream?timeout=1",
                       headers=alice) as r:
        body = "".join(r.iter_text())
    assert "event: agent.offline" in body
    owner_db.expire_all()
    statuses = {a.status for a in owner_db.scalars(select(Agent).where(
        Agent.tenant_id == world.tenant_a))}
    assert statuses == {"offline"}


def test_city_is_tenant_isolated(client, world, make_token):
    r = client.get(f"/tenants/{world.tenant_b}/city/state",
                   headers=auth(make_token, world.alice))
    assert r.status_code in (403, 404)
