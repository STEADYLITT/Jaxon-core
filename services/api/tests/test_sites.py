"""Phase 17: website factory (spec vol. 03).

Spec vol. 06 acceptance covered here: "website preview passes tests".
"""

import os
import uuid

import httpx
import pytest
from conftest import auth, events, run_state
from sqlalchemy import text, update
from sqlalchemy.exc import DBAPIError

from jackson_api import sites as sites_mod
from jackson_api.models import SiteProject, SiteVersion
from jackson_api.sites import (
    DnsVerifier,
    build_site,
    contrast,
    preview_check,
    static_checks,
)
from jackson_api.worker import Worker

REQ = {"business_name": "CSW Trades", "tagline": "Electrical work done right",
       "intro": "Licensed electricians serving the county.", "about": "Family run.\n\nInsured.",
       "services": [{"name": "Rewiring", "description": "Whole-home rewiring."},
                    {"name": "Panels", "description": "Panel upgrades."}],
       "contact_email": "hello@example.com"}


@pytest.fixture(autouse=True)
def _browser(monkeypatch, tmp_path):
    if not os.environ.get("BROWSER_EXECUTABLE") and os.path.exists("/opt/pw-browsers/chromium"):
        monkeypatch.setenv("BROWSER_EXECUTABLE", "/opt/pw-browsers/chromium")
    monkeypatch.setenv("ARTIFACT_DIR", str(tmp_path))
    monkeypatch.delenv("SITE_DOMAIN_VERIFY", raising=False)


@pytest.fixture()
def alice(make_token, world):
    return auth(make_token, world.alice)


@pytest.fixture()
def fake_preview(monkeypatch):
    """For tests about the flow, not the browser: a stand-in preview that passes."""
    monkeypatch.setattr(sites_mod, "preview_check",
                        lambda files: {"ran": True, "passed": True, "problems": [],
                                       "fake": True})


# --- Build + static checks ----------------------------------------------------------------------

def test_template_build_passes_all_static_checks():
    files = build_site(REQ)
    assert sorted(files) == ["about.html", "contact.html", "index.html", "privacy.html",
                             "services.html"]
    rv = static_checks(files, REQ)
    assert rv["passed"], rv["problems"]
    assert "hello@example.com" in files["privacy.html"]  # privacy notice names a contact


def test_owner_text_is_escaped_so_it_cannot_inject_script():
    req = {**REQ, "tagline": '<script>alert(1)</script>', "about": '<img src=x onerror=a()>'}
    files = build_site(req)
    assert "<script>" not in files["index.html"] and "<img" not in files["about.html"]
    assert static_checks(files, req)["passed"]


def test_pages_can_be_left_out_and_links_still_resolve():
    files = build_site({**REQ, "pages": ["about"]})
    assert sorted(files) == ["about.html", "index.html", "privacy.html"]
    assert static_checks(files, REQ)["passed"]


GOOD = build_site(REQ)["index.html"]


@pytest.mark.parametrize("src,needle", [
    (GOOD.replace("<!doctype html>", ""), "doctype"),
    (GOOD.replace(' lang="en"', ""), "lang"),
    (GOOD.replace('name="description"', 'name="x"'), "meta description"),
    (GOOD.replace("width=device-width", "width=1024"), "viewport"),
    (GOOD.replace("<h1>", "<h2>").replace("</h1>", "</h2>"), "exactly one <h1>"),
    (GOOD.replace("</main>", "<h4>x</h4></main>"), "heading jumps"),
    (GOOD.replace("</main>", '<img src="a.png"></main>'), "alt text"),
    (GOOD.replace("</main>", "<script>x()</script></main>"), "scripts"),
    (GOOD.replace("</main>", '<input id="e" type="email"></main>'), "without a label"),
    (GOOD.replace("</main>", '<a href="pricing.html">p</a></main>'), "broken link"),
    (GOOD.replace('href="privacy.html"', 'href="#"'), "privacy notice"),
])
def test_static_checks_catch_problems(src, needle):
    rv = static_checks({**build_site(REQ), "index.html": src}, REQ)
    assert not rv["passed"] and any(needle in p for p in rv["problems"]), rv["problems"]


def test_low_contrast_brand_colours_fail():
    assert contrast("#000000", "#ffffff") == 21.0
    req = {**REQ, "brand": {"primary": "#ffcc00", "text": "#999999", "background": "#ffffff"}}
    problems = static_checks(build_site(req), req)["problems"]
    assert sum("contrast too low" in p for p in problems) == 3


