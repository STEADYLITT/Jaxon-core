"""ADR-039: Jackson is the CEO of all of Steady's business lines. Every line is a City
building with real agents (or proposed ones), a playbook, real-data KPIs, connectors,
a launch template, and routing from the command bar."""

import uuid

from conftest import auth, run_state
from sqlalchemy import select, update

from jackson_api import city
from jackson_api.lines import BY_KEY, LINES, line_of_text
from jackson_api.models import AgentSpec, Business, Event
from jackson_api.seed import propose_line_agents
from jackson_api.worker import Worker

LINE_OF = {"stu": "STU", "clips": "Clips", "creators": "Creators", "ecommerce": "Shop",
           "influencers": "Influencers", "agency": "Agency"}


def setup_lines(owner_db, world):
    """Give the test tenant one business per line (as seeding does for the owner)."""
    ids = {}
    for key, name in LINE_OF.items():
        b = Business(tenant_id=world.tenant_a, name=f"{name} {uuid.uuid4().hex[:4]}",
                     kind="test", line=key)
        owner_db.add(b)
        owner_db.flush()
        ids[key] = b.id
    propose_line_agents(owner_db, world.tenant_a, None)
    owner_db.commit()
    return ids


def command(client, world, make_token, text):
    r = client.post(f"/tenants/{world.tenant_a}/commands", headers=auth(make_token, world.alice),
                    json={"type": "PLAN", "objective": text, "route": True,
                          "idempotency_key": uuid.uuid4().hex})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def routed(owner_db, run_id):
    owner_db.expire_all()
    return owner_db.scalars(select(Event).where(Event.run_id == uuid.UUID(run_id),
                                                Event.type == "command.routed")).one()


def test_eight_lines_are_the_departments_and_every_worker_has_a_line():
    assert [ln.key for ln in LINES] == ["rnd", "stu", "agency", "clips", "creators",
                                        "influencers", "ecommerce", "finance"]
    assert [d[0] for d in city.DEPARTMENTS] == [ln.key for ln in LINES]
    for kind, (dept, floor, _n, _r) in city.BUILT_IN.items():
        if dept != "jaxon":
            assert dept in BY_KEY and floor in {f for f, _ in BY_KEY[dept].floors}, kind
    assert line_of_text("find a new product idea under $30")[0].key == "ecommerce"
    assert line_of_text("make 10 posts for Replay Trainer")[0].key == "stu"
    assert line_of_text("find 3 new clip channels")[0].key == "clips"
    assert line_of_text("plan a week for creator X")[0].key == "creators"


def test_lines_api_shows_playbook_kpis_connectors_agents(client, world, make_token, maker,
                                                         owner_db, monkeypatch):
    for v in ("UPLOADPOST_API_KEY", "UPLOADPOST_USER", "FAL_KEY", "SHOPIFY_STORE_DOMAIN"):
        monkeypatch.delenv(v, raising=False)
    setup_lines(owner_db, world)
    Worker(maker).city_heartbeat()
    h = auth(make_token, world.alice)
    lines = {x["key"]: x for x in client.get(f"/tenants/{world.tenant_a}/lines",
                                              headers=h).json()}
    assert len(lines) == 8
    clips = lines["clips"]
    assert clips["staffed"] is False  # nobody works there until Steady approves an agent
    assert {p["name"] for p in clips["proposed_agents"]} >= {"clip-scout", "clip-captioner"}
    assert "YOUTUBE_API_KEY" in clips["missing_variables"]
    assert "UPLOADPOST_API_KEY" in clips["missing_variables"]
    assert {c["mode"] for c in clips["connectors"]} == {"MOCK"}
    views = next(k for k in clips["kpis"] if k["name"] == "Views")
    assert views["value"] is None and "needs" in views["note"]  # never estimated
    stu = lines["stu"]
    assert stu["staffed"] and any(a["name"] == "Content Writer" for a in stu["agents"])
    whop = next(k for k in stu["kpis"] if "Whop" in k["name"])
    assert whop["value"] is None and "WHOP_API_KEY" in stu["missing_variables"]
    assert stu["playbook"]["version"] == 1 and stu["playbook"]["rules"]
    assert lines["ecommerce"]["launch"]["template"]["channels"]["shopify_publish"] is True
    creators = lines["creators"]
    assert any("explicit" in r for r in creators["playbook"]["rules"])
    fin = {k["name"]: k for k in lines["finance"]["kpis"]}
    assert fin["Agents on staff"]["value"] >= 10


