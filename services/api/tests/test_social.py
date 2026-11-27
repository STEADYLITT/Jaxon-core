"""Upload-Post social connector (ADR-034). Request shapes checked against
docs.upload-post.com; the network is faked."""

import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from conftest import auth, events, run_state
from sqlalchemy import update

from jackson_api.connectors import social
from jackson_api.connectors.social import MockSocial, UploadPost, social_backend
from jackson_api.models import Task
from jackson_api.worker import Worker
from jackson_api.worker_errors import NonRetryableError

KEY = "up-" + "k" * 30  # built at runtime: keeps the repo secret scan clean


class FakeUploadPost:
    def __init__(self, status=200, body=None):
        self.calls, self.status, self.body = [], status, body

    def __call__(self, request: httpx.Request) -> httpx.Response:
        form = httpx.QueryParams(request.content.decode())
        self.calls.append((request, form))
        platform = form.get("platform[]")
        return httpx.Response(self.status, json=self.body or {
            "success": True, "results": {platform: {
                "success": True, "post_id": "p-123", "url": f"https://{platform}.example/p/123"}}})


def api(fake):
    return UploadPost(KEY, "steady", client=httpx.Client(transport=httpx.MockTransport(fake)))


def test_text_post_matches_the_documented_request():
    fake = FakeUploadPost()
    out = api(fake).publish("x", "Hello #trading", [], idempotency_key="content-1",
                            ai_generated=False)
    req, form = fake.calls[0]
    assert str(req.url) == "https://api.upload-post.com/api/upload_text"
    assert req.headers["Authorization"] == f"Apikey {KEY}"
    assert req.headers["Idempotency-Key"] == "content-1"
    assert form.get("user") == "steady" and form.get("platform[]") == "x"
    assert form.get("title") == "Hello #trading"
    assert out == {"post_id": "p-123", "url": "https://x.example/p/123", "platform": "x",
                   "connector": "upload-post"}


def test_photos_and_video_use_their_endpoints_and_ai_label():
    fake = FakeUploadPost()
    api(fake).publish("instagram", "Grip", [{"url": "https://cdn.example/a.jpg"},
                                            {"url": "https://cdn.example/b.png"}],
                      idempotency_key="c-2", ai_generated=True)
    req, form = fake.calls[0]
    assert req.url.path == "/api/upload_photos"
    assert form.get_list("photos[]") == ["https://cdn.example/a.jpg",
                                         "https://cdn.example/b.png"]
    assert form.get("is_ai_generated") == "true"
    api(fake).publish("tiktok", "Clip", [{"url": "https://cdn.example/v.mp4"}],
                      idempotency_key="c-3", ai_generated=False)
    req, form = fake.calls[1]
    assert req.url.path == "/api/upload" and form.get("video") == "https://cdn.example/v.mp4"
    assert form.get("platform[]") == "tiktok" and "is_ai_generated" not in form


@pytest.mark.parametrize("channel,assets,msg", [
    ("instagram", [], "needs an image or a video"),
    ("email", [], "isn't a social channel"),
    ("instagram", [{"url": "http://insecure.example/a.jpg"}], "needs an image"),
])
def test_bad_requests_are_refused_before_calling(channel, assets, msg):
    fake = FakeUploadPost()
    with pytest.raises(NonRetryableError, match=msg):
        api(fake).publish(channel, "t", assets, idempotency_key="k", ai_generated=False)
    assert fake.calls == []


def test_refusals_and_rate_limits():
    refused = FakeUploadPost(body={"success": True, "results": {"x": {
        "success": False, "error": "duplicate content"}}})
    with pytest.raises(NonRetryableError, match="duplicate content"):
        api(refused).publish("x", "t", [], idempotency_key="k", ai_generated=False)
    limit = FakeUploadPost(status=200, body={"success": False,
                                             "message": "This upload would exceed your "
                                                        "monthly limit."})
    with pytest.raises(NonRetryableError, match="monthly limit"):
        api(limit).publish("x", "t", [], idempotency_key="k", ai_generated=False)
    with pytest.raises(httpx.HTTPStatusError):  # 429 is retried by the worker
        api(FakeUploadPost(status=429)).publish("x", "t", [], idempotency_key="k",
                                                ai_generated=False)


