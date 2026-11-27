"""Phase 19: owner dashboard, metrics, alerts, global stop and daily reports (spec vol. 04)."""

import json
import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from conftest import auth, new_run, run_state
from sqlalchemy import text, update
from sqlalchemy.exc import DBAPIError

from jackson_api.models import (
    Approval,
    Artifact,
    ContentItem,
    LedgerEntry,
    ModelUsage,
    OutreachCampaign,
    Task,
    WorkflowRun,
)
from jackson_api.worker import Worker


@pytest.fixture()
def alice(make_token, world):
    return auth(make_token, world.alice)


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("ARTIFACT_DIR", str(tmp_path))
    for v in ("S3_BUCKET", "SITE_DOMAIN_VERIFY", "SHOPIFY_ADMIN_API_TOKEN",
              "SHOPIFY_STORE_DOMAIN", "SHOPIFY_CLIENT_ID", "SHOPIFY_CLIENT_SECRET"):
        monkeypatch.delenv(v, raising=False)


def dash(client, world, alice):
    r = client.get(f"/tenants/{world.tenant_a}/dashboard", headers=alice)
    assert r.status_code == 200, r.text
    return r.json()


def test_empty_dashboard_is_honest(client, world, alice):
    d = dash(client, world, alice)
    assert d["stop"]["active"] is False
    assert d["metrics"]["verified_success_rate"] is None  # no data, not 0% or 100%
    assert d["metrics"]["conversion_rate"] is None and "conversion_rate" in \
        d["metrics"]["not_measured"]
    assert {c["id"]: c["mode"] for c in d["connectors"]} == {
        "models": "mock", "shopify": "mock", "social": "mock", "hosting": "mock",
        "dns": "mock", "outreach": "mock", "artifacts": "mock"}
    assert all(c["needs"] for c in d["connectors"])


def test_portfolio_keeps_actual_and_estimated_revenue_apart(client, world, alice, owner_db,
                                                           make_token):
    biz = str(world.biz_a)
    for kind, cents, ref in (("actual", 50_000, "order 1001"), ("refund", 5_000, "refund 1001"),
                             ("estimate", 900_000, "economics model, 300 orders")):
        r = client.post(f"/tenants/{world.tenant_a}/revenue", headers=alice, json={
            "kind": kind, "amount_cents": cents, "business_id": biz, "reference": ref})
        assert r.status_code == 201, r.text
    run_id = new_run(client, world, make_token)
    rid = uuid.UUID(run_id)
    owner_db.execute(update(WorkflowRun).where(WorkflowRun.id == rid)
                     .values(business_id=world.biz_a))
    res = LedgerEntry(tenant_id=world.tenant_a, run_id=rid, kind="reserve", amount_cents=3000,
                      description="ads", idempotency_key=f"r-{rid}")
    owner_db.add(res)
    owner_db.flush()
    owner_db.add_all([
        LedgerEntry(tenant_id=world.tenant_a, run_id=rid, reservation_id=res.id, kind="commit",
                    amount_cents=2000, description="ads", idempotency_key=f"c-{rid}"),
        LedgerEntry(tenant_id=world.tenant_a, run_id=rid, kind="reserve", amount_cents=700,
                    description="held", idempotency_key=f"h-{rid}"),
        ModelUsage(tenant_id=world.tenant_a, run_id=rid, provider="anthropic", model="m",
                   mode="live", profile="standard", cost_usd_micros=1_230_000)])
    owner_db.commit()
    p = dash(client, world, alice)["portfolio"]
    [b] = [b for b in p["businesses"] if b["business_id"] == biz]
    assert (b["actual_revenue_cents"], b["refunds_cents"], b["spend_cents"],
            b["model_cost_cents"]) == (50_000, 5_000, 2_000, 123)
    assert b["held_cents"] == 1_700  # 3000 + 700 reserved - 2000 committed
    assert b["actual_profit_cents"] == 50_000 - 5_000 - 2_000 - 123
    assert b["estimated_revenue_cents"] == 900_000  # shown, never added to profit


