"""Phase 23, part 3: the City exterior in real Chromium against the real API. Everything
the page shows is checked against database rows."""

import os
import re
from contextlib import contextmanager
from pathlib import Path

import pytest
from conftest import auth, new_run
from sqlalchemy import select, text, update

from jackson_api.models import Agent
from jackson_api.worker import Worker

EXE = os.environ.get("BROWSER_EXECUTABLE") or (
    "/opt/pw-browsers/chromium" if os.path.exists("/opt/pw-browsers/chromium") else None)
DIST = Path(__file__).parents[1] / "jackson_api" / "city_dist" / "assets"


@contextmanager
def city(client, world, token, viewport=None, device=None, reduced_motion=None, share=None,
         query=""):
    from playwright.sync_api import sync_playwright

    def proxy(route, request):
        path = request.url.removeprefix("http://city.test")
        r = client.request(request.method, path, content=request.post_data_buffer,
                           headers={k: v for k, v in request.headers.items()
                                    if k.lower() in ("authorization", "content-type")})
        headers = {"content-type": r.headers.get("content-type", "text/plain")}
        if "content-security-policy" in r.headers:
            headers["content-security-policy"] = r.headers["content-security-policy"]
        route.fulfill(status=r.status_code, body=r.content, headers=headers)

    with sync_playwright() as pw:
        b = pw.chromium.launch(executable_path=EXE, args=["--no-sandbox", "--use-gl=swiftshader",
                                                          "--enable-unsafe-swiftshader"]
                               if EXE else [])
        opts = dict(pw.devices[device]) if device else {
            "viewport": viewport or {"width": 1440, "height": 900}}
        if device:
            opts.pop("default_browser_type", None)
        ctx = b.new_context(reduced_motion=reduced_motion or "no-preference", **opts)
        page = ctx.new_page()
        problems = []
        page.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))
        page.on("console", lambda m: problems.append(m.text) if m.type == "error" else None)
        page.route("**/*", proxy)
        if share:  # the client's read-only link: no sign-in at all
            page.goto(f"http://city.test/city?share={share}")
        else:
            page.goto(f"http://city.test/city{query}")
            page.fill("#token", token)
            page.fill("#tenant", str(world.tenant_a))
            page.click("#go")
        page.wait_for_selector(".hud")
        try:
            yield page, problems
        finally:
            b.close()


def state(page):
    return page.evaluate("window.__city")


def until(page, expr, timeout=20_000):
    """Poll from Python: the page's strict CSP blocks Playwright's in-page polling."""
    import time

    end = time.monotonic() + timeout / 1000
    while time.monotonic() < end:
        js = f"(() => {{ try {{ return !!({expr}); }} catch (e) {{ return false; }} }})()"
        if page.evaluate(js):
            return
        page.wait_for_timeout(100)
    raise AssertionError(f"timed out waiting for {expr}")


def test_desktop_draws_the_real_city_in_3d(client, world, make_token, maker, owner_db, drain):
    Worker(maker).city_heartbeat()
    snap = client.get(f"/tenants/{world.tenant_a}/city/state",
                      headers=auth(make_token, world.alice)).json()
    with city(client, world, make_token(world.alice)) as (page, problems):
        page.wait_for_selector(".stage canvas", timeout=20_000)
        until(page, "window.__city && window.__city.staffed.length > 0")
        s = state(page)
        assert s["mode"] == "high"
        assert sorted(s["staffed"]) == sorted(d["key"] for d in snap["departments"]
                                              if d["staffed"])
        assert "legal" not in s["staffed"] and "creators" not in s["staffed"]
        page.wait_for_selector(".label.ceo", state="attached", timeout=10_000)
        labels = page.locator(".label").all_text_contents()
        assert any("not staffed" in x and "Legal & Admin" in x for x in labels)
        assert any("CEO · Steady" in x for x in labels), " | ".join(labels)
        hud = {k: page.text_content(f"[data-stat={k}] .v") for k in ("online", "flight")}
        assert hud["online"] == f"{snap['hud']['agents_online']}/{snap['hud']['agents_total']}"
        assert page.locator("[data-stat=revenue]").count() == 0  # no connected source
        assert problems == [], problems