def test_mock_until_both_settings_exist(monkeypatch):
    monkeypatch.delenv("UPLOADPOST_API_KEY", raising=False)
    monkeypatch.delenv("UPLOADPOST_USER", raising=False)
    assert isinstance(social_backend(), MockSocial)
    monkeypatch.setenv("UPLOADPOST_API_KEY", KEY)
    assert isinstance(social_backend(), MockSocial)
    monkeypatch.setenv("UPLOADPOST_USER", "steady")
    assert isinstance(social_backend(), UploadPost)


# --- End to end: still behind the approval gate --------------------------------------------------

@pytest.fixture()
def live(monkeypatch):
    fake = FakeUploadPost()
    monkeypatch.setenv("UPLOADPOST_API_KEY", KEY)
    monkeypatch.setenv("UPLOADPOST_USER", "steady")
    monkeypatch.setattr(social, "_new_client",
                        lambda: httpx.Client(transport=httpx.MockTransport(fake)))
    return fake


def content(client, world, alice, **kw):
    body = {"channel": "x", "title": "Tip", "body": "Size small, live long.",
            "hashtags": ["trading"], **kw}
    r = client.post(f"/tenants/{world.tenant_a}/content", json=body, headers=alice)
    assert r.status_code == 201, r.text
    return r.json()


def test_approved_scheduled_post_goes_out_through_upload_post(client, world, make_token, maker,
                                                              owner_db, drain, live):
    alice = auth(make_token, world.alice)
    c = content(client, world, alice)
    client.post(f"/tenants/{world.tenant_a}/content/{c['id']}/approve", headers=alice)
    r = client.post(f"/tenants/{world.tenant_a}/content/{c['id']}/schedule", headers=alice,
                    json={"scheduled_for": datetime.now(UTC).isoformat(),
                          "idempotency_key": uuid.uuid4().hex})
    run_id = r.json()["run_id"]
    owner_db.execute(update(Task).where(Task.run_id == uuid.UUID(run_id))
                     .values(next_attempt_at=datetime.now(UTC) - timedelta(seconds=1)))
    owner_db.commit()
    Worker(maker, retry_base_seconds=0).run_once()
    run, tasks = run_state(owner_db, run_id)
    assert run.status == "succeeded", tasks[0].error
    assert tasks[0].output["connector"] == "upload-post"
    assert tasks[0].output["url"] == "https://x.example/p/123"
    assert live.calls[0][1].get("title") == "Size small, live long.\n\n#trading"
    assert live.calls[0][0].headers["Idempotency-Key"] == f"content-{c['id']}"
    got = client.get(f"/tenants/{world.tenant_a}/content/{c['id']}", headers=alice).json()
    assert got["status"] == "published" and got["external_post_id"] == "p-123"
    assert "content.published" in events(owner_db, run_id)


def test_unapproved_content_never_reaches_upload_post(client, world, make_token, maker,
                                                      owner_db, drain, live):
    from sqlalchemy import text

    from jackson_api.runs import create_run

    alice = auth(make_token, world.alice)
    c = content(client, world, alice)  # reviewed but NOT approved
    uid = owner_db.scalar(text("SELECT id FROM users WHERE auth_subject = :s"),
                          {"s": world.alice})
    run, _ = create_run(owner_db, world.tenant_a, uid, {
        "type": "BUILD", "objective": "sneak a post", "idempotency_key": uuid.uuid4().hex},
        first_task={"kind": "content_publish", "title": "post", "tool_scope": [],
                    "input": {"content_id": c["id"]}})
    rid = str(run.id)
    owner_db.commit()
    Worker(maker, retry_base_seconds=0).run_once()
    assert run_state(owner_db, rid)[0].status == "failed"
    assert live.calls == []


def test_connector_panel_shows_live(client, world, make_token, live):
    d = client.get(f"/tenants/{world.tenant_a}/dashboard",
                   headers=auth(make_token, world.alice)).json()
    assert {c["id"]: c["mode"] for c in d["connectors"]}["social"] == "live"