def test_revenue_entries_are_checked_and_append_only(client, world, alice, app_session):
    url = f"/tenants/{world.tenant_a}/revenue"
    assert client.post(url, headers=alice, json={"kind": "actual", "amount_cents": 100}) \
        .status_code == 422  # actual revenue needs a reference
    assert client.post(url, headers=alice, json={"kind": "actual", "amount_cents": -1,
                                                 "reference": "x123"}).status_code == 422
    with pytest.raises(DBAPIError, match="permission denied"):
        app_session.execute(text("UPDATE revenue_entries SET amount_cents = 0"))
    app_session.rollback()
    with pytest.raises(DBAPIError, match="permission denied"):
        app_session.execute(text("DELETE FROM revenue_entries"))
    app_session.rollback()


def test_metrics_and_alerts(client, world, alice, owner_db, make_token):
    ok, bad = (uuid.UUID(new_run(client, world, make_token)) for _ in range(2))
    now = datetime.now(UTC)
    owner_db.execute(update(WorkflowRun).where(WorkflowRun.id == ok).values(
        status="succeeded", started_at=now - timedelta(seconds=30), finished_at=now))
    owner_db.execute(update(WorkflowRun).where(WorkflowRun.id == bad).values(
        status="failed", started_at=now - timedelta(seconds=10), finished_at=now))
    for i in range(3):
        owner_db.add(Task(tenant_id=world.tenant_a, run_id=bad, sequence=10 + i,
                          kind="opportunity_research", title="t", status="failed", error="x"))
    t = owner_db.query(Task).filter(Task.run_id == ok).first()
    owner_db.add(Approval(tenant_id=world.tenant_a, run_id=ok, task_id=t.id, category="spend",
                          description="old ask", amount_cents=500, fingerprint="f" * 64,
                          reason="r", status="pending",
                          created_at=now - timedelta(hours=30)))
    owner_db.commit()
    d = dash(client, world, alice)
    m = d["metrics"]
    assert m["verified_success_rate"] == 0.5 and m["failure_rate"] == 0.5
    assert m["avg_latency_seconds"] == 20.0
    kinds = {a["kind"] for a in d["alerts"]}
    assert {"repeated_failures", "stale_approvals"} <= kinds
    assert d["approvals"]["pending"] == 1 and d["approvals"]["items"][0]["amount_cents"] == 500
    assert [r["status"] for r in d["runs"][:2]] in (["failed", "succeeded"],
                                                    ["succeeded", "failed"])


# --- Global stop --------------------------------------------------------------------------------