def test_real_events_fly_and_land_in_the_ticker(client, world, make_token, maker, owner_db,
                                                drain):
    w = Worker(maker, retry_base_seconds=0)
    w.city_heartbeat()
    with city(client, world, make_token(world.alice)) as (page, problems):
        page.wait_for_selector(".stage canvas", timeout=20_000)
        until(page, "window.__city && window.__city.staffed.length > 0")
        before = state(page)["lastEventId"]
        assert state(page)["envelopes"] == 0  # nothing moves without an event
        new_run(client, world, make_token, objective="Ticker proof command")
        until(page, "window.__city.flown > 0")  # the letter flew (it lasts 2.6 s on screen)
        w.run_once()
        page.wait_for_selector(".ticker li:has-text('Steady → Jaxon: Ticker proof command')",
                               timeout=15_000)
        page.wait_for_selector(".ticker li:has-text('Done: Plan: Ticker proof command')")
        assert state(page)["lastEventId"] > before
        assert problems == [], problems


def test_offline_worker_turns_windows_gray(client, world, make_token, maker, owner_db, drain):
    Worker(maker).city_heartbeat()
    owner_db.execute(update(Agent).where(Agent.tenant_id == world.tenant_a)
                     .values(last_heartbeat_at=text("now() - interval '5 minutes'")))
    owner_db.commit()
    with city(client, world, make_token(world.alice)) as (page, problems):
        until(page, "window.__city && window.__city.windows.offline > 0", timeout=20_000)
        s = state(page)
        assert set(s["windows"]) == {"offline"}


def test_phone_gets_low_mode_and_a_list_with_the_same_data(client, world, make_token, maker,
                                                           owner_db, drain):
    Worker(maker).city_heartbeat()
    with city(client, world, make_token(world.alice), device="iPhone 15") as (page, problems):
        until(page, "window.__city && window.__city.mode")
        assert state(page)["mode"] == "low"
        page.click("#list-toggle")
        page.wait_for_selector(".list")
        txt = page.text_content(".list")
        assert "Legal & Admin — not staffed" in txt and "Content Writer" in txt
        assert "Steady's inbox" in txt
        width = page.evaluate("document.documentElement.scrollWidth")
        assert width <= page.viewport_size["width"]  # no sideways scroll on the phone
        assert problems == [], problems


def test_demo_banner_only_behind_the_flag(client, world, make_token, monkeypatch):
    monkeypatch.setenv("JAXON_DEMO", "true")
    with city(client, world, make_token(world.alice), reduced_motion="reduce") as (page, _):
        page.wait_for_selector(".demo-banner")
        assert "DEMO DATA" in page.text_content(".demo-banner")
    monkeypatch.delenv("JAXON_DEMO")
    with city(client, world, make_token(world.alice), reduced_motion="reduce") as (page, _):
        until(page, "window.__city && window.__city.mode")
        assert page.locator(".demo-banner").count() == 0


def test_city_page_and_assets_are_served_safely(client):
    r = client.get("/city")
    assert r.status_code == 200 and "script-src 'self'" in r.headers["content-security-policy"]
    js = re.findall(r'src="/city/assets/([^"]+)"', r.text)[0]
    a = client.get(f"/city/assets/{js}")
    assert a.status_code == 200 and a.headers["content-type"].startswith("text/javascript")
    assert "immutable" in a.headers["cache-control"]
    assert client.get("/city/assets/..%2F..%2Fmain.py").status_code == 404
    assert client.get("/city/assets/nope.js").status_code == 404


def test_production_bundle_has_no_fake_activity():
    """Acceptance 6: our own chunks contain no random source, faker or mock data. Library
    chunks (vendor-*) use random ids internally (React keys, three.js UUIDs)."""
    ours = [p for p in DIST.glob("*.js") if not p.name.startswith("vendor")]
    assert ours, "City bundle missing: run `pnpm --filter @jackson-os/city build`"
    for p in ours:
        src = p.read_text()
        assert "Math.random" not in src, p.name
        assert not re.search(r"faker|lorem ipsum|mockAgents|fakeEvents", src, re.I), p.name
    for p in DIST.glob("*.js"):
        assert "faker" not in p.read_text().lower(), p.name




