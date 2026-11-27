"""Phase 23, part 3: the City exterior in real Chromium against the real API. Everything
the page shows is checked against database rows."""

import os
import re
from contextlib import contextmanager
from pathlib import Path

from conftest import auth, new_run
from sqlalchemy import text, update

from jackson_api.models import Agent
from jackson_api.worker import Worker

EXE = os.environ.get("BROWSER_EXECUTABLE") or (
    "/opt/pw-browsers/chromium" if os.path.exists("/opt/pw-browsers/chromium") else None)
DIST = Path(__file__).parents[1] / "jackson_api" / "city_dist" / "assets"


@contextmanager
def city(client, world, token, viewport=None, device=None, reduced_motion=None):
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
        page.goto("http://city.test/city")
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
        assert "fulfillment" not in s["staffed"] and "support" not in s["staffed"]
        page.wait_for_selector(".label.ceo", state="attached", timeout=10_000)
        labels = page.locator(".label").all_text_contents()
        assert any("not staffed" in x and "Support" in x for x in labels)
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
        until(page, "window.__city.envelopes + window.__city.queued > 0")  # the letter flies
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
        assert "Support — not staffed" in txt and "Content Writer" in txt
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