def test_invented_claims_in_requirements_fail():
    req = {**REQ, "tagline": "The #1 electrician, 500+ five-star reviews"}
    assert any(p.startswith("copy:") for p in static_checks(build_site(req), req)["problems"])


# --- Real browser preview -----------------------------------------------------------------------

def test_preview_in_real_chromium_passes_and_takes_a_screenshot():
    rv = preview_check(build_site(REQ))
    assert rv["ran"] and rv["passed"], rv["problems"]
    assert rv["_screenshot"].startswith(b"\x89PNG")


def test_preview_catches_runtime_layout_and_navigation_problems():
    files = build_site(REQ)
    files["about.html"] = files["about.html"].replace(
        "</main>", '<div style="width:900px">wide</div></main>')
    files["services.html"] = files["services.html"].replace("<h1>", '<h1 style="display:none">')
    files["index.html"] = files["index.html"].replace(
        '<li><a href="contact.html">', '<li><a href="missing.html">')
    files["contact.html"] = files["contact.html"].replace(
        "</head>", '<link rel="stylesheet" href="https://cdn.example.com/x.css"></head>')
    problems = " | ".join(preview_check(files)["problems"])
    assert "phone about.html: page scrolls sideways" in problems
    assert "services.html: <h1> not visible" in problems
    assert "missing.html" in problems  # nav link to a page that isn't there
    assert "cdn.example.com" in problems  # nothing loads from outside the site


# --- API flow -----------------------------------------------------------------------------------

def make_site(client, world, alice, **req):
    r = client.post(f"/tenants/{world.tenant_a}/sites", headers=alice,
                    json={"name": "CSW site", "requirements": {**REQ, **req}})
    assert r.status_code == 201, r.text
    return r.json()


def build(client, world, alice, maker, owner_db, site_id):
    r = client.post(f"/tenants/{world.tenant_a}/sites/{site_id}/build", headers=alice,
                    json={"idempotency_key": uuid.uuid4().hex})
    Worker(maker, retry_base_seconds=0).run_once()
    run, tasks = run_state(owner_db, r.json()["run_id"])
    assert run.status == "succeeded", tasks[0].error
    return tasks[0].output


def launch(client, world, alice, sid, vid, domain="cswtrades.example.com"):
    return client.post(f"/tenants/{world.tenant_a}/sites/{sid}/launch", headers=alice,
                       json={"version_id": vid, "domain": domain,
                             "idempotency_key": uuid.uuid4().hex})


def approve_pending(client, world, alice, run_id):
    [ap] = [a for a in client.get(f"/tenants/{world.tenant_a}/approvals?status=pending",
                                  headers=alice).json() if a["run_id"] == run_id]
    assert ap["category"] == "production_change"
    client.post(f"/tenants/{world.tenant_a}/approvals/{ap['id']}/decision",
                json={"decision": "approve"}, headers=alice)


def test_website_preview_passes_tests(client, world, alice, maker, owner_db, drain):
    """Spec acceptance: website preview passes tests. Requirements -> build -> HTML and
    accessibility checks -> real-browser preview at desktop and phone size -> a stored,
    reviewable version with a screenshot."""
    site = make_site(client, world, alice)
    out = build(client, world, alice, maker, owner_db, site["id"])
    assert out["passed"] is True and out["problems"] == [] and out["number"] == 1
    assert out["preview_screenshot_artifact_id"]
    got = client.get(f"/tenants/{world.tenant_a}/sites/{site['id']}", headers=alice).json()
    assert got["status"] == "ready"
    checks = got["versions"][0]["checks"]
    assert checks["static"]["passed"] and checks["preview"]["ran"] and \
        checks["preview"]["passed"]
    page = client.get(f"/tenants/{world.tenant_a}/sites/{site['id']}/versions/"
                      f"{out['version_id']}/files/index.html", headers=alice)
    assert page.status_code == 200 and "<h1>CSW Trades</h1>" in page.text
    assert "default-src 'none'" in page.headers["content-security-policy"]


def test_failing_version_is_kept_but_cannot_launch(client, world, alice, maker, owner_db,
                                                   drain, fake_preview):
    site = make_site(client, world, alice, brand={"primary": "#ffee00"})
    out = build(client, world, alice, maker, owner_db, site["id"])
    assert out["passed"] is False and any("contrast" in p for p in out["problems"])
    r = launch(client, world, alice, site["id"], out["version_id"])
    assert r.status_code == 409 and r.json()["detail"]["code"] == "version_not_tested"
    got = client.get(f"/tenants/{world.tenant_a}/sites/{site['id']}", headers=alice).json()
    assert got["status"] == "draft"


