"""Phase 22: command-center console. Real Chromium against the real API (through the
TestClient), real records only."""

import os
import uuid
from contextlib import contextmanager

from conftest import auth, new_run
from sqlalchemy import text

from jackson_api.models import AgentSpec, Approval, Task

EXE = os.environ.get("BROWSER_EXECUTABLE") or (
    "/opt/pw-browsers/chromium" if os.path.exists("/opt/pw-browsers/chromium") else None)


@contextmanager
def console(client, world, token, viewport=None, reduced_motion=None):
    from playwright.sync_api import sync_playwright

    def proxy(route, request):
        path = request.url.removeprefix("http://console.test")
        r = client.request(request.method, path, content=request.post_data_buffer,
                           headers={k: v for k, v in request.headers.items()
                                    if k.lower() in ("authorization", "content-type")})
        headers = {"content-type": r.headers.get("content-type", "text/plain")}
        if "content-security-policy" in r.headers:
            headers["content-security-policy"] = r.headers["content-security-policy"]
        route.fulfill(status=r.status_code, body=r.content, headers=headers)

    with sync_playwright() as pw:
        b = pw.chromium.launch(executable_path=EXE, args=["--no-sandbox"] if EXE else [])
        ctx = b.new_context(viewport=viewport or {"width": 1440, "height": 900},
                            reduced_motion=reduced_motion or "no-preference")
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.route("**/*", proxy)
        page.goto("http://console.test/console")
        page.fill("#token", token)
        page.fill("#tenant", str(world.tenant_a))
        page.click("#go")
        page.wait_for_selector("#dash:not([hidden])")
        try:
            yield page, errors
        finally:
            b.close()


def webgl(page) -> bool:
    return page.evaluate("!!document.createElement('canvas').getContext('webgl2')")


def test_greeting_counts_only_real_things_that_need_the_owner(client, world, make_token,
                                                              owner_db):
    tok = make_token(world.alice)
    with console(client, world, tok) as (page, errors):
        assert "nothing needs you right now" in page.text_content("#greeting")
    run_id = new_run(client, world, make_token)
    task = owner_db.query(Task).filter(Task.run_id == uuid.UUID(run_id)).first()
    owner_db.add(Approval(tenant_id=world.tenant_a, run_id=task.run_id, task_id=task.id,
                          category="spend", description="buy samples", amount_cents=2500,
                          fingerprint="a" * 64, reason="r"))
    owner_db.commit()
    alice = auth(make_token, world.alice)
    client.post(f"/tenants/{world.tenant_a}/launches", headers=alice, json={
        "title": "Real launch", "business_id": str(world.biz_a), "total_budget_cents": 1000,
        "daily_cap_cents": 100, "duration_days": 14})
    with console(client, world, tok) as (page, errors):
        g = page.text_content("#greeting")
        assert ("Good morning" in g or "Good afternoon" in g or "Good evening" in g)
        assert "1 thing needs you today" in g  # the pending approval (launch is approved)
        assert errors == []


def test_desktop_shows_the_3d_core_and_agents_with_details(client, world, make_token,
                                                           owner_db):
    spec = AgentSpec(tenant_id=world.tenant_a, name="scout", version=1, role="research",
                     objective="find products", instructions="be careful", status="active",
                     created_by="owner")
    owner_db.add(spec)
    owner_db.commit()
    run_id = new_run(client, world, make_token)
    owner_db.execute(text("UPDATE tasks SET agent_spec_id = :a, title = 'Research grips', "
                          "status = 'running' WHERE run_id = :r"),
                     {"a": spec.id, "r": run_id})
    owner_db.commit()
    with console(client, world, make_token(world.alice)) as (page, errors):
        page.wait_for_function("document.body.dataset.mode !== undefined")
        page.wait_for_timeout(800)
        mode = page.evaluate("document.body.dataset.mode")
        assert mode == ("3d" if webgl(page) else "2d")
        if mode == "3d":
            assert page.is_visible("#core3d")
            page.wait_for_selector("#corelabels .node-label:has-text('scout')")
        assert "Thinking" in page.text_content("#corestatus")  # a run is in progress
        page.click("#agents button[data-agent]")
        assert page.is_visible("#agentdetail")
        assert page.text_content("#ad-name") == "scout v1"
        assert page.text_content("#ad-task") == "Research grips"
        assert page.text_content("#ad-state") == "running"
        assert page.text_content("#ad-cost") == "$0.0000"
        assert errors == []


def test_phone_gets_the_fast_2d_layout_without_sideways_scrolling(client, world, make_token):
    with console(client, world, make_token(world.alice),
                 viewport={"width": 375, "height": 740}) as (page, errors):
        page.wait_for_timeout(500)
        assert page.evaluate("document.body.dataset.mode") == "2d"
        assert not page.is_visible("#core3d")
        assert page.is_visible(".core-2d .nucleus")
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
        for sel in ("#greeting", "#approvals", "#launches", "#feed", "#biz"):
            assert page.is_visible(sel), sel
        assert errors == []


def test_reduced_motion_also_gets_2d(client, world, make_token):
    with console(client, world, make_token(world.alice),
                 reduced_motion="reduce") as (page, errors):
        page.wait_for_timeout(400)
        assert page.evaluate("document.body.dataset.mode") == "2d"


def test_live_feed_streams_real_events(client, world, make_token):
    with console(client, world, make_token(world.alice)) as (page, errors):
        page.wait_for_selector("#feedstate.live")
        new_run(client, world, make_token, objective="Feed me")
        page.wait_for_selector("#feed li:has-text('run.created')", timeout=15000)
        assert errors == []


def test_business_cards_show_real_numbers_and_empty_states(client, world, make_token):
    alice = auth(make_token, world.alice)
    client.post(f"/tenants/{world.tenant_a}/revenue", headers=alice, json={
        "kind": "actual", "amount_cents": 12_345, "business_id": str(world.biz_a),
        "reference": "order 77"})
    with console(client, world, make_token(world.alice)) as (page, errors):
        cards = page.text_content("#biz")
        assert "$123.45" in cards and "Profit" in cards
        assert "No agents yet" in page.text_content("#agents")
        assert "No launches yet" in page.text_content("#launches")


def test_text_contrast_is_readable(client, world, make_token):
    with console(client, world, make_token(world.alice)) as (page, errors):
        ratio = page.evaluate("""() => {
          const lum = (c) => { const [r, g, b] = c.match(/\\d+/g).slice(0, 3).map((v) => {
            v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; });
            return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
          const fg = lum(getComputedStyle(document.body).color);
          const bg = lum(getComputedStyle(document.documentElement).backgroundColor);
          return (Math.max(fg, bg) + 0.05) / (Math.min(fg, bg) + 0.05); }""")
        assert ratio >= 7  # WCAG AAA for body text


def test_vendor_files_are_allow_listed(client):
    assert client.get("/console/vendor/three.module.min.js").status_code == 200
    assert client.get("/console/vendor/three.core.min.js").status_code == 200
    assert client.get("/console/vendor/THREE-LICENSE.txt").status_code == 404
    assert client.get("/console/vendor/..%2F..%2Fmain.py").status_code == 404