# --- Phase 4: click-through, real controls, CEO inbox -------------------------------------------

def click3d(page, name, timeout=20_000):
    """Click a 3D object with the real mouse at its projected position."""
    until(page, f"window.__cityScreenOf && window.__cityScreenOf({name!r})", timeout)
    until(page, "window.__cityCamBusy === false", timeout)  # not mid-flight
    x, y = page.evaluate(f"window.__cityScreenOf({name!r})")
    page.mouse.click(x, y)


def test_ceo_tower_inbox_approves_and_the_agent_continues(client, world, make_token, maker,
                                                          owner_db, drain):
    from jackson_api.policy import Action, Category
    from jackson_api.worker import TaskResult

    def plan(ctx):
        ctx.authorize(Action(Category.SPEND, "Buy 2 grip samples", 1800, always_ask=True))
        return TaskResult({"bought": True})

    w = Worker(maker, handlers={"plan": plan}, retry_base_seconds=0)
    w.city_heartbeat()
    run_id = new_run(client, world, make_token, objective="Get samples")
    w.run_once()
    with city(client, world, make_token(world.alice)) as (page, problems):
        page.wait_for_selector(".label.ceo", state="attached", timeout=20_000)
        click3d(page, "ceo-tower")
        page.wait_for_selector("[data-panel=inbox] li:has-text('Buy 2 grip samples')")
        assert "$18.00" in page.text_content("[data-panel=inbox]")
        page.click("[data-panel=inbox] li:has-text('Buy 2 grip samples') >> text=Approve")
        page.wait_for_selector("[data-panel=inbox] .msg:has-text('Approved')")
        assert problems == [], problems
    w.run_once()  # acceptance 5: the waiting agent continues
    from conftest import run_state
    run, tasks = run_state(owner_db, run_id)
    assert run.status == "succeeded" and tasks[0].output == {"bought": True}


def test_building_floor_office_agent_and_real_pause(client, world, make_token, maker, owner_db,
                                                    drain):
    w = Worker(maker, retry_base_seconds=0)
    w.city_heartbeat()
    client.post(f"/tenants/{world.tenant_a}/content/generate",
                headers=auth(make_token, world.alice),
                json={"brief": "grip tips", "channel": "x", "count": 1,
                      "business_id": str(world.biz_a)})
    w.run_once()
    with city(client, world, make_token(world.alice)) as (page, problems):
        page.wait_for_selector(".label.ceo", state="attached", timeout=20_000)
        click3d(page, "building-marketing")
        page.wait_for_selector("[data-panel=building] h2:has-text('Marketing')")
        page.click("[data-panel=building] button.floor:has-text('Content')")
        page.wait_for_selector(".floor-bar:has-text('Floor 1 · Content')")
        page.wait_for_selector(".screen:has-text('Content Writer')", state="attached")
        writer = owner_db.scalar(select(Agent.id).where(Agent.tenant_id == world.tenant_a,
                                                        Agent.key == "kind:content_generate"))
        page.wait_for_selector(f".screen[data-agent='{writer}'] .tools:has-text('Tool: model')",
                               state="attached")  # its real model call, on its monitor
        click3d(page, f"desk-{writer}")
        page.wait_for_selector("[data-panel=agent] h2:has-text('Content Writer')")
        assert "Done today" in page.text_content("[data-panel=agent]")
        page.click("[data-panel=agent] button:has-text('Pause')")
        page.wait_for_selector("[data-panel=agent] .msg:has-text('Now paused')")
        owner_db.expire_all()
        assert owner_db.get(Agent, writer).status == "paused"  # really paused, in the DB
        page.click("[data-panel=agent] button:has-text('Resume')")
        page.wait_for_selector("[data-panel=agent] .msg:has-text('Now idle')")
        assert problems == [], problems