def test_launch_needs_owner_approval_then_goes_live_and_can_roll_back(
        client, world, alice, maker, owner_db, drain, fake_preview):
    site = make_site(client, world, alice)
    sid = site["id"]
    v1 = build(client, world, alice, maker, owner_db, sid)["version_id"]
    r = launch(client, world, alice, sid, v1)
    run_id = r.json()["run_id"]
    assert r.json()["dns_record"]["name"] == "_jackson-verify.cswtrades.example.com"
    w = Worker(maker, retry_base_seconds=0)
    w.run_once()
    assert run_state(owner_db, run_id)[0].status == "waiting_approval"  # nothing live yet
    assert client.get(f"/tenants/{world.tenant_a}/sites/{sid}",
                      headers=alice).json()["live_version_id"] is None
    approve_pending(client, world, alice, run_id)
    w.run_once()
    run, tasks = run_state(owner_db, run_id)
    assert run.status == "succeeded" and tasks[0].output["host"] == "mock"
    assert tasks[0].output["domain_check"] == "mock"
    assert "site.launched" in events(owner_db, run_id)
    live = client.get(f"/tenants/{world.tenant_a}/sites/{sid}", headers=alice).json()
    assert live["status"] == "live" and live["live_version_id"] == v1
    assert live["live_url"] == "mock://sites/cswtrades.example.com/v1/"

    # v2 replaces v1; v1 is kept for rollback.
    client.put(f"/tenants/{world.tenant_a}/sites/{sid}/requirements", headers=alice,
               json={**REQ, "tagline": "Now booking spring jobs"})
    v2 = build(client, world, alice, maker, owner_db, sid)["version_id"]
    run2 = launch(client, world, alice, sid, v2).json()["run_id"]
    w.run_once()
    approve_pending(client, world, alice, run2)
    w.run_once()
    live = client.get(f"/tenants/{world.tenant_a}/sites/{sid}", headers=alice).json()
    assert (live["live_version_id"], live["previous_version_id"]) == (v2, v1)

    rb = client.post(f"/tenants/{world.tenant_a}/sites/{sid}/rollback", headers=alice).json()
    assert (rb["live_version_id"], rb["previous_version_id"]) == (v1, v2)
    assert rb["live_url"].endswith("/v1/")


def test_rollback_needs_a_previous_version(client, world, alice):
    site = make_site(client, world, alice)
    r = client.post(f"/tenants/{world.tenant_a}/sites/{site['id']}/rollback", headers=alice)
    assert r.status_code == 409


def test_unverified_domain_stops_the_launch_before_asking_for_approval(
        client, world, alice, maker, owner_db, drain, fake_preview, monkeypatch):
    monkeypatch.setenv("SITE_DOMAIN_VERIFY", "dns")
    monkeypatch.setattr(sites_mod, "DnsVerifier", lambda: DnsVerifier(client=httpx.Client(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, json={"Status": 0})))))
    site = make_site(client, world, alice)
    v1 = build(client, world, alice, maker, owner_db, site["id"])["version_id"]
    run_id = launch(client, world, alice, site["id"], v1).json()["run_id"]
    Worker(maker, retry_base_seconds=0).run_once()
    run, tasks = run_state(owner_db, run_id)
    assert run.status == "failed" and "_jackson-verify.cswtrades.example.com" in tasks[0].error
    assert not [a for a in client.get(f"/tenants/{world.tenant_a}/approvals",
                                      headers=alice).json() if a["run_id"] == run_id]


