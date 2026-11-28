# ruff: noqa: F811 - pytest fixtures imported from test_sites
"""ADR-050: App Factory builds go to PRIVATE GitHub repos in one commit, after approval.
GitHub's REST API is faked with its documented shapes."""

import json
import uuid

import httpx
import pytest
from conftest import run_state
from sqlalchemy import select
from test_sites import _browser, alice, build, fake_preview, make_site  # noqa: F401

from jackson_api import connections as C
from jackson_api.connectors import github_repos as G
from jackson_api.models import Approval
from jackson_api.worker import Worker

TOKEN = "github_pat_" + "g" * 40


class FakeGitHub:
    def __init__(self):
        self.repos, self.trees, self.calls = {}, [], []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.calls.append(req)
        assert req.headers["authorization"] == f"Bearer {TOKEN}"
        assert req.headers["x-github-api-version"] == "2022-11-28"
        p, m = req.url.path, req.method
        if p == "/user":
            return httpx.Response(200, json={"login": "STEADYLITT"})
        if p == "/user/repos" and m == "POST":
            b = json.loads(req.content)
            assert b["private"] is True and b["auto_init"] is True
            self.repos[b["name"]] = {"name": b["name"], "private": True,
                                     "html_url": f"https://github.com/STEADYLITT/{b['name']}"}
            return httpx.Response(201, json=self.repos[b["name"]])
        parts = p.split("/")
        if len(parts) == 4 and parts[1] == "repos" and m == "GET":
            r = self.repos.get(parts[3])
            return httpx.Response(200, json=r) if r else httpx.Response(
                404, json={"message": "Not Found"})
        if p.endswith("/git/ref/heads/main"):
            return httpx.Response(200, json={"object": {"sha": "head0"}})
        if "/git/commits/" in p and m == "GET":
            return httpx.Response(200, json={"tree": {"sha": "tree0"}})
        if p.endswith("/git/trees") and m == "POST":
            b = json.loads(req.content)
            self.trees.append(b)
            return httpx.Response(201, json={"sha": "tree1"})
        if p.endswith("/git/commits") and m == "POST":
            b = json.loads(req.content)
            assert b["tree"] == "tree1" and b["parents"] == ["head0"]
            return httpx.Response(201, json={"sha": "commit1"})
        if p.endswith("/git/refs/heads/main") and m == "PATCH":
            return httpx.Response(200, json={"object": {"sha": "commit1"}})
        return httpx.Response(404, json={"message": "Not Found"})


@pytest.fixture()
def live(monkeypatch):
    fake = FakeGitHub()
    monkeypatch.setenv("GITHUB_APP_TOKEN", TOKEN)
    monkeypatch.setenv("GITHUB_OWNER", "STEADYLITT")
    mk = lambda: httpx.Client(transport=httpx.MockTransport(fake))  # noqa: E731
    monkeypatch.setattr(G, "_new_client", mk)
    monkeypatch.setattr(C, "_new_client", mk)
    return fake


def test_names_and_paths():
    assert G.repo_name("Site CSW Trades!!") == "jackson-site-csw-trades"
    with pytest.raises(Exception, match="bad file path"):
        G.GitHub(TOKEN, "x", client=httpx.Client()).commit_files("r", {"../x": "y"}, "m")


def test_connection_test(client, world, alice, live):
    r = client.post(f"/tenants/{world.tenant_a}/admin/connectors/github/test", headers=alice)
    assert r.json()["ok"] and r.json()["result"]["signed_in_as"] == "STEADYLITT"
    assert TOKEN not in r.text


def test_site_version_goes_to_a_private_repo_after_approval(client, world, alice, maker,
                                                            owner_db, drain, fake_preview,
                                                            live):
    sid = make_site(client, world, alice)["id"]
    vid = build(client, world, alice, maker, owner_db, sid)["version_id"]
    r = client.post(f"/tenants/{world.tenant_a}/factory/repos", headers=alice,
                    json={"site_id": sid, "version_id": vid,
                          "idempotency_key": uuid.uuid4().hex})
    assert r.status_code == 202, r.text
    w = Worker(maker, retry_base_seconds=0)
    w.run_once()
    assert live.repos == {}  # nothing created before approval
    ap = owner_db.scalar(select(Approval).where(Approval.run_id == uuid.UUID(r.json()["run_id"])))
    assert ap.category == "production_change" and "private GitHub repo" in ap.description
    client.post(f"/tenants/{world.tenant_a}/approvals/{ap.id}/decision", headers=alice,
                json={"decision": "approve"})
    w.run_once()
    run, tasks = run_state(owner_db, r.json()["run_id"])
    assert run.status == "succeeded", tasks[0].error
    out = tasks[0].output
    assert out["private"] is True and out["commit"] == "commit1"
    [repo] = live.repos.values()
    assert repo["name"].startswith("jackson-site-csw-site-")
    paths = {t["path"] for t in live.trees[0]["tree"]}
    assert "index.html" in paths and "README.md" in paths
    listed = client.get(f"/tenants/{world.tenant_a}/factory/repos", headers=alice).json()
    assert listed[0]["repo"] == out["repo"]


def test_public_repo_is_never_pushed_to(live):
    live.repos["jackson-x"] = {"name": "jackson-x", "private": False}
    g = G.GitHub(TOKEN, "STEADYLITT", client=G._new_client())
    with pytest.raises(Exception, match="PUBLIC"):
        g.ensure_repo("jackson-x", "d")


def test_needs_one_source_and_the_keys(client, world, alice, monkeypatch):
    monkeypatch.setenv("GITHUB_APP_TOKEN", TOKEN)
    monkeypatch.setenv("GITHUB_OWNER", "STEADYLITT")
    assert client.post(f"/tenants/{world.tenant_a}/factory/repos", headers=alice,
                       json={"idempotency_key": uuid.uuid4().hex}).status_code == 422
    monkeypatch.delenv("GITHUB_APP_TOKEN")
    r = client.post(f"/tenants/{world.tenant_a}/factory/repos", headers=alice,
                    json={"course_id": str(uuid.uuid4()), "idempotency_key": uuid.uuid4().hex})
    assert r.status_code == 409 and "GITHUB_APP_TOKEN" in r.text
