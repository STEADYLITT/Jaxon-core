"""Phase 23, part 4: agent controls that really control the agent, the agent's monitor,
and Jaxon's routing view. Kill terminates a real child process before anything is
emitted (acceptance test 4)."""

import os
import threading
import time
import uuid

from conftest import auth, new_run, run_state
from sqlalchemy import select, text

from jackson_api import city
from jackson_api.models import Agent, AgentSpec, Event, RunEvent, Task
from jackson_api.policy import Action, Category
from jackson_api.runs import create_run
from jackson_api.worker import TaskResult, Worker


def agent_row(owner_db, world, key):
    owner_db.expire_all()
    return owner_db.scalar(select(Agent).where(Agent.tenant_id == world.tenant_a,
                                               Agent.key == key))


def ctl(client, world, h, agent_id, action, **body):
    return client.post(f"/tenants/{world.tenant_a}/city/agents/{agent_id}/{action}",
                       headers=h, json=body or None)


def wait_for(fn, timeout=20):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        v = fn()
        if v:
            return v
        time.sleep(0.1)
    raise AssertionError("timed out")


def test_kill_terminates_the_process_then_emits(client, world, make_token, maker, owner_db,
                                               drain):
    h = auth(make_token, world.alice)

    def slow_plan(ctx):  # a real long-running task in its own process
        ctx.log(f"pid {os.getpid()} working")
        for _ in range(600):
            time.sleep(0.05)
        return TaskResult({"finished": True})  # never reached: it gets killed

    w = Worker(maker, handlers={"plan": slow_plan}, retry_base_seconds=0, isolate=True)
    run_id = new_run(client, world, make_token, objective="Long job")
    t = threading.Thread(target=w.run_once)
    t.start()
    def first_log():
        owner_db.expire_all()
        return owner_db.scalar(select(Event).where(Event.run_id == uuid.UUID(run_id),
                                                   Event.type == "agent.log"))
    log = wait_for(first_log)
    child = int(log.payload["line"].split()[1])
    assert child != os.getpid()
    os.kill(child, 0)  # the child process is alive and working
    a = agent_row(owner_db, world, "kind:plan")
    assert a.status == "working"
    r = ctl(client, world, h, a.id, "kill").json()
    assert r == {"agent_id": str(a.id), "action": "kill", "status": "killed",
                 "pending_stop": True}
    # nothing is announced until the process is really gone
    assert owner_db.scalar(select(Event.id).where(Event.agent_id == a.id,
                                                  Event.type == "agent.killed")) is None
    t.join(timeout=15)
    assert not t.is_alive()
    try:
        os.kill(child, 0)
        alive = True
    except ProcessLookupError:
        alive = False
    assert not alive  # acceptance 4: the process actually stopped
    run, tasks = run_state(owner_db, run_id)
    assert run.status == "canceled" and tasks[0].status == "canceled"
    assert tasks[0].error == "killed by the owner"
    killed = owner_db.scalars(select(RunEvent).where(RunEvent.run_id == run.id,
                                                     RunEvent.type == "task.killed")).one()
    assert killed.data["pid"] == child
    ev = owner_db.scalars(select(Event).where(Event.agent_id == a.id,
                                              Event.type == "agent.killed")).one()
    assert ev.payload["stopped"] == "process terminated"
    s = client.get(f"/tenants/{world.tenant_a}/city/state", headers=h).json()
    me = next(x for x in s["agents"] if x["id"] == str(a.id))
    assert me["status"] == "killed" and me["online"] is False  # the window is gray