def test_dns_verifier_reads_the_txt_record():
    seen = {}

    def handler(req):
        seen["url"] = str(req.url)
        return httpx.Response(200, json={"Status": 0, "Answer": [
            {"name": "_jackson-verify.a.example.com", "type": 16,
             "data": '"jackson-verify=tok123"'}]})
    v = DnsVerifier(client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert v.verify("a.example.com", "tok123")["verified"] is True
    assert "name=_jackson-verify.a.example.com" in seen["url"] and "type=TXT" in seen["url"]
    assert v.verify("a.example.com", "other")["verified"] is False


@pytest.mark.parametrize("domain", ["localhost", "intranet.local", "no_dots", "a.internal",
                                    "http://x.com"])
def test_launch_rejects_non_public_domains(client, world, alice, domain):
    site = make_site(client, world, alice)
    assert launch(client, world, alice, site["id"], str(uuid.uuid4()),
                  domain).status_code == 422


# --- Database guarantees ------------------------------------------------------------------------

def test_only_tested_versions_can_go_live_even_with_direct_sql(client, world, alice, maker,
                                                               owner_db, drain):
    site = make_site(client, world, alice, brand={"primary": "#ffee00"})
    sid = uuid.UUID(site["id"])
    with maker() as db:  # the worker role, where agents run
        db.add(SiteVersion(tenant_id=world.tenant_a, site_id=sid, number=1,
                           files={"index.html": "x"}, checks={}, passed=False))
        db.commit()
    vid = owner_db.scalar(text("SELECT id FROM site_versions WHERE site_id = :s"), {"s": sid})
    with maker() as db, pytest.raises(DBAPIError, match="site_version_not_tested"):
        db.execute(update(SiteProject).where(SiteProject.id == sid)
                   .values(live_version_id=vid, status="live"))
    with maker() as db, pytest.raises(DBAPIError, match="site_version_not_tested"):
        db.execute(update(SiteProject).where(SiteProject.id == sid).values(status="live"))
    with maker() as db, pytest.raises(DBAPIError, match="permission denied"):
        db.execute(update(SiteVersion).where(SiteVersion.id == vid).values(passed=True))
    with pytest.raises(DBAPIError, match="immutable"):  # not even the schema owner
        owner_db.execute(update(SiteVersion).where(SiteVersion.id == vid).values(passed=True))
    owner_db.rollback()
    with maker() as db, pytest.raises(DBAPIError, match="permission denied"):
        db.execute(update(SiteProject).where(SiteProject.id == sid).values(domain="evil.com"))


def test_sites_are_tenant_isolated(client, world, alice, make_token):
    site = make_site(client, world, alice)
    bob = auth(make_token, world.bob)
    assert client.get(f"/tenants/{world.tenant_b}/sites/{site['id']}",
                      headers=bob).status_code == 404
    assert client.get(f"/tenants/{world.tenant_a}/sites/{site['id']}",
                      headers=bob).status_code == 403


def test_a_failing_browser_preview_fails_the_version(client, world, alice, maker, owner_db,
                                                     drain, monkeypatch):
    monkeypatch.setattr(sites_mod, "preview_check", lambda files: {
        "ran": True, "passed": False, "problems": ["phone index.html: page scrolls sideways"]})
    site = make_site(client, world, alice)
    out = build(client, world, alice, maker, owner_db, site["id"])
    assert out["passed"] is False and "scrolls sideways" in out["problems"][0]
    assert launch(client, world, alice, site["id"], out["version_id"]).status_code == 409


def test_worker_refuses_to_launch_an_untested_version_before_asking(client, world, alice,
                                                                     maker, owner_db, drain):
    """Defence in depth: even a launch task created without the API stops at the worker,
    before any approval is requested."""
    from jackson_api.runs import create_run

    site = make_site(client, world, alice)
    sid = uuid.UUID(site["id"])
    v = SiteVersion(tenant_id=world.tenant_a, site_id=sid, number=1, files={"index.html": "x"},
                    checks={}, passed=False)
    owner_db.add(v)
    owner_db.execute(update(SiteProject).where(SiteProject.id == sid)
                     .values(domain="a.example.com", domain_token="t" * 32))
    owner_db.flush()
    uid = owner_db.scalar(text("SELECT id FROM users WHERE auth_subject = :s"), {"s": world.alice})
    run, _ = create_run(owner_db, world.tenant_a, uid, {
        "type": "BUILD", "objective": "launch", "idempotency_key": uuid.uuid4().hex},
        first_task={"kind": "site_launch", "title": "launch", "tool_scope": [],
                    "input": {"site_id": str(sid), "version_id": str(v.id)}})
    run_id = str(run.id)
    owner_db.commit()
    Worker(maker, retry_base_seconds=0).run_once()
    run, tasks = run_state(owner_db, run_id)
    assert run.status == "failed" and "did not pass its tests" in tasks[0].error
    assert not [a for a in client.get(f"/tenants/{world.tenant_a}/approvals",
                                      headers=alice).json() if a["run_id"] == run_id]
