"""ADR-039: Jackson is the CEO of all of Steady's businesses: 15 departments. Every one is a City
building with real agents (or proposed ones), a playbook, real-data KPIs, connectors,
a launch template, and routing from the command bar."""

import uuid

from conftest import auth, run_state
from sqlalchemy import select, update

from jackson_api import city
from jackson_api.lines import BY_KEY, LINES, MAX_PROPOSED_AGENTS, line_of_text
from jackson_api.models import AgentSpec, Business, Event
from jackson_api.seed import propose_line_agents
from jackson_api.worker import Worker

LINE_OF = {"marketing": "STU", "courses": "AI Wealth Academy", "clips": "Clips",
           "creators": "Creators", "ecommerce": "Shop", "influencers": "Influencers",
           "agency": "Agency", "factory": "GOTTINK", "trading": "APEX-7"}


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


def test_fifteen_departments_and_every_worker_has_one():
    assert [ln.key for ln in LINES] == [
        "rnd", "courses", "marketing", "agency", "sales", "factory", "clips", "creators",
        "influencers", "ecommerce", "community", "trading", "realestate", "legal", "finance"]
    assert sorted(ln.slot for ln in LINES) == list(range(15))
    assert sum(len(ln.proposed) for ln in LINES) <= MAX_PROPOSED_AGENTS
    assert [d[0] for d in city.DEPARTMENTS] == [ln.key for ln in LINES]
    for kind, (dept, floor, _n, _r) in city.BUILT_IN.items():
        if dept != "jaxon":
            assert dept in BY_KEY and floor in {f for f, _ in BY_KEY[dept].floors}, kind
    assert line_of_text("find a new product idea under $30")[0].key == "ecommerce"
    assert line_of_text("make 10 posts for Replay Trainer")[0].key == "marketing"
    assert line_of_text("AI trading course is trending, build it")[0].key == "courses"
    assert line_of_text("build a landing page for GOTTINK")[0].key == "factory"
    assert line_of_text("backtest APEX-7 on MNQ last 30 days")[0].key == "trading"
    assert line_of_text("pull comps and underwrite 12 Oak St")[0].key == "realestate"
    assert line_of_text("draft an NDA for the Atom Labs partner")[0].key == "legal"
    assert line_of_text("sync Discord roles for new members")[0].key == "community"
    assert line_of_text("build a call list of insurance leads")[0].key == "sales"
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
    assert len(lines) == 15
    clips = lines["clips"]
    assert clips["staffed"] is True  # the built-in Clip Scout (ADR-052) works there
    assert lines["legal"]["staffed"] is False  # nobody until Steady approves an agent
    assert {p["name"] for p in clips["proposed_agents"]} >= {"clip-scout", "clip-captioner"}
    assert "YOUTUBE_API_KEY" in clips["missing_variables"]
    assert "UPLOADPOST_API_KEY" in clips["missing_variables"]
    assert {c["mode"] for c in clips["connectors"]} == {"MOCK"}
    assert "CLIP_PAYOUTS_SOURCE" in clips["missing_variables"]  # built (ADR-057), not set
    views = next(k for k in clips["kpis"] if k["name"] == "Views paid on this month")
    assert views["value"] is None and "needs" in views["note"]  # never estimated
    mk = lines["marketing"]
    assert mk["staffed"] and any(a["name"] == "Content Writer" for a in mk["agents"])
    courses = lines["courses"]
    whop = next(k for k in courses["kpis"] if "Whop" in k["name"])
    assert whop["value"] is None and "WHOP_API_KEY" in courses["missing_variables"]
    assert courses["playbook"]["version"] == 1 and courses["playbook"]["rules"]
    trading = {k["name"]: k for k in lines["trading"]["kpis"]}
    assert trading["Paper P&L, last month (Alpaca paper)"]["value"] is None
    assert trading["Live orders placed"]["value"] == 0
    assert any("never filed" in r.lower() for r in lines["legal"]["playbook"]["rules"])
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
    assert e.payload["line"] == "marketing" and e.payload["agent"] == "Content Writer"
    w.run_once()
    run, tasks = run_state(owner_db, r2)
    assert run.business_id == ids["marketing"] and tasks[1].input["count"] == 10
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


def test_full_scope_examples_route_with_reasons(client, world, make_token, maker, owner_db,
                                                drain, monkeypatch):
    for v in ("WHOP_API_KEY", "APEX7_RESULTS_SOURCE", "MARKET_DATA_API_KEY"):
        monkeypatch.delenv(v, raising=False)
    setup_lines(owner_db, world)
    w = Worker(maker, retry_base_seconds=0)
    cases = [("AI trading course is trending, build it", "courses", "Course Builder"),
             ("build a landing page for GOTTINK", "factory", "web-developer"),
             ("backtest APEX-7 on MNQ last 30 days", "trading", "backtest-runner"),
             ("draft an NDA for the Atom Labs partner", "legal", "contract-drafter")]
    for text, line, agent in cases:
        rid = command(client, world, make_token, text)
        for _ in range(3):  # the course build is queued too: run until this plan has routed
            w.run_once()
        e = routed(owner_db, rid)
        assert e.payload["line"] == line, (text, e.payload)
        assert agent in e.payload["reason"], (text, e.payload["reason"])
    legal = routed(owner_db, rid).payload["reason"]
    assert "never filed automatically" in legal


def test_live_trades_are_refused_before_planning(client, world, make_token, maker, owner_db,
                                                 drain):
    setup_lines(owner_db, world)
    rid = command(client, world, make_token, "go live with APEX-7 on my real account")
    Worker(maker, retry_base_seconds=0).run_once()
    e = routed(owner_db, rid)
    assert e.payload["refused"] is True and e.payload["line"] == "trading"
    assert "kill switch" in e.payload["reason"] and "loss caps" in e.payload["reason"]
    assert [t.kind for t in run_state(owner_db, rid)[1]] == ["plan"]
    owner_db.expire_all()
    assert owner_db.scalar(select(Event.id).where(Event.run_id == uuid.UUID(rid),
                                                  Event.type == "tool.called")) is None


def test_proposed_agents_are_capped(owner_db, world, monkeypatch):
    from jackson_api import lines

    monkeypatch.setattr(lines, "MAX_PROPOSED_AGENTS", 3)
    assert propose_line_agents(owner_db, world.tenant_a, None) == 3
    assert propose_line_agents(owner_db, world.tenant_a, None) == 0
    owner_db.rollback()
