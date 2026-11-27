"""Phase 23, part 5: a command typed in the City is planned, then routed by Jaxon to the
department agent that can do it, with the reason recorded (command.routed)."""

import uuid

from conftest import auth, run_state
from sqlalchemy import select

from jackson_api import router
from jackson_api.models import Department, Event
from jackson_api.worker import Worker


def command(client, world, make_token, text, route=True):
    r = client.post(f"/tenants/{world.tenant_a}/commands",
                    headers=auth(make_token, world.alice),
                    json={"type": "PLAN", "objective": text, "route": route,
                          "idempotency_key": uuid.uuid4().hex})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def trail(owner_db, run_id):
    owner_db.expire_all()
    return owner_db.scalars(select(Event).where(Event.run_id == uuid.UUID(run_id))
                            .order_by(Event.id)).all()


def test_rules_pick_the_agent_and_say_why():
    r = router.by_rules("Write an Instagram post about our magnetic grip")
    assert r["kind"] == "content_generate" and r["input"]["channel"] == "instagram"
    assert "'post'" in r["reason"] and "'instagram'" in r["reason"]
    assert router.by_rules("Send me a summary of today")["kind"] == "daily_report"
    assert router.by_rules("Research phone grips")  is None  # nothing to read: no URLs
    r = router.by_rules("Research https://example.com/grips for product ideas")
    assert r["kind"] == "opportunity_research"
    assert r["input"]["urls"] == ["https://example.com/grips"]


def test_typed_command_is_routed_to_marketing_and_a_real_agent_starts(client, world,
                                                                      make_token, maker,
                                                                      owner_db, drain):
    """Acceptance 2."""
    run_id = command(client, world, make_token, "Write an Instagram post about the grip")
    w = Worker(maker, retry_base_seconds=0)
    w.run_once()  # Jaxon plans, then routes
    evs = trail(owner_db, run_id)
    routed = next(e for e in evs if e.type == "command.routed")
    marketing = owner_db.scalar(select(Department.id).where(
        Department.tenant_id == world.tenant_a, Department.key == "marketing"))
    assert (routed.from_kind, routed.to_kind, routed.to_id) == ("jaxon", "department",
                                                                str(marketing))
    assert routed.payload["agent"] == "Content Writer" and routed.payload["via"] == "rules"
    assert "'post'" in routed.payload["reason"]
    w.run_once()  # the Content Writer really starts and finishes the routed task
    run, tasks = run_state(owner_db, run_id)
    assert [t.kind for t in tasks] == ["plan", "content_generate"]
    assert tasks[1].input["channel"] == "instagram" and run.status == "succeeded", tasks[1].error
    types = [e.type for e in trail(owner_db, run_id)]
    last_done = len(types) - 1 - types[::-1].index("task.completed")
    assert types.index("command.issued") < types.index("command.routed") < last_done
    started = [e for e in trail(owner_db, run_id) if e.type == "task.started"]
    assert len(started) == 2 and started[1].task_id == tasks[1].id


def test_no_fitting_agent_means_jaxon_keeps_it(client, world, make_token, maker, owner_db,
                                               drain):
    run_id = command(client, world, make_token, "Think about our pricing strategy")
    Worker(maker, retry_base_seconds=0).run_once()
    routed = next(e for e in trail(owner_db, run_id) if e.type == "command.routed")
    assert routed.to_kind == "jaxon" and routed.payload["agent"] == "Jaxon"
    assert routed.payload["reason"].startswith("[MOCK model]")  # labeled, never passed off
    run, tasks = run_state(owner_db, run_id)
    assert [t.kind for t in tasks] == ["plan"] and run.status == "succeeded"


def test_routing_only_when_asked(client, world, make_token, maker, owner_db, drain):
    run_id = command(client, world, make_token, "Write an Instagram post", route=False)
    Worker(maker, retry_base_seconds=0).run_once()
    assert not [e for e in trail(owner_db, run_id) if e.type == "command.routed"]
    assert [t.kind for t in run_state(owner_db, run_id)[1]] == ["plan"]


def test_jaxon_view_shows_the_reason(client, world, make_token, maker, owner_db, drain):
    command(client, world, make_token, "Send me a summary of this week")
    Worker(maker, retry_base_seconds=0).run_once()
    j = client.get(f"/tenants/{world.tenant_a}/city/jaxon",
                   headers=auth(make_token, world.alice)).json()
    c = next(x for x in j["commands"] if x["command"] == "Send me a summary of this week")
    assert c["routing_reasons"][0]["payload"]["agent"] == "Daily Reporter"
    assert [r["agent"] for r in c["routed_to"]] == ["Jaxon Planner", "Daily Reporter"]
