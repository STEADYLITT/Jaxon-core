"""Phase 11 acceptance: visual browser sessions, against real Postgres + real Chromium.

Spec vol. 06 acceptance covered here: "browser session visible".
"""

import os
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from conftest import auth, events, run_state
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from jackson_api.artifacts import LocalArtifactStore
from jackson_api.models import Artifact, BrowserSession
from jackson_api.worker import TaskResult, Worker

PAGES = {
    "/home": "<title>Supplier Home</title><h1>Supplier X</h1>"
             "<a id=next href='/next'>Pricing</a><p>$1.80/unit</p>",
    "/next": "<title>Pricing</title><h1>Pricing</h1><p>MOQ 500</p>",
    "/login": "<title>Login</title><form><input name=user value=steady>"
              "<input type=password name=pw value=hunter2-secret>"
              "<span data-sensitive>Acct 4111-1111-1111-1111</span></form>",
    "/tracker": "<title>Tracker</title><img src='http://tracker.invalid/pixel.gif'>"
                "<script src='http://evil.invalid/x.js'></script><p>ok</p>",
}


class Site(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):  # noqa: N802
        body = PAGES.get(self.path.split("?")[0], "<title>404</title>not found")
        self.send_response(200 if self.path.split("?")[0] in PAGES else 404)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(body.encode())


@pytest.fixture(scope="module")
def site():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Site)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.fixture(autouse=True)
def _browser_env(monkeypatch, tmp_path):
    # Use the pre-installed Chromium where present (dev box); CI installs Playwright's own.
    if not os.environ.get("BROWSER_EXECUTABLE") and os.path.exists("/opt/pw-browsers/chromium"):
        monkeypatch.setenv("BROWSER_EXECUTABLE", "/opt/pw-browsers/chromium")
    monkeypatch.setenv("ARTIFACT_DIR", str(tmp_path / "artifacts"))


@pytest.fixture()
def alice(make_token, world):
    return auth(make_token, world.alice)


def setup(client, world, alice, level=2, domains=("127.0.0.1",)):
    client.put(f"/tenants/{world.tenant_a}/businesses/{world.biz_a}/autonomy",
               json={"level": level}, headers=alice)
    if domains is not None:
        r = client.post(f"/tenants/{world.tenant_a}/tool-grants", headers=alice, json={
            "tool_name": "browser.session", "business_id": str(world.biz_a),
            "config": {"allowed_domains": list(domains)}})
        assert r.status_code == 201
    r = client.post(f"/tenants/{world.tenant_a}/commands", headers=alice, json={
        "type": "PLAN", "objective": "Check supplier site", "business_id": str(world.biz_a),
        "idempotency_key": f"k-{uuid.uuid4().hex}"})
    return r.json()["id"]


def run(maker, fn):
    out = {}

    def handler(ctx):
        out["r"] = fn(ctx)
        return TaskResult({"ok": True})

    Worker(maker, handlers={"plan": handler}, retry_base_seconds=0, browser_poll_s=0.05,
           artifact_store=LocalArtifactStore()).run_once()
    return out.get("r")


def sessions(owner_db, run_id):
    owner_db.expire_all()
    return owner_db.scalars(select(BrowserSession).where(
        BrowserSession.run_id == uuid.UUID(run_id))).all()


# --- Visible session --------------------------------------------------------------------------

def test_browser_session_is_visible(client, world, alice, maker, owner_db, drain, site):
    """Spec acceptance: browser session visible. Each step leaves a screenshot the owner
    can fetch, the session shows the current URL/title, and the live feed announces it."""
    run_id = setup(client, world, alice)

    def browse(ctx):
        with ctx.browser() as b:
            first = b.goto(f"{site}/home")
            after = b.click("#next")
            return first, after, b.text()

    first, after, page = run(maker, browse)
    assert first["title"] == "Supplier Home" and after["title"] == "Pricing"
    assert "MOQ 500" in page["text"] and page["untrusted"] is True
    [s] = sessions(owner_db, run_id)
    assert s.status == "closed" and s.current_url.endswith("/next") and s.title == "Pricing"
    assert str(s.last_screenshot_id) == after["screenshot_artifact_id"]

    shot = client.get(f"/tenants/{world.tenant_a}/artifacts/{after['screenshot_artifact_id']}",
                      headers=alice)
    assert shot.status_code == 200 and shot.headers["content-type"] == "image/png"
    assert shot.content[:8] == b"\x89PNG\r\n\x1a\n"
    api = client.get(f"/tenants/{world.tenant_a}/browser-sessions/{s.id}", headers=alice).json()
    assert api["current_url"].endswith("/next")
    ev = events(owner_db, run_id)
    assert ev.count("browser.screenshot") == 2
    assert "browser.opened" in ev and "browser.closed" in ev
    sse = client.get(f"/tenants/{world.tenant_a}/events?run_id={run_id}&timeout=0.5",
                     headers=alice).text
    assert "event: browser.screenshot" in sse


def test_sensitive_fields_masked_and_stripped(client, world, alice, maker, owner_db, drain,
                                              site):
    setup(client, world, alice)

    def login_page(ctx):
        with ctx.browser() as b:
            return b.goto(f"{site}/login"), b.text()

    shot, page = run(maker, login_page)
    assert shot["masked_elements"] == 2  # password input + [data-sensitive]
    assert "4111-1111-1111-1111" not in page["text"] and "hunter2" not in page["text"]
    art = owner_db.get(Artifact, uuid.UUID(shot["screenshot_artifact_id"]))
    assert art.meta["masked_elements"] == 2