def test_orb_shows_routing_and_phone_list_opens_panels(client, world, make_token, maker,
                                                       owner_db, drain):
    w = Worker(maker, retry_base_seconds=0)
    w.city_heartbeat()
    new_run(client, world, make_token, objective="Route me please")
    w.run_once()
    with city(client, world, make_token(world.alice)) as (page, problems):
        page.wait_for_selector(".label.ceo", state="attached", timeout=20_000)
        click3d(page, "jaxon-orb")
        page.wait_for_selector("[data-panel=jaxon] li:has-text('Route me please')")
        assert "Jaxon → Jaxon Planner" in page.text_content("[data-panel=jaxon]")
    with city(client, world, make_token(world.alice), device="iPhone 15") as (page, problems):
        page.click("#list-toggle")
        page.click(".list .agent-link:has-text('Content Writer')")
        page.wait_for_selector("[data-panel=agent] h2:has-text('Content Writer')")
        page.click("[data-panel=agent] .close")
        page.click("#open-inbox")
        page.wait_for_selector("[data-panel=inbox]")
        assert problems == [], problems


# --- Phase 5: command bar -> Jaxon routing, end to end -----------------------------------------

def test_typing_a_command_flies_to_jaxon_and_a_real_agent_starts(client, world, make_token,
                                                                 maker, owner_db, drain):
    """Acceptance 2: a row in commands (workflow_runs) and events, the letter flies, and a
    real agent starts the routed task."""
    from jackson_api.models import Event, Task, WorkflowRun

    w = Worker(maker, retry_base_seconds=0)
    w.city_heartbeat()
    text = "Write an Instagram post about the magnetic grip"
    with city(client, world, make_token(world.alice)) as (page, problems):
        page.wait_for_selector(".label.ceo", state="attached", timeout=20_000)
        page.fill("#command", text)
        page.click(".command-bar button")
        page.wait_for_selector(".cmd-msg:has-text('Sent to Jaxon')")
        owner_db.expire_all()
        run = owner_db.scalar(select(WorkflowRun).where(WorkflowRun.objective == text))
        assert run is not None  # the command row
        assert owner_db.scalar(select(Event.id).where(Event.run_id == run.id,
                                                      Event.type == "command.issued"))
        until(page, "window.__city.flown > 0")  # the letter flew (it lasts 2.6 s on screen)
        page.wait_for_selector(f".ticker li:has-text('Steady → Jaxon: {text}')")
        w.run_once()  # Jaxon plans and routes
        page.wait_for_selector(".ticker li:has-text('Jaxon → Content Writer')", timeout=15_000)
        w.run_once()  # the Content Writer starts the routed task
        owner_db.expire_all()
        routed = owner_db.scalar(select(Task).where(Task.run_id == run.id,
                                                    Task.kind == "content_generate"))
        assert routed is not None and routed.started_at is not None
        assert owner_db.scalar(select(Event.id).where(Event.task_id == routed.id,
                                                      Event.type == "task.started"))
        page.wait_for_selector(".ticker li:has-text('Done: Draft content')", timeout=15_000)
        assert problems == [], problems


# --- Phase 6: client view, replay, trace, red letters -----------------------------------------

def test_client_link_shows_a_read_only_city_of_one_client(client, world, make_token, maker,
                                                          owner_db, drain, monkeypatch):
    import uuid as _uuid

    from jackson_api.models import Business

    monkeypatch.setenv("UNSUBSCRIBE_SECRET", "s" * 40)
    h = auth(make_token, world.alice)
    other = Business(tenant_id=world.tenant_a, name="Other client", kind="agency")
    owner_db.add(other)
    owner_db.commit()
    for biz, text_ in ((world.biz_a, "Client A launch plan"), (other.id, "Secret other plan")):
        client.post(f"/tenants/{world.tenant_a}/commands", headers=h, json={
            "type": "PLAN", "objective": text_, "business_id": str(biz),
            "idempotency_key": _uuid.uuid4().hex})
    w = Worker(maker, retry_base_seconds=0)
    w.city_heartbeat()
    w.run_once()
    w.run_once()
    token = client.post(f"/tenants/{world.tenant_a}/city/shares", headers=h,
                        json={"business_id": str(world.biz_a)}).json()["token"]
    with city(client, world, "", share=token) as (page, problems):
        page.wait_for_selector(".client-banner:has-text('read only')")
        page.wait_for_selector(".ticker li:has-text('Client A launch plan')")
        body = page.text_content("body")
        assert "Secret other plan" not in body
        assert page.locator(".command-bar").count() == 0  # no commands from a client view
        assert page.locator("#replay").count() == 0
        assert page.locator(".ticker .traceable").count() == 0
        page.wait_for_selector(".label.ceo", state="attached", timeout=20_000)
        click3d(page, "ceo-tower")
        page.wait_for_timeout(500)
        assert page.locator("[data-panel=inbox]").count() == 0  # Steady's inbox stays private
        assert problems == [], problems
    sid = token.split(".")[1]
    client.post(f"/tenants/{world.tenant_a}/city/shares/{sid}/revoke", headers=h)
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:  # a revoked link shows a plain refusal, no city
        b = pw.chromium.launch(executable_path=EXE, args=["--no-sandbox"] if EXE else [])
        page = b.new_page()
        page.route("**/*", lambda route, req: route.fulfill(
            status=(r := client.get(req.url.removeprefix("http://city.test"))).status_code,
            body=r.content, headers={"content-type": r.headers.get("content-type", "")}))
        page.goto(f"http://city.test/city?share={token}")
        page.wait_for_selector(".err:has-text('not valid or has expired')")
        b.close()