def test_global_stop_halts_everything_and_reports_what_to_reconcile(
        client, world, alice, owner_db, maker, make_token, drain):
    queued = new_run(client, world, make_token)
    # an approved, scheduled post and an approved campaign waiting to send
    uid = owner_db.scalar(text("SELECT id FROM users WHERE auth_subject = :s"),
                          {"s": world.alice})
    c = ContentItem(tenant_id=world.tenant_a, channel="x", kind="post", body="b",
                    status="scheduled", review={}, approved_by=uid,
                    scheduled_for=datetime.now(UTC) + timedelta(hours=1))
    k = OutreachCampaign(tenant_id=world.tenant_a, name="k", channel="email", sender_name="s",
                         body_template="b" * 10, status="approved", approved_by=uid)
    owner_db.add_all([c, k])
    owner_db.commit()

    r = client.post(f"/tenants/{world.tenant_a}/stop", headers=alice,
                    json={"reason": "something looks wrong"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert queued in out["runs_canceled"]
    assert out["content_unscheduled"] == 1 and out["campaigns_cancelled"] == 1
    assert "reconcile" in out and "products_live" in out["reconcile"]
    assert run_state(owner_db, queued)[0].status == "canceled"
    owner_db.expire_all()
    assert owner_db.get(ContentItem, c.id).status == "approved"
    assert owner_db.get(OutreachCampaign, k.id).status == "cancelled"

    # nothing new can start
    r = client.post(f"/tenants/{world.tenant_a}/commands", headers=alice, json={
        "type": "PLAN", "objective": "anything", "idempotency_key": uuid.uuid4().hex})
    assert r.status_code == 423 and "global stop" in r.text
    d = dash(client, world, alice)
    assert d["stop"]["active"] and d["alerts"][0]["kind"] == "global_stop"

    # ...even bypassing the API: the database refuses new runs and task starts
    with pytest.raises(DBAPIError, match="tenant_stopped"):
        owner_db.add(WorkflowRun(tenant_id=world.tenant_a, requested_by=uid,
                                 command_type="PLAN", objective="sneaky",
                                 idempotency_key=uuid.uuid4().hex, request_hash="x" * 64))
        owner_db.flush()
    owner_db.rollback()
    t = owner_db.query(Task).filter(Task.run_id == uuid.UUID(queued)).first()
    with maker() as db, pytest.raises(DBAPIError, match="tenant_stopped"):
        db.execute(update(Task).where(Task.id == t.id).values(status="running"))

    # resume: new work is allowed again; canceled runs stay canceled
    assert client.post(f"/tenants/{world.tenant_a}/resume", headers=alice).json() == \
        {"stopped": False}
    assert run_state(owner_db, queued)[0].status == "canceled"
    new_run(client, world, make_token)
    n = owner_db.scalar(text("SELECT count(*) FROM audit_events WHERE tenant_id = :t AND "
                             "action IN ('tenant.global_stop','tenant.resumed')"),
                        {"t": world.tenant_a})
    assert n == 2


def test_worker_skips_stopped_tenants(client, world, alice, owner_db, maker, make_token, drain):
    run_id = new_run(client, world, make_token)
    owner_db.execute(text("UPDATE tenants SET stopped_at = now() WHERE id = :t"),
                     {"t": world.tenant_a})
    owner_db.commit()
    assert Worker(maker, retry_base_seconds=0).run_once() is False
    assert run_state(owner_db, run_id)[0].status == "queued"
    owner_db.execute(text("UPDATE tenants SET stopped_at = NULL WHERE id = :t"),
                     {"t": world.tenant_a})
    owner_db.commit()


def test_only_the_owner_can_stop(client, world, make_token, owner_db):
    carol = auth(make_token, world.carol)  # viewer
    assert client.post(f"/tenants/{world.tenant_a}/stop", headers=carol,
                       json={"reason": "nope"}).status_code == 403
    uid = owner_db.scalar(text("SELECT id FROM users WHERE auth_subject = :s"),
                          {"s": world.carol})
    owner_db.execute(text("UPDATE memberships SET role = 'admin' WHERE user_id = :u"),
                     {"u": uid})
    owner_db.commit()
    assert client.post(f"/tenants/{world.tenant_a}/stop", headers=carol,
                       json={"reason": "nope"}).status_code == 403


# --- Daily report -------------------------------------------------------------------------------

def test_daily_report_is_stored_as_an_artifact(client, world, alice, maker, owner_db, drain,
                                               make_token):
    done = uuid.UUID(new_run(client, world, make_token, objective="Research candle niches"))
    owner_db.execute(update(WorkflowRun).where(WorkflowRun.id == done).values(
        status="succeeded", started_at=datetime.now(UTC), finished_at=datetime.now(UTC)))
    owner_db.commit()
    r = client.post(f"/tenants/{world.tenant_a}/reports/daily", headers=alice,
                    json={"idempotency_key": uuid.uuid4().hex})
    w = Worker(maker, retry_base_seconds=0)
    while run_state(owner_db, r.json()["run_id"])[0].status in ("queued", "running"):
        assert w.run_once()
    run, tasks = run_state(owner_db, r.json()["run_id"])
    assert run.status == "succeeded", [t.error for t in tasks]
    art_id = [t.output for t in tasks if t.kind == "daily_report"][0]["artifact_id"]
    body = json.loads(client.get(f"/tenants/{world.tenant_a}/artifacts/{art_id}",
                                 headers=alice).content)
    assert "Research candle niches (succeeded)" in body["outcomes"]
    md = body["markdown"]
    for heading in ("## Outcomes", "## Blockers", "## Next actions", "## Numbers"):
        assert heading in md
    assert "Estimated revenue (not actual)" in md
    listed = client.get(f"/tenants/{world.tenant_a}/reports", headers=alice).json()
    assert listed[0]["type"] == "daily_report" and listed[0]["id"] == art_id
    assert owner_db.get(Artifact, uuid.UUID(art_id)).kind == "report"


def test_dashboard_is_tenant_isolated(client, world, make_token):
    bob = auth(make_token, world.bob)
    for path in ("dashboard", "revenue", "reports"):
        assert client.get(f"/tenants/{world.tenant_a}/{path}", headers=bob).status_code == 403
    assert client.post(f"/tenants/{world.tenant_a}/stop", headers=bob,
                       json={"reason": "attack"}).status_code == 403


# --- Console in a real browser ------------------------------------------------------------------

def test_console_shows_the_dashboard_and_global_stop_works(client, world, alice, owner_db,
                                                           make_token, drain, monkeypatch):
    from playwright.sync_api import sync_playwright

    new_run(client, world, make_token, objective='<img src=x onerror="window.pwned=1"> launch')
    page_resp = client.get("/console")
    assert "script-src 'self'" in page_resp.headers["content-security-policy"]
    token = alice["Authorization"].split()[1]

    def proxy(route, request):  # the browser talks to the real API through TestClient
        path = request.url.removeprefix("http://console.test")
        r = client.request(request.method, path, content=request.post_data_buffer,
                           headers={k: v for k, v in request.headers.items()
                                    if k.lower() in ("authorization", "content-type")})
        route.fulfill(status=r.status_code, body=r.content,
                      headers={"content-type": r.headers.get("content-type", "text/plain"),
                               **({"content-security-policy": r.headers[
                                   "content-security-policy"]}
                                  if "content-security-policy" in r.headers else {})})

    exe = os.environ.get("BROWSER_EXECUTABLE") or (
        "/opt/pw-browsers/chromium" if os.path.exists("/opt/pw-browsers/chromium") else None)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=exe, args=["--no-sandbox"] if exe else [])
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.route("**/*", proxy)
        page.goto("http://console.test/console")
        page.fill("#token", token)
        page.fill("#tenant", str(world.tenant_a))
        page.click("#go")
        page.wait_for_selector("#dash:not([hidden])")
        assert page.text_content("#banner") == "Running normally."
        assert "launch" in page.text_content("#runs")
        assert '<img src=x onerror="window.pwned=1">' in page.text_content("#runs")
        assert page.evaluate("window.pwned") is None  # shown as text, never executed
        assert "MOCK" in page.text_content("#connectors")
        page.once("dialog", lambda d: d.accept("testing the stop button"))
        page.on("dialog", lambda d: d.accept())
        page.click("#stop")
        page.wait_for_selector("#banner.critical")
        assert page.is_visible("#resume") and not page.is_visible("#stop")
        page.click("#resume")
        page.wait_for_selector("#resume", state="hidden")
        assert page.text_content("#banner") == "Running normally."
        assert errors == []
        browser.close()
    reason = owner_db.scalar(text("SELECT data->>'reason' FROM audit_events WHERE tenant_id = :t"
                                  " AND action = 'tenant.global_stop'"), {"t": world.tenant_a})
    assert reason == "testing the stop button"


def test_console_assets_are_locked_down(client):
    assert client.get("/console/app.js").status_code == 200
    assert client.get("/console/..%2Fmain.py").status_code == 404
    assert client.get("/console/nope.js").status_code == 404


def test_console_asset_names_cannot_escape_the_folder():
    from fastapi import HTTPException

    from jackson_api.routes_dashboard import console_asset

    for name in ("../main.py", "../../pyproject.toml", "index.html", "APP.JS"):
        with pytest.raises(HTTPException):
            console_asset(name)
    assert console_asset("app.css").status_code == 200
