"""Phase 21: Launch approvals (ADR-032). One owner approval per launch, then Jackson acts
alone inside its scope; everything outside still goes to the approvals inbox."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from conftest import auth, fast_worker, run_state
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError

from jackson_api import launches as L
from jackson_api.models import (
    Approval,
    ContentItem,
    Launch,
    LaunchSignal,
    LedgerEntry,
    WorkflowRun,
)
from jackson_api.policy import Action, Category
from jackson_api.runs import create_run
from jackson_api.worker import TaskResult, Worker


@pytest.fixture()
def alice(make_token, world):
    return auth(make_token, world.alice)


SCOPE = {"total_budget_cents": 50_000, "daily_cap_cents": 10_000, "duration_days": 30,
         "channels": {"shopify_publish": True, "social_accounts": ["instagram"],
                      "email_lists": ["vip"], "ads": False},
         "kill_rules": {"max_cac_cents": 4_000, "zero_sales_after_days": 7}}


def make_launch(client, world, alice, **over):
    body = {"title": "Mag Grip launch", "business_id": str(world.biz_a),
            "plan": "Draft store, publish, post daily, email VIP list", **SCOPE, **over}
    r = client.post(f"/tenants/{world.tenant_a}/launches", headers=alice, json=body)
    assert r.status_code == 201, r.text
    return r.json()


def uid(owner_db, world):
    return owner_db.scalar(text("SELECT id FROM users WHERE auth_subject = :s"),
                           {"s": world.alice})


def launch_run(owner_db, world, launch_id, kind="probe", input=None, business=None):
    run, _ = create_run(owner_db, world.tenant_a, uid(owner_db, world), {
        "type": "EXECUTE", "business_id": business or world.biz_a, "objective": "launch work",
        "idempotency_key": uuid.uuid4().hex},
        first_task={"kind": kind, "title": kind, "tool_scope": [], "input": input or {}},
        launch_id=uuid.UUID(launch_id) if launch_id else None)
    rid = str(run.id)
    owner_db.commit()
    return rid


def probe(maker, owner_db, rid, fn):
    out = {}

    def handler(ctx):
        out["r"] = fn(ctx)
        return TaskResult({"ok": True})
    fast_worker(maker, {"probe": handler}).run_once()
    return run_state(owner_db, rid)[0].status, out


# --- Approving a launch ---------------------------------------------------------------------------

def test_owner_approves_a_launch_once_with_its_scope(client, world, alice, owner_db):
    la = make_launch(client, world, alice)
    assert la["status"] == "active" and la["version"] == 1 and la["approved_by"]
    assert la["channels"] == {"shopify_publish": True, "social_accounts": ["instagram"],
                              "email_lists": ["vip"], "ads": False, "ad_platforms": []}
    ends = datetime.fromisoformat(la["ends_at"]) - datetime.fromisoformat(la["starts_at"])
    assert ends == timedelta(days=30)
    assert owner_db.scalar(text("SELECT count(*) FROM audit_events WHERE entity_id = :i AND "
                                "action = 'launch.approved'"), {"i": la["id"]}) == 1


def test_only_the_owner_can_approve_and_caps_must_make_sense(client, world, alice, make_token,
                                                            owner_db):
    carol = auth(make_token, world.carol)
    body = {"title": "x launch", "business_id": str(world.biz_a), **SCOPE}
    assert client.post(f"/tenants/{world.tenant_a}/launches", headers=carol,
                       json=body).status_code == 403
    cid = owner_db.scalar(text("SELECT id FROM users WHERE auth_subject = :s"),
                          {"s": world.carol})
    owner_db.execute(text("UPDATE memberships SET role='admin' WHERE user_id=:u"), {"u": cid})
    owner_db.commit()
    assert client.post(f"/tenants/{world.tenant_a}/launches", headers=carol,
                       json=body).status_code == 403  # admins can't, only the owner
    bad = {**body, "daily_cap_cents": 60_000}
    assert client.post(f"/tenants/{world.tenant_a}/launches", headers=alice,
                       json=bad).status_code == 422


# --- In scope runs alone; out of scope goes to the inbox ------------------------------------------

@pytest.mark.parametrize("action", [
    Action(Category.PRODUCTION_CHANGE, "publish shopify product 1", channel="shopify_publish"),
    Action(Category.PUBLIC_POST, "publish content 1", channel="social", target="instagram"),
    Action(Category.OUTBOUND_CAMPAIGN, "send campaign 1", channel="email", target="vip"),
    Action(Category.SANDBOX_CHANGE, "shopify draft"),
])
def test_in_scope_actions_run_without_asking(client, world, alice, maker, owner_db, drain,
                                             action):
    la = make_launch(client, world, alice)
    rid = launch_run(owner_db, world, la["id"])
    status, _ = probe(maker, owner_db, rid, lambda ctx: ctx.authorize(action))
    assert status == "succeeded"
    # the same action outside a launch still needs a person
    rid2 = launch_run(owner_db, world, None)
    assert probe(maker, owner_db, rid2, lambda ctx: ctx.authorize(action))[0] in (
        "waiting_approval", "succeeded")
    if action.category != Category.SANDBOX_CHANGE:
        assert run_state(owner_db, rid2)[0].status == "waiting_approval"


@pytest.mark.parametrize("action,why", [
    (Action(Category.PUBLIC_POST, "post", channel="social", target="tiktok"), "tiktok"),
    (Action(Category.OUTBOUND_CAMPAIGN, "send", channel="email", target="everyone"),
     "everyone"),
    (Action(Category.SPEND, "ads", 1_000, channel="ads"), "ad spend"),
    (Action(Category.PAYMENT, "pay supplier", 500), "never covered"),
    (Action(Category.ACCOUNT_CREATION, "open a new store"), "never covered"),
    (Action(Category.PRODUCTION_CHANGE, "launch website"), "production change"),
])
def test_outside_the_scope_goes_to_the_inbox(client, world, alice, maker, owner_db, drain,
                                             action, why):
    la = make_launch(client, world, alice)
    rid = launch_run(owner_db, world, la["id"])
    assert probe(maker, owner_db, rid, lambda ctx: ctx.authorize(action))[0] == \
        "waiting_approval"
    reason = owner_db.scalar(select(Approval.reason).where(Approval.run_id == uuid.UUID(rid)))
    assert "outside the launch" in reason and why in reason


def test_live_trades_are_refused_even_inside_a_launch(client, world, alice, maker, owner_db,
                                                      drain):
    la = make_launch(client, world, alice)
    rid = launch_run(owner_db, world, la["id"])
    status, _ = probe(maker, owner_db, rid, lambda ctx: ctx.authorize(
        Action(Category.LIVE_TRADE, "buy ES futures")))
    assert status == "failed"


def test_spend_runs_alone_within_caps_and_asks_beyond_them(client, world, alice, maker,
                                                           owner_db, drain):
    la = make_launch(client, world, alice)
    rid = launch_run(owner_db, world, la["id"])
    status, _ = probe(maker, owner_db, rid, lambda ctx: ctx.reserve_budget(4_000, "video"))
    assert status == "succeeded"  # within the $100/day cap, no approval
    rid2 = launch_run(owner_db, world, la["id"])
    status, _ = probe(maker, owner_db, rid2, lambda ctx: ctx.reserve_budget(7_000, "more"))
    assert status == "waiting_approval"  # 4k + 7k > 10k daily cap
    reason = owner_db.scalar(select(Approval.reason).where(Approval.run_id == uuid.UUID(rid2)))
    assert "daily cap" in reason


# --- Real handlers inside a launch ----------------------------------------------------------------

def test_reviewed_content_publishes_inside_the_launch_without_a_per_post_approval(
        client, world, alice, maker, owner_db, drain):
    la = make_launch(client, world, alice)
    c = ContentItem(tenant_id=world.tenant_a, business_id=world.biz_a,
                    launch_id=uuid.UUID(la["id"]), channel="instagram", kind="post",
                    body="Grip it. AI-generated character", status="in_review",
                    review={"passed": True, "problems": []})
    owner_db.add(c)
    owner_db.commit()
    rid = launch_run(owner_db, world, la["id"], kind="content_publish",
                     input={"content_id": str(c.id)})
    Worker(maker, retry_base_seconds=0).run_once()
    run, tasks = run_state(owner_db, rid)
    assert run.status == "succeeded", tasks[0].error
    owner_db.expire_all()
    got = owner_db.get(ContentItem, c.id)
    assert got.status == "published" and got.approved_by is None


def test_content_outside_the_launch_scope_is_refused_by_the_database(client, world, alice,
                                                                     maker, owner_db):
    la = make_launch(client, world, alice)
    for channel, review in (("tiktok", {"passed": True}), ("instagram", {"passed": False})):
        with maker() as db, pytest.raises(DBAPIError, match="ck_content_needs_approval"):
            c = ContentItem(tenant_id=world.tenant_a, business_id=world.biz_a,
                            launch_id=uuid.UUID(la["id"]), channel=channel, kind="post",
                            body="b", status="in_review", review=review)
            db.add(c)
            db.flush()
            db.execute(update(ContentItem).where(ContentItem.id == c.id)
                       .values(status="published"))
    # another business's launch doesn't cover this business's content
    with maker() as db, pytest.raises(DBAPIError, match="ck_content_needs_approval"):
        c = ContentItem(tenant_id=world.tenant_a, business_id=None,
                        launch_id=uuid.UUID(la["id"]), channel="instagram", kind="post",
                        body="b", status="in_review", review={"passed": True})
        db.add(c)
        db.flush()
        db.execute(update(ContentItem).where(ContentItem.id == c.id).values(status="published"))


def test_campaign_to_the_named_list_sends_inside_the_launch(client, world, alice, maker,
                                                            owner_db, drain, monkeypatch):
    monkeypatch.setenv("UNSUBSCRIBE_SECRET", "s" * 40)
    la = make_launch(client, world, alice)
    client.post(f"/tenants/{world.tenant_a}/contacts", headers=alice, json={
        "name": "Vip Buyer", "email": f"v{uuid.uuid4().hex[:6]}@x.example",
        "business_id": str(world.biz_a), "source_type": "existing_customer",
        "source_ref": "shopify customer", "lawful_basis": "existing_relationship"})
    addr = "Steady Ventures, 100 Main St, Springfield, IL 62701"
    k = client.post(f"/tenants/{world.tenant_a}/campaigns", headers=alice, json={
        "name": "VIP drop", "channel": "email", "sender_name": "Steady",
        "sender_email": "steady@example.com", "postal_address": addr,
        "subject_template": "New grip for {first_name}",
        "body_template": "Hi {first_name}\n\n{postal_address}\nUnsubscribe: {unsubscribe_url}",
        "audience": {"list": "vip"}, "business_id": str(world.biz_a),
        "launch_id": la["id"]}).json()
    client.post(f"/tenants/{world.tenant_a}/campaigns/{k['id']}/prepare", headers=alice,
                json={"idempotency_key": uuid.uuid4().hex})
    Worker(maker, retry_base_seconds=0).run_once()
    rid = launch_run(owner_db, world, la["id"], kind="outreach_send",
                     input={"campaign_id": k["id"]})
    Worker(maker, retry_base_seconds=0).run_once()
    run, tasks = run_state(owner_db, rid)
    assert run.status == "succeeded", tasks[0].error
    assert tasks[0].output["sent"] == 1
    got = client.get(f"/tenants/{world.tenant_a}/campaigns/{k['id']}", headers=alice).json()
    assert got["approved_by"] is None and got["status"] == "completed"


# --- Auto-pause and kill rules --------------------------------------------------------------------

def add_spend(owner_db, world, launch_id, cents, when=None):
    rid = launch_run(owner_db, world, launch_id)
    owner_db.execute(update(WorkflowRun).where(WorkflowRun.id == uuid.UUID(rid))
                     .values(paused=True))  # a ledger holder only; never claimed
    e = LedgerEntry(tenant_id=world.tenant_a, run_id=uuid.UUID(rid), kind="reserve",
                    amount_cents=cents, description="spend",
                    idempotency_key=uuid.uuid4().hex)
    if when:
        e.created_at = when
    owner_db.add(e)
    owner_db.commit()


def signal(owner_db, world, launch_id, kind, n=1, cents=0):
    owner_db.add(LaunchSignal(tenant_id=world.tenant_a, launch_id=uuid.UUID(launch_id),
                              kind=kind, quantity=n, amount_cents=cents, source="test"))
    owner_db.commit()


def status_after_sweep(maker, owner_db, launch_id):
    Worker(maker).sweep_launches()
    owner_db.expire_all()
    la = owner_db.get(Launch, uuid.UUID(launch_id))
    return la.status, la.status_reason


@pytest.mark.parametrize("setup,status,needle", [
    (lambda o, w, lid: add_spend(o, w, lid, 6_000), "paused", "last hour"),
    (lambda o, w, lid: (signal(o, w, lid, "send", 40), signal(o, w, lid, "bounce", 5)),
     "paused", "bounce"),
    (lambda o, w, lid: (signal(o, w, lid, "send", 200), signal(o, w, lid, "complaint", 2)),
     "paused", "complaint"),
    (lambda o, w, lid: (signal(o, w, lid, "sale", 10, 30_000), signal(o, w, lid, "refund", 3)),
     "paused", "refund"),
    (lambda o, w, lid: signal(o, w, lid, "platform_error", 6), "paused", "platform errors"),
    (lambda o, w, lid: (add_spend(o, w, lid, 9_000, datetime.now(UTC) - timedelta(hours=3)),
                        signal(o, w, lid, "sale", 1, 2_000)), "killed", "cost per sale"),
])
def test_anomalies_pause_and_kill_rules_stop(client, world, alice, maker, owner_db, setup,
                                             status, needle):
    # anomaly cases run without kill rules, so the anomaly is what stops them
    rules = SCOPE["kill_rules"] if status == "killed" else {}
    la = make_launch(client, world, alice, kill_rules=rules)
    setup(owner_db, world, la["id"])
    got, reason = status_after_sweep(maker, owner_db, la["id"])
    assert got == status and needle in reason
    assert owner_db.scalar(text("SELECT count(*) FROM audit_events WHERE entity_id = :i AND "
                                "action = :a"), {"i": la["id"], "a": f"launch.{status}"}) == 1


def test_zero_sales_rule_and_expiry(client, world, alice, maker, owner_db):
    la = make_launch(client, world, alice)
    owner_db.execute(update(Launch).where(Launch.id == uuid.UUID(la["id"])).values(
        starts_at=datetime.now(UTC) - timedelta(days=8)))
    owner_db.commit()
    assert status_after_sweep(maker, owner_db, la["id"]) == (
        "killed", "kill rule: no sales after 7 days")
    lb = make_launch(client, world, alice)
    owner_db.execute(update(Launch).where(Launch.id == uuid.UUID(lb["id"])).values(
        starts_at=datetime.now(UTC) - timedelta(days=2),
        ends_at=datetime.now(UTC) - timedelta(minutes=1)))
    owner_db.commit()
    assert status_after_sweep(maker, owner_db, lb["id"])[0] == "expired"


def test_a_paused_launch_holds_its_work_until_the_owner_resumes(client, world, alice, maker,
                                                                owner_db, drain):
    la = make_launch(client, world, alice)
    client.post(f"/tenants/{world.tenant_a}/launches/{la['id']}/pause", headers=alice)
    rid = launch_run(owner_db, world, la["id"])
    w = fast_worker(maker, {"probe": lambda ctx: TaskResult({"ok": True})})
    assert w.run_once() is False  # nothing claimed while paused
    r = client.post(f"/tenants/{world.tenant_a}/launches/{la['id']}/resume", headers=alice)
    assert r.json()["status"] == "active"
    assert w.run_once() is True and run_state(owner_db, rid)[0].status == "succeeded"


# --- The AI can't widen its own scope ----------------------------------------------------------

def test_the_worker_cannot_widen_or_restart_a_launch(client, world, alice, maker, owner_db):
    la = make_launch(client, world, alice)
    lid = uuid.UUID(la["id"])
    with maker() as db, pytest.raises(DBAPIError, match="permission denied"):
        db.execute(update(Launch).where(Launch.id == lid).values(total_budget_cents=10**8))
    with maker() as db, pytest.raises(DBAPIError, match="permission denied"):
        db.execute(update(Launch).where(Launch.id == lid).values(channels={"ads": True}))
    client.post(f"/tenants/{world.tenant_a}/launches/{la['id']}/pause", headers=alice)
    with maker() as db:  # a paused launch isn't updatable by the worker at all
        assert db.execute(update(Launch).where(Launch.id == lid)
                          .values(status="active")).rowcount == 0
    with maker() as db, pytest.raises(DBAPIError, match="row-level security"):
        db.add(Launch(tenant_id=world.tenant_a, business_id=world.biz_a, title="self",
                      status="active", approved_by=uid(owner_db, world),
                      starts_at=datetime.now(UTC), ends_at=datetime.now(UTC) + timedelta(days=1),
                      total_budget_cents=1, daily_cap_cents=1, channels={}, kill_rules={}))
        db.flush()
    rid = launch_run(owner_db, world, None)
    with maker() as db, pytest.raises(DBAPIError, match="run_launch_fixed"):
        db.execute(update(WorkflowRun).where(WorkflowRun.id == uuid.UUID(rid))
                   .values(launch_id=lid))


def test_jackson_can_propose_and_the_owner_approves_with_scope(client, world, alice, maker,
                                                              owner_db, drain):
    rid = launch_run(owner_db, world, None, kind="launch_propose",
                     input={"title": "Launch candle kit", "plan": "test on IG"})
    Worker(maker, retry_base_seconds=0).run_once()
    lid = run_state(owner_db, rid)[1][0].output["launch_id"]
    got = client.get(f"/tenants/{world.tenant_a}/launches/{lid}", headers=alice).json()
    assert got["status"] == "proposed" and got["proposed_by"] == "jackson"
    r = client.post(f"/tenants/{world.tenant_a}/launches/{lid}/approve", headers=alice,
                    json=SCOPE)
    assert r.json()["status"] == "active" and r.json()["total_budget_cents"] == 50_000


# --- Owner controls -----------------------------------------------------------------------------

def test_owner_edits_scope_with_a_new_version_and_full_audit(client, world, alice, owner_db):
    la = make_launch(client, world, alice)
    r = client.patch(f"/tenants/{world.tenant_a}/launches/{la['id']}", headers=alice,
                     json={**SCOPE, "total_budget_cents": 80_000,
                           "channels": {**SCOPE["channels"], "ads": True}})
    assert r.json()["version"] == 2 and r.json()["channels"]["ads"] is True
    row = owner_db.execute(text("SELECT data FROM audit_events WHERE entity_id = :i AND "
                                "action = 'launch.scope_changed'"), {"i": la["id"]}).scalar()
    assert row["before"]["total_budget_cents"] == 50_000
    assert row["after"]["total_budget_cents"] == 80_000


def test_revoke_ends_the_launch_and_cancels_its_runs(client, world, alice, owner_db, drain):
    la = make_launch(client, world, alice)
    rid = launch_run(owner_db, world, la["id"])
    r = client.post(f"/tenants/{world.tenant_a}/launches/{la['id']}/revoke", headers=alice)
    assert r.json()["status"] == "revoked"
    assert run_state(owner_db, rid)[0].status == "canceled"


def test_global_stop_pauses_launches(client, world, alice, owner_db):
    la = make_launch(client, world, alice)
    out = client.post(f"/tenants/{world.tenant_a}/stop", headers=alice,
                      json={"reason": "check"}).json()
    assert out["launches_paused"] == 1
    got = client.get(f"/tenants/{world.tenant_a}/launches/{la['id']}", headers=alice).json()
    assert got["status"] == "paused" and "global stop" in got["status_reason"]
    client.post(f"/tenants/{world.tenant_a}/resume", headers=alice)


def test_launches_show_on_the_dashboard_and_daily_report(client, world, alice):
    make_launch(client, world, alice)
    d = client.get(f"/tenants/{world.tenant_a}/dashboard", headers=alice).json()
    assert d["launches"][0]["title"] == "Mag Grip launch"
    rep = client.get(f"/tenants/{world.tenant_a}/reports/daily/preview", headers=alice).json()
    assert "## Launches" in rep["markdown"] and "Mag Grip launch" in rep["markdown"]


def test_launches_are_tenant_isolated(client, world, alice, make_token):
    la = make_launch(client, world, alice)
    bob = auth(make_token, world.bob)
    assert client.get(f"/tenants/{world.tenant_b}/launches/{la['id']}",
                      headers=bob).status_code == 404
    assert client.post(f"/tenants/{world.tenant_b}/launches/{la['id']}/revoke",
                       headers=bob).status_code == 404


def test_pure_verdict_ok_for_a_healthy_launch():
    la = Launch(status="active", total_budget_cents=100, daily_cap_cents=50, kill_rules={},
                starts_at=datetime.now(UTC), ends_at=datetime.now(UTC) + timedelta(days=1))
    m = {"spend_cents": 0, "spend_last_hour_cents": 0, "sales": 0, "refunds": 0, "sends": 0,
         "bounces": 0, "complaints": 0, "cac_cents": None, "errors_last_hour": 0,
         "days_running": 0}
    assert L.verdict(la, m) is None


def test_console_launch_button_approves_a_launch(client, world, alice, owner_db):
    import os

    from playwright.sync_api import sync_playwright

    opp = client.post(f"/tenants/{world.tenant_a}/opportunities", headers=alice, json={
        "title": f"Candle kit {uuid.uuid4().hex[:4]}", "business_id": str(world.biz_a),
        "signals": [{"type": "avg_price", "value": 30, "source_url": "https://x.example/a"}]})
    assert opp.status_code in (200, 201), opp.text
    token = alice["Authorization"].split()[1]

    def proxy(route, request):
        path = request.url.removeprefix("http://console.test")
        r = client.request(request.method, path, content=request.post_data_buffer,
                           headers={k: v for k, v in request.headers.items()
                                    if k.lower() in ("authorization", "content-type")})
        route.fulfill(status=r.status_code, body=r.content,
                      headers={"content-type": r.headers.get("content-type", "text/plain")})

    exe = os.environ.get("BROWSER_EXECUTABLE") or (
        "/opt/pw-browsers/chromium" if os.path.exists("/opt/pw-browsers/chromium") else None)
    with sync_playwright() as pw:
        b = pw.chromium.launch(executable_path=exe, args=["--no-sandbox"] if exe else [])
        page = b.new_page()
        page.route("**/*", proxy)
        page.goto("http://console.test/console")
        page.fill("#token", token)
        page.fill("#tenant", str(world.tenant_a))
        page.click("#go")
        page.wait_for_selector("#opps button.launch")
        page.click("#opps button.launch")
        page.fill("#lf-budget", "500")
        page.fill("#lf-cap", "100")
        page.check("#lf-shopify")
        page.fill("#lf-social", "instagram, tiktok")
        page.fill("#lf-email", "vip")
        page.fill("#lf-cac", "40")
        page.fill("#lf-zero", "7")
        page.click("#lf-go")
        page.wait_for_selector("#launches li:has-text('active')")
        b.close()
    la = client.get(f"/tenants/{world.tenant_a}/launches", headers=alice).json()[0]
    assert la["status"] == "active" and la["total_budget_cents"] == 50_000
    assert la["daily_cap_cents"] == 10_000 and la["opportunity_id"] == opp.json()["id"]
    assert la["channels"]["social_accounts"] == ["instagram", "tiktok"]
    assert la["kill_rules"] == {"max_cac_cents": 4000, "zero_sales_after_days": 7}


def test_scope_needs_an_active_launch_of_the_same_business(client, world, alice, owner_db):
    la = make_launch(client, world, alice)
    lid = uuid.UUID(la["id"])
    post = Action(Category.PUBLIC_POST, "post", channel="social", target="instagram")
    assert L.covers(owner_db, lid, world.biz_a, post)[0] is True
    ok, why = L.covers(owner_db, lid, uuid.uuid4(), post)  # another business's run
    assert ok is False and "business" in why
    client.post(f"/tenants/{world.tenant_a}/launches/{la['id']}/pause", headers=alice)
    owner_db.expire_all()
    ok, why = L.covers(owner_db, lid, world.biz_a, post)
    assert ok is False and "paused" in why
    owner_db.rollback()


def test_an_anomaly_stops_the_very_next_action(client, world, alice, maker, owner_db, drain):
    """The health check runs before each in-scope decision, not only in the periodic sweep."""
    la = make_launch(client, world, alice, kill_rules={})
    add_spend(owner_db, world, la["id"], 6_000)  # spike: over half the daily cap this hour
    rid = launch_run(owner_db, world, la["id"])
    status, _ = probe(maker, owner_db, rid, lambda ctx: ctx.authorize(
        Action(Category.PUBLIC_POST, "post", channel="social", target="instagram")))
    assert status == "waiting_approval"
    owner_db.expire_all()
    assert owner_db.get(Launch, uuid.UUID(la["id"])).status == "paused"