def test_command_bar_covers_every_line_with_a_reason(client, world, make_token, maker,
                                                     owner_db, drain, monkeypatch):
    for v in ("YOUTUBE_API_KEY", "UPLOADPOST_API_KEY", "SHOPIFY_STORE_DOMAIN"):
        monkeypatch.delenv(v, raising=False)
    ids = setup_lines(owner_db, world)
    w = Worker(maker, retry_base_seconds=0)

    # 1. clips: no active agent yet -> Jaxon plans; says who would take it and which keys
    r1 = command(client, world, make_token, "find 3 new clip channels")
    w.run_once()
    e = routed(owner_db, r1)
    assert e.payload["line"] == "clips" and e.payload["kind"] is None
    assert "clip-scout" in e.payload["reason"] and "YOUTUBE_API_KEY" in e.payload["reason"]
    clips_dept = city.ensure_departments(owner_db, world.tenant_a)["clips"].id
    assert (e.to_kind, e.to_id) == ("department", str(clips_dept))  # the letter goes there

    # 2. Replay Trainer posts -> Content Writer, 10 drafts, for the STU business
    r2 = command(client, world, make_token, "make 10 posts for Replay Trainer")
    w.run_once()
    e = routed(owner_db, r2)
    assert e.payload["line"] == "stu" and e.payload["agent"] == "Content Writer"
    w.run_once()
    run, tasks = run_state(owner_db, r2)
    assert run.business_id == ids["stu"] and tasks[1].input["count"] == 10
    assert tasks[1].status == "succeeded", tasks[1].error  # drafted for the STU business
    assert tasks[1].output["mode"] == "mock"  # no Claude key here: labeled, never passed off

    # 3. creator week -> Jaxon plans, creator-planner proposed
    r3 = command(client, world, make_token, "plan a week for creator X")
    w.run_once()
    e = routed(owner_db, r3)
    assert e.payload["line"] == "creators" and "creator-planner" in e.payload["reason"]

    # 4. product idea under $30 -> E-commerce; Research Scout needs sources
    r4 = command(client, world, make_token, "find a new product idea under $30")
    w.run_once()
    e = routed(owner_db, r4)
    assert e.payload["line"] == "ecommerce" and "product-researcher" in e.payload["reason"]
    assert "SHOPIFY_STORE_DOMAIN" in e.payload["reason"]


def test_explicit_requests_are_refused_before_any_planning(client, world, make_token, maker,
                                                           owner_db, drain):
    setup_lines(owner_db, world)
    run_id = command(client, world, make_token, "write explicit nude captions for creator X")
    Worker(maker, retry_base_seconds=0).run_once()
    e = routed(owner_db, run_id)
    assert e.payload["refused"] is True and "out of scope" in e.payload["reason"]
    owner_db.expire_all()
    assert owner_db.scalar(select(Event.id).where(Event.run_id == uuid.UUID(run_id),
                                                  Event.type == "tool.called")) is None
    assert [t.kind for t in run_state(owner_db, run_id)[1]] == ["plan"]


def test_an_approved_line_agent_gets_the_work_and_staffs_the_building(client, world,
                                                                      make_token, maker,
                                                                      owner_db, drain):
    setup_lines(owner_db, world)
    owner_db.execute(update(AgentSpec).where(AgentSpec.tenant_id == world.tenant_a,
                                             AgentSpec.name == "clip-scout")
                     .values(status="active"))  # what Steady's approval does
    owner_db.commit()
    run_id = command(client, world, make_token, "find 3 new clip channels for GTA 6")
    Worker(maker, retry_base_seconds=0).run_once()
    e = routed(owner_db, run_id)
    assert e.payload["agent"] == "clip-scout" and e.payload["kind"] == "agent"
    lines = {x["key"]: x for x in client.get(f"/tenants/{world.tenant_a}/lines",
                                              headers=auth(make_token, world.alice)).json()}
    assert lines["clips"]["staffed"] is True


def test_playbook_edits_are_versioned_and_owner_only(client, world, make_token, owner_db):
    h = auth(make_token, world.alice)
    r = client.patch(f"/tenants/{world.tenant_a}/lines/clips/playbook", headers=h,
                     json={"rules": ["Only creators in a clipping program."],
                           "budget_monthly_cents": 8_000})
    assert r.status_code == 200 and r.json()["version"] == 2
    assert client.patch(f"/tenants/{world.tenant_a}/lines/clips/playbook",
                        headers=auth(make_token, world.carol), json={}).status_code == 403
    b = Business(tenant_id=world.tenant_a, name="New client co", kind="agency")
    owner_db.add(b)
    owner_db.commit()
    r = client.patch(f"/tenants/{world.tenant_a}/businesses/{b.id}/line", headers=h,
                     json={"line": "agency"})
    assert r.json()["line"] == "agency"
    assert client.patch(f"/tenants/{world.tenant_a}/businesses/{b.id}/line", headers=h,
                        json={"line": "casino"}).status_code == 422