def test_replay_trace_and_red_letters(client, world, make_token, maker, owner_db, drain):
    from jackson_api import city as city_mod
    from jackson_api.models import Agent as AgentRow

    w = Worker(maker, retry_base_seconds=0)
    w.city_heartbeat()
    new_run(client, world, make_token, objective="Replay me later")
    w.run_once()
    a = owner_db.scalar(select(AgentRow).where(AgentRow.tenant_id == world.tenant_a,
                                               AgentRow.key == "kind:content_generate"))
    city_mod.alert(owner_db, a, "cost_guard", "Content Writer spent $6.00 today, over its "
                                              "$5.00 limit.")
    owner_db.commit()
    with city(client, world, make_token(world.alice)) as (page, problems):
        page.wait_for_selector(".label.ceo", state="attached", timeout=20_000)
        # trace: click a ticker line back to the command
        page.click(".ticker .traceable:has-text('Replay me later')")
        page.wait_for_selector("[data-panel=trace]:has-text('Steady: “Replay me later”')")
        page.click("[data-panel=trace] .close")
        # red letter in the CEO inbox
        click3d(page, "ceo-tower")
        page.wait_for_selector("[data-panel=inbox] [data-letter=cost_guard]:has-text('over its')")
        page.click("[data-panel=inbox] .close")
        # replay the last 24h from the event log
        page.click("#replay")
        page.wait_for_selector(".replay-banner")
        page.select_option(".replay-bar select", "3600")
        page.wait_for_selector(".ticker li:has-text('Steady → Jaxon: Replay me later')",
                               timeout=20_000)
        assert page.locator(".command-bar").count() == 0  # no commands while replaying
        page.click(".replay-bar button:has-text('Live')")
        page.wait_for_selector(".command-bar")
        assert problems == [], problems


# --- Business lines (ADR-039) -------------------------------------------------------------------

def test_a_building_shows_its_business_line(client, world, make_token, maker, owner_db, drain):
    from jackson_api.models import Business as B
    from jackson_api.seed import propose_line_agents

    owner_db.add(B(tenant_id=world.tenant_a, name="Creator X Agency", kind="agency",
                   line="creators"))
    owner_db.flush()
    propose_line_agents(owner_db, world.tenant_a, None)
    owner_db.commit()
    Worker(maker).city_heartbeat()
    with city(client, world, make_token(world.alice)) as (page, problems):
        page.wait_for_selector(".label.ceo", state="attached", timeout=20_000)
        click3d(page, "lot-creators")
        page.wait_for_selector("[data-panel=building] h2:has-text('Creator Agency')")
        page.wait_for_selector("[data-line=creators] .proposed li:has-text('creator-planner')")
        txt = page.text_content("[data-line=creators]")
        assert "not measured" in txt and "CREATOR_EARNINGS_SOURCE" in txt
        assert "Agents never generate explicit content" in txt
        assert problems == [], problems