def test_a_killed_or_paused_agent_takes_no_new_work_until_resumed(client, world, make_token,
                                                                   maker, owner_db, drain):
    h = auth(make_token, world.alice)
    done = []

    def plan(ctx):
        done.append(ctx.task_id)
        return TaskResult({})

    w = Worker(maker, handlers={"plan": plan}, retry_base_seconds=0)
    w.city_heartbeat()
    a = agent_row(owner_db, world, "kind:plan")
    r = ctl(client, world, h, a.id, "kill").json()
    assert r["pending_stop"] is False  # nothing was running: the hold is the stop
    assert owner_db.scalar(select(Event.payload).where(
        Event.agent_id == a.id, Event.type == "agent.killed"))["stopped"] == \
        "no task was running"
    run_id = new_run(client, world, make_token)
    assert w.run_once() is False and done == []  # held
    assert ctl(client, world, h, a.id, "kill").status_code == 409
    ctl(client, world, h, a.id, "resume")
    assert agent_row(owner_db, world, "kind:plan").status == "idle"
    assert w.run_once() is True and len(done) == 1
    assert run_state(owner_db, run_id)[0].status == "succeeded"

    ctl(client, world, h, a.id, "pause")
    new_run(client, world, make_token)
    assert w.run_once() is False  # paused: no new work
    types = [e.type for e in owner_db.scalars(select(Event).where(Event.agent_id == a.id)
                                              .order_by(Event.id))]
    assert "agent.paused" in types and "agent.resumed" in types
    ctl(client, world, h, a.id, "resume")
    assert w.run_once() is True


def test_pause_also_pauses_the_running_work(client, world, make_token, maker, owner_db, drain):
    h = auth(make_token, world.alice)
    seen = {}

    def plan(ctx):
        a = owner_db.scalar(select(Agent.id).where(Agent.tenant_id == world.tenant_a,
                                                   Agent.key == "kind:plan"))
        ctl(client, world, h, a, "pause")
        seen["stop"] = ctx.should_stop()  # the handler's next check says stop
        return TaskResult({})

    run_id = new_run(client, world, make_token)
    Worker(maker, handlers={"plan": plan}, retry_base_seconds=0).run_once()
    assert seen["stop"] is True
    ev = owner_db.scalar(select(Event).where(Event.type == "agent.paused")
                         .order_by(Event.id.desc()))
    assert ev.payload["paused_run"] == run_id  # its run was paused, then the event


def test_approving_from_the_inbox_lets_the_waiting_agent_continue(client, world, make_token,
                                                                  maker, owner_db, drain):
    """Acceptance 5."""
    h = auth(make_token, world.alice)

    def plan(ctx):
        ctx.authorize(Action(Category.SPEND, "buy one sample", 900, always_ask=True))
        return TaskResult({"bought": True})

    w = Worker(maker, handlers={"plan": plan}, retry_base_seconds=0)
    w.city_heartbeat()
    run_id = new_run(client, world, make_token)
    w.run_once()
    s = client.get(f"/tenants/{world.tenant_a}/city/state", headers=h).json()
    ap = next(x for x in s["inbox"]["approvals"] if x["question"] == "buy one sample")
    assert agent_row(owner_db, world, "kind:plan").status == "waiting"
    client.post(f"/tenants/{world.tenant_a}/approvals/{ap['id']}/decision", headers=h,
                json={"decision": "approve"})
    w.run_once()
    run, tasks = run_state(owner_db, run_id)
    assert run.status == "succeeded" and tasks[0].output == {"bought": True}
    assert agent_row(owner_db, world, "kind:plan").status == "done"


def test_isolated_tasks_record_success_and_crashes(client, world, make_token, maker, owner_db,
                                                   drain):
    def ok(ctx):
        return TaskResult({"pid": os.getpid()})

    w = Worker(maker, handlers={"plan": ok}, retry_base_seconds=0, isolate=True)
    run_id = new_run(client, world, make_token)
    w.run_once()
    run, tasks = run_state(owner_db, run_id)
    assert run.status == "succeeded" and tasks[0].output["pid"] != os.getpid()

    def crash(ctx):
        os._exit(3)  # the process dies without reporting

    w = Worker(maker, handlers={"plan": crash}, retry_base_seconds=0, isolate=True)
    run_id = new_run(client, world, make_token)
    w.run_once()
    _, tasks = run_state(owner_db, run_id)
    assert "exited unexpectedly" in tasks[0].error


