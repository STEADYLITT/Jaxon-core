# ruff: noqa: F811 - pytest fixtures imported from test_sites
"""ADR-045: website hosting on Netlify. Approved launches publish for real (zip deploy);
course pages publish the same way. Netlify's network is faked with its documented shapes."""

import io
import uuid
import zipfile

import httpx
import pytest
from conftest import run_state
from sqlalchemy import select
from test_sites import (  # noqa: F401
    _browser,
    alice,
    approve_pending,
    build,
    fake_preview,
    launch,
    make_site,
)

from jackson_api import connections as C
from jackson_api.connectors import hosting as H
from jackson_api.models import Approval, Course
from jackson_api.worker import Worker

TOKEN = "nfp_" + "n" * 40


class FakeNetlify:
    def __init__(self):
        self.sites, self.deploys, self.calls = {}, [], []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.calls.append(req)
        assert req.headers["authorization"] == f"Bearer {TOKEN}"
        p, m = req.url.path, req.method
        if p == "/api/v1/user":
            return httpx.Response(200, json={"full_name": "Steady", "email": "s@example.com"})
        if p == "/api/v1/sites" and m == "GET":
            name = req.url.params["name"]
            return httpx.Response(200, json=[s for s in self.sites.values()
                                             if name in s["name"]])
        if p == "/api/v1/sites" and m == "POST":
            import json
            body = json.loads(req.content)
            sid = f"site{len(self.sites) + 1}"
            s = {"id": sid, "name": body["name"], "custom_domain": body.get("custom_domain"),
                 "ssl_url": f"https://{body['name']}.netlify.app"}
            self.sites[sid] = s
            return httpx.Response(201, json=s)
        if p.startswith("/api/v1/sites/") and p.endswith("/deploys") and m == "POST":
            assert req.headers["content-type"] == "application/zip"
            files = zipfile.ZipFile(io.BytesIO(req.content)).namelist()
            sid = p.split("/")[4]
            self.deploys.append((sid, sorted(files)))
            return httpx.Response(200, json={"id": f"dep{len(self.deploys)}", "state": "uploaded"})
        if p.startswith("/api/v1/deploys/"):
            return httpx.Response(200, json={"id": p.rsplit("/", 1)[-1], "state": "ready"})
        return httpx.Response(404, json={"message": "Not Found"})


@pytest.fixture()
def live(monkeypatch):
    fake = FakeNetlify()
    monkeypatch.setenv("NETLIFY_AUTH_TOKEN", TOKEN)
    monkeypatch.delenv("NETLIFY_SITE_PREFIX", raising=False)
    mk = lambda: httpx.Client(transport=httpx.MockTransport(fake))  # noqa: E731
    monkeypatch.setattr(H, "_new_client", mk)
    monkeypatch.setattr(C, "_new_client", mk)
    monkeypatch.setattr(H.time, "sleep", lambda s: None)
    return fake


def test_names_and_zip():
    assert H.site_name("CSW Trades!! Site") == "jackson-csw-trades-site"
    z = zipfile.ZipFile(io.BytesIO(H.zip_files({"index.html": "<h1>x</h1>"})))
    assert z.read("index.html") == b"<h1>x</h1>"
    with pytest.raises(Exception, match="bad file path"):
        H.zip_files({"../etc/passwd": "x"})


def test_publish_reuses_the_site_and_waits_for_ready(live):
    h = H.NetlifyHost(TOKEN, client=H._new_client(), sleep=lambda s: None)
    a = h.publish("jackson-demo", {"index.html": "a"})
    b = h.publish("jackson-demo", {"index.html": "b", "about.html": "c"})
    assert a["site_id"] == b["site_id"] == "site1" and len(live.sites) == 1
    assert a["url"] == "https://jackson-demo.netlify.app"
    assert live.deploys[1] == ("site1", ["about.html", "index.html"])


def test_connection_test(client, world, alice, live):  # noqa: F811
    r = client.post(f"/tenants/{world.tenant_a}/admin/connectors/hosting/test", headers=alice)
    assert r.json()["ok"] and r.json()["result"]["account"] == "Steady" and TOKEN not in r.text


def test_approved_site_launch_publishes_on_netlify(client, world, alice, maker,  # noqa: F811
                                                   owner_db, drain, fake_preview, live):
    sid = make_site(client, world, alice)["id"]
    v1 = build(client, world, alice, maker, owner_db, sid)["version_id"]
    run_id = launch(client, world, alice, sid, v1).json()["run_id"]
    w = Worker(maker, retry_base_seconds=0)
    w.run_once()
    assert live.deploys == []  # nothing published before approval
    approve_pending(client, world, alice, run_id)
    w.run_once()
    run, tasks = run_state(owner_db, run_id)
    assert run.status == "succeeded", tasks[0].error
    assert tasks[0].output["host"] == "netlify"
    assert tasks[0].output["url"] == "https://cswtrades.example.com/"
    [site] = live.sites.values()
    assert site["custom_domain"] == "cswtrades.example.com"
    assert "index.html" in live.deploys[0][1]


def course(owner_db, world, **kw):
    from test_courses import LESSON, OUTLINE

    from jackson_api.courses import build_platform, build_sales_page
    lessons = {(0, 0): LESSON, (0, 1): LESSON, (1, 0): LESSON}
    c = Course(tenant_id=world.tenant_a, business_id=world.biz_a, brief="b",
               title="AI for Traders", status="ready", mode="live", outline=OUTLINE,
               lessons=[], platform_files=build_platform(OUTLINE, lessons),
               sales_files=build_sales_page(OUTLINE, "49"), checks={}, **kw)
    owner_db.add(c)
    owner_db.commit()
    return c


def test_course_publish_needs_approval_then_both_sites_go_live(client, world, alice,  # noqa: F811
                                                                maker, owner_db, drain, live):
    c = course(owner_db, world)
    r = client.post(f"/tenants/{world.tenant_a}/courses/{c.id}/publish", headers=alice,
                    json={"idempotency_key": uuid.uuid4().hex})
    assert r.status_code == 202, r.text
    w = Worker(maker, retry_base_seconds=0)
    w.run_once()
    ap = owner_db.scalar(select(Approval).where(Approval.run_id == uuid.UUID(r.json()["run_id"])))
    assert ap.category == "production_change" and live.deploys == []
    client.post(f"/tenants/{world.tenant_a}/approvals/{ap.id}/decision", headers=alice,
                json={"decision": "approve"})
    w.run_once()
    run, tasks = run_state(owner_db, r.json()["run_id"])
    assert run.status == "succeeded", tasks[0].error
    out = tasks[0].output
    assert out["platform_url"].endswith("-learn.netlify.app") and out["host"] == "netlify"
    assert len(live.deploys) == 2 and "dashboard.html" in live.deploys[0][1]
    got = client.get(f"/tenants/{world.tenant_a}/courses/{c.id}", headers=alice).json()
    assert got["published"]["sales_url"] == out["sales_url"]


def test_mock_course_cannot_be_published(client, world, alice, maker, owner_db, drain,  # noqa: F811
                                         live):
    c = course(owner_db, world)
    owner_db.execute(Course.__table__.update().where(Course.id == c.id).values(mode="mock"))
    owner_db.commit()
    r = client.post(f"/tenants/{world.tenant_a}/courses/{c.id}/publish", headers=alice,
                    json={"idempotency_key": uuid.uuid4().hex})
    Worker(maker, retry_base_seconds=0).run_once()
    run, tasks = run_state(owner_db, r.json()["run_id"])
    assert tasks[0].status == "failed" and "MOCK" in tasks[0].error and live.deploys == []