def test_cinematic_intro_flies_in_from_space_and_any_key_skips_it(client, world, make_token,
                                                                  maker, owner_db, drain):
    Worker(maker).city_heartbeat()
    with city(client, world, make_token(world.alice), query="?intro&fxslow") as (page, problems):
        until(page, "document.documentElement.dataset.cityIntro === 'playing'")
        until(page, "window.__cityCamera")
        x, y, z = page.evaluate("window.__cityCamera()")
        assert (x * x + y * y + z * z) ** 0.5 > 150  # starts out in deep space
        page.keyboard.press("Escape")  # any key skips
        until(page, "document.documentElement.dataset.cityIntro === 'done'", 10_000)
        x, y, z = page.evaluate("window.__cityCamera()")
        assert abs(x) < 1 and 40 < y < 50 and 85 < z < 100  # landed on the overview
        assert problems == [], problems


def test_automation_and_reduced_motion_skip_the_intro(client, world, make_token, maker,
                                                      owner_db, drain):
    Worker(maker).city_heartbeat()
    with city(client, world, make_token(world.alice)) as (page, problems):
        until(page, "document.documentElement.dataset.cityIntro === 'off'")
        assert problems == [], problems


def test_glass_hud_fonts_are_self_hosted_and_allowed(client):
    from jackson_api.routes_city import CITY

    r = client.get("/city")
    assert "font-src 'self'" in r.headers["content-security-policy"]
    fonts = sorted(p.name for p in (CITY / "assets").glob("*.woff2"))
    assert any("spacegrotesk" in f.lower() for f in fonts)
    assert any("montserrat" in f.lower() for f in fonts)
    for f in fonts:
        a = client.get(f"/city/assets/{f}")
        assert a.status_code == 200 and a.headers["content-type"] == "font/woff2"


def test_glass_hud_renders_with_its_fonts_and_adaptive_resolution(client, world, make_token,
                                                                  maker, owner_db, drain):
    Worker(maker).city_heartbeat()
    with city(client, world, make_token(world.alice)) as (page, problems):
        page.wait_for_selector("[data-stat=online]")
        until(page, "document.fonts.check('600 16px Montserrat') && "
                    "document.fonts.check('16px \"Space Grotesk\"')")
        page.evaluate("document.fonts.load('600 16px Montserrat')")
        until(page, "[...document.fonts].some(f => f.family.includes('Montserrat') "
                    "&& f.status === 'loaded')")
        font = page.evaluate("getComputedStyle(document.querySelector('.stat .v')).fontFamily")
        assert font.startswith('"Montserrat"') or font.startswith("Montserrat"), font
        until(page, "document.documentElement.dataset.cityDpr")
        dpr = float(page.evaluate("document.documentElement.dataset.cityDpr"))
        assert 0.75 <= dpr <= 2
        assert problems == [], problems


OVERLAPS_JS = """(() => {
  const r = [...document.querySelectorAll('.label-layer .label:not(.ticker-holo)')]
    .map(e => [e.textContent, e.getBoundingClientRect()]).filter(([, b]) => b.width > 0);
  const out = [];
  for (let i = 0; i < r.length; i++) for (let j = i + 1; j < r.length; j++) {
    const a = r[i][1], b = r[j][1];
    if (a.left < b.right - 1 && b.left < a.right - 1 &&
        a.top < b.bottom - 1 && b.top < a.bottom - 1)
      out.push(r[i][0] + ' x ' + r[j][0]);
  }
  return out;
})()"""


@pytest.mark.parametrize("where", ["desktop", "phone"])
def test_every_sign_is_readable_no_two_labels_overlap(client, world, make_token, maker,
                                                      owner_db, drain, where):
    import time

    Worker(maker).city_heartbeat()
    kw = {"device": "iPhone 15"} if where == "phone" else {}
    with city(client, world, make_token(world.alice), **kw) as (page, problems):
        page.wait_for_selector(".label.ceo", state="attached", timeout=20_000)
        until(page, "document.querySelectorAll('.label-layer .label').length > 10")
        end, left = time.monotonic() + 25, None
        while time.monotonic() < end:  # the declutter settles within a few passes
            left = page.evaluate(OVERLAPS_JS)
            if not left:
                break
            page.wait_for_timeout(500)
        assert left == [], left
        assert problems == [], problems