def test_agent_monitor_traces_back_to_steadys_command(client, world, make_token, maker,
                                                      owner_db, drain):
    h = auth(make_token, world.alice)
    seen = {}

    def plan(ctx):
        for i in range(10):
            ctx.log(f"line {i}")
        a = owner_db.scalar(select(Agent.id).where(Agent.tenant_id == world.tenant_a,
                                                   Agent.key == "kind:plan"))
        seen["d"] = client.get(f"/tenants/{world.tenant_a}/city/agents/{a}", headers=h).json()
        return TaskResult({})

    w = Worker(maker, handlers={"plan": plan}, retry_base_seconds=0)
    w.city_heartbeat()
    new_run(client, world, make_token, objective="Trace this command")
    w.run_once()
    d = seen["d"]
    assert d["status"] == "working" and d["time_on_task_s"] is not None
    assert d["trace"][0]["command"] == "Trace this command"
    assert d["trace"][1]["title"].startswith("Plan:")
    assert [e["payload"]["line"] for e in d["log"]] == [f"line {i}" for i in range(9, 1, -1)]
    assert d["controls"] == {"pause": True, "resume": False, "kill": True, "reassign": False}
    j = client.get(f"/tenants/{world.tenant_a}/city/jaxon", headers=h).json()
    cmd = next(c for c in j["commands"] if c["command"] == "Trace this command")
    assert cmd["routed_to"][0]["agent"] == "Jaxon Planner"


def test_reassign_moves_a_waiting_spec_task(client, world, make_token, owner_db, drain):
    h = auth(make_token, world.alice)
    specs = {}
    for name in ("copy-a", "copy-b"):
        s = AgentSpec(tenant_id=world.tenant_a, name=f"{name}-{uuid.uuid4().hex[:4]}",
                      version=1, role="marketing copywriter", objective="write",
                      instructions="be good", status="active", created_by="owner")
        owner_db.add(s)
        specs[name] = s
    owner_db.commit()
    uid = owner_db.scalar(text("SELECT id FROM users WHERE auth_subject=:s"),
                          {"s": world.alice})
    run, _ = create_run(owner_db, world.tenant_a, uid, {
        "type": "BUILD", "objective": "copy", "idempotency_key": uuid.uuid4().hex},
        first_task={"kind": "agent", "title": "write a tagline", "tool_scope": [],
                    "agent_spec_id": specs["copy-a"].id, "input": {"objective": "x"}})
    task = owner_db.scalar(select(Task).where(Task.run_id == run.id))
    task.status = "waiting_approval"
    a = city.agent_for_task(owner_db, task)
    a.current_task_id = task.id
    b = city.ensure_agent(owner_db, world.tenant_a, "agent", specs["copy-b"].name, "marketing")
    a_id, b_id, t_id = a.id, b.id, task.id
    owner_db.commit()
    r = ctl(client, world, h, a_id, "reassign", to_agent_id=str(b_id))
    assert r.status_code == 200, r.text
    owner_db.expire_all()
    assert owner_db.get(Task, t_id).agent_spec_id == specs["copy-b"].id
    client.get(f"/tenants/{world.tenant_a}/city/state", headers=h)  # registers all agents
    builtin = agent_row(owner_db, world, "kind:plan")
    assert ctl(client, world, h, builtin.id, "reassign").status_code == 409


def test_only_owner_or_admin_controls_agents(client, world, make_token, maker, owner_db):
    Worker(maker).city_heartbeat()
    a = agent_row(owner_db, world, "kind:plan")
    assert ctl(client, world, auth(make_token, world.carol), a.id, "pause").status_code == 403
    assert ctl(client, world, auth(make_token, world.alice), a.id, "explode").status_code == 404