def test_every_request_is_limited_to_the_allowlist(client, world, alice, maker, owner_db,
                                                   drain, site):
    run_id = setup(client, world, alice)

    def visit(ctx):
        with ctx.browser() as b:
            b.goto(f"{site}/tracker")
            blocked = list(b.blocked)
            try:
                b.goto("http://evil.invalid/")
            except Exception as e:  # noqa: BLE001
                return blocked, str(e)
        return blocked, None

    blocked, err = run(maker, visit)
    assert "tracker.invalid" in blocked and "evil.invalid" in blocked  # sub-resources blocked
    assert "allowlist" in err
    assert run_state(owner_db, run_id)[0].status == "succeeded"


def test_browser_requires_a_grant(client, world, alice, maker, owner_db, drain, site):
    run_id = setup(client, world, alice, domains=None)
    run(maker, lambda ctx: ctx.browser().__enter__())
    run_, tasks = run_state(owner_db, run_id)
    assert run_.status == "failed" and "not granted" in tasks[0].error


def test_click_needs_autonomy_level_2(client, world, alice, maker, owner_db, drain, site):
    run_id = setup(client, world, alice, level=1)

    def browse(ctx):
        with ctx.browser() as b:
            b.goto(f"{site}/home")
            b.click("#next")

    run(maker, browse)
    assert run_state(owner_db, run_id)[0].status == "waiting_approval"
    [s] = sessions(owner_db, run_id)
    assert s.title == "Supplier Home"  # the read worked; the click did not happen


# --- Owner controls ------------------------------------------------------------------------------

def control(client, world, alice, session_id, action):
    return client.post(f"/tenants/{world.tenant_a}/browser-sessions/{session_id}/control",
                       json={"action": action}, headers=alice)


def test_pause_blocks_the_agent_until_resume(client, world, alice, maker, owner_db, drain, site):
    run_id = setup(client, world, alice)
    timeline = []

    def browse(ctx):
        with ctx.browser() as b:
            b.goto(f"{site}/home")
            control(client, world, alice, b.session_id, "pause")
            threading.Timer(0.6, lambda: control(client, world, alice, b.session_id,
                                                 "resume")).start()
            t0 = time.monotonic()
            b.goto(f"{site}/next")
            timeline.append(time.monotonic() - t0)

    run(maker, browse)
    assert timeline[0] >= 0.5  # the agent really waited for the owner
    ev = events(owner_db, run_id)
    assert ev.index("browser.paused") < ev.index("browser.resumed")
    assert run_state(owner_db, run_id)[0].status == "succeeded"


def test_takeover_then_terminate_stops_the_agent(client, world, alice, maker, owner_db, drain,
                                                 site):
    run_id = setup(client, world, alice)

    def browse(ctx):
        with ctx.browser() as b:
            b.goto(f"{site}/home")
            control(client, world, alice, b.session_id, "takeover")
            threading.Timer(0.3, lambda: control(client, world, alice, b.session_id,
                                                 "terminate")).start()
            b.goto(f"{site}/next")  # waits during takeover, then sees terminate

    run(maker, browse)
    run_, tasks = run_state(owner_db, run_id)
    assert run_.status == "failed" and "terminated by owner" in tasks[0].error
    [s] = sessions(owner_db, run_id)
    assert s.status == "terminated" and s.title == "Supplier Home"
    ev = events(owner_db, run_id)
    assert "browser.taken_over" in ev and "browser.terminated" in ev
    assert control(client, world, alice, s.id, "resume").status_code == 409


def test_viewer_cannot_control(client, world, alice, maker, owner_db, drain, site, make_token):
    setup(client, world, alice)
    seen = {}

    def browse(ctx):
        with ctx.browser() as b:
            b.goto(f"{site}/home")
            r = client.post(f"/tenants/{world.tenant_a}/browser-sessions/{b.session_id}/control",
                            json={"action": "terminate"}, headers=auth(make_token, world.carol))
            seen["status"] = r.status_code
            b.goto(f"{site}/next")  # still allowed: the viewer's request changed nothing

    run(maker, browse)
    assert seen["status"] == 403


def test_agent_cannot_override_owner_control(maker):
    """The worker role can report status but has no right to write `control`."""
    with maker() as db, pytest.raises(DBAPIError, match="permission denied"):
        db.execute(text("UPDATE browser_sessions SET control = 'none'"))


def test_artifacts_are_tenant_isolated(client, world, alice, maker, owner_db, drain, site,
                                       make_token):
    setup(client, world, alice)

    def browse(ctx):
        with ctx.browser() as b:
            return b.goto(f"{site}/home")

    shot = run(maker, browse)
    bob = auth(make_token, world.bob)
    url = f"/tenants/{world.tenant_b}/artifacts/{shot['screenshot_artifact_id']}"
    assert client.get(url, headers=bob).status_code == 404
    assert client.get(f"/tenants/{world.tenant_a}/artifacts/{shot['screenshot_artifact_id']}",
                      headers=bob).status_code == 403
