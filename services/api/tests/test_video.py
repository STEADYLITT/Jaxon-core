"""fal.ai UGC video clips (ADR-036). Request shapes checked against fal.ai's veo3.1 fast API
page; the network is faked. Cost is shown first; nothing is generated before approval."""

import json
import uuid

import httpx
import pytest
from conftest import auth, run_state
from sqlalchemy import select
from test_launches import launch_run, make_launch

from jackson_api.connectors import video
from jackson_api.connectors.video import FalVideo, MockVideo, VideoPending, estimate_cents
from jackson_api.models import Approval, ContentItem, LedgerEntry
from jackson_api.worker import Worker
from jackson_api.worker_errors import NonRetryableError

KEY = "fal-" + "k" * 30  # built at runtime: keeps the repo secret scan clean
BASE = "https://queue.fal.run/fal-ai/veo3.1/requests/req-1"


class FakeFal:
    def __init__(self, statuses=("IN_QUEUE", "IN_PROGRESS", "COMPLETED"), submit_status=200):
        self.calls, self.statuses, self.submit_status = [], list(statuses), submit_status

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if request.method == "POST":
            if self.submit_status != 200:
                return httpx.Response(self.submit_status, json={"detail": "content policy"})
            return httpx.Response(200, json={"request_id": "req-1",
                                             "status_url": f"{BASE}/status",
                                             "response_url": BASE})
        if request.url.path.endswith("/status"):
            return httpx.Response(200, json={"status": self.statuses.pop(0)})
        return httpx.Response(200, json={"video": {"url": "https://v3.fal.media/files/out.mp4"}})

    def submitted(self):
        return [json.loads(r.content) for r in self.calls if r.method == "POST"]


def fal(fake):
    return FalVideo(KEY, "fal-ai/veo3.1/fast",
                    client=httpx.Client(transport=httpx.MockTransport(fake)), sleep=lambda s: None)


@pytest.fixture(autouse=True)
def _defaults(monkeypatch):
    monkeypatch.delenv("FAL_VIDEO_MODEL", raising=False)
    monkeypatch.delenv("FAL_VIDEO_CENTS_PER_SECOND", raising=False)


def test_estimate_uses_the_published_per_second_price(monkeypatch):
    assert estimate_cents(8, "1080p", True) == 120  # $0.15/s with audio
    assert estimate_cents(8, "720p", False) == 80  # $0.10/s
    assert estimate_cents(4, "4k", True) == 140  # $0.35/s
    assert estimate_cents(6, "4k", False) == 180  # $0.30/s
    with pytest.raises(NonRetryableError, match="duration"):
        estimate_cents(5, "1080p", True)
    monkeypatch.setenv("FAL_VIDEO_MODEL", "fal-ai/kling-video")
    with pytest.raises(NonRetryableError, match="FAL_VIDEO_CENTS_PER_SECOND"):
        estimate_cents(8, "1080p", True)  # unknown price: refuse rather than guess
    monkeypatch.setenv("FAL_VIDEO_CENTS_PER_SECOND", "7")
    assert estimate_cents(8, "1080p", True) == 56


def test_submit_and_wait_match_the_documented_queue():
    fake = FakeFal()
    api = fal(fake)
    job = api.submit("A hand grips a magnetic tool holder", 8, "9:16", "1080p", True)
    req = fake.calls[0]
    assert str(req.url) == "https://queue.fal.run/fal-ai/veo3.1/fast"
    assert req.headers["Authorization"] == f"Key {KEY}"
    assert fake.submitted() == [{"prompt": "A hand grips a magnetic tool holder",
                                 "duration": "8s", "aspect_ratio": "9:16",
                                 "resolution": "1080p", "generate_audio": True}]
    assert api.wait(job) == "https://v3.fal.media/files/out.mp4"
    assert [str(r.url) for r in fake.calls[1:]] == [f"{BASE}/status"] * 3 + [BASE]


def test_slow_failed_and_refused_jobs():
    with pytest.raises(VideoPending):  # retried later; the same request is resumed
        fal(FakeFal(statuses=["IN_QUEUE"] * 3)).wait(
            {"request_id": "req-1", "status_url": f"{BASE}/status", "response_url": BASE},
            timeout_s=0)
    with pytest.raises(NonRetryableError, match="video failed"):
        fal(FakeFal(statuses=["FAILED"])).wait(
            {"request_id": "req-1", "status_url": f"{BASE}/status", "response_url": BASE})
    with pytest.raises(NonRetryableError, match="content policy"):
        fal(FakeFal(submit_status=422)).submit("x", 8, "9:16", "1080p", True)
    with pytest.raises(NonRetryableError, match="aspect ratio"):
        fal(FakeFal()).submit("x", 8, "1:1", "1080p", True)


def test_mock_until_key(monkeypatch):
    monkeypatch.delenv("FAL_KEY", raising=False)
    assert isinstance(video.video_backend(), MockVideo)
    monkeypatch.setenv("FAL_KEY", KEY)
    assert isinstance(video.video_backend(), FalVideo)


# --- End to end: cost first, then approval, then generation --------------------------------------

@pytest.fixture()
def alice(make_token, world):
    return auth(make_token, world.alice)


@pytest.fixture()
def live(monkeypatch):
    fake = FakeFal()
    monkeypatch.setenv("FAL_KEY", KEY)
    monkeypatch.setattr(video, "_new_client",
                        lambda: httpx.Client(transport=httpx.MockTransport(fake)))
    monkeypatch.setattr(video.time, "sleep", lambda s: None)
    return fake


def item(owner_db, world, **kw):
    row = {"tenant_id": world.tenant_a, "business_id": world.biz_a, "channel": "tiktok",
           "kind": "post", "title": "Mag Grip", "body": "Never drop a bit again.",
           "hashtags": ["tools"], "status": "in_review",
           "review": {"passed": True, "problems": []}, **kw}
    c = ContentItem(**row)
    owner_db.add(c)
    owner_db.commit()
    return c


def ask(client, world, alice, cid, **kw):
    r = client.post(f"/tenants/{world.tenant_a}/content/{cid}/video", headers=alice,
                    json={"idempotency_key": uuid.uuid4().hex, **kw})
    assert r.status_code == 202, r.text
    return r.json()


def test_estimate_endpoint_shows_cost_first(client, world, alice, live):
    r = client.get(f"/tenants/{world.tenant_a}/video/estimate?duration_s=8&resolution=1080p",
                   headers=alice)
    assert r.status_code == 200, r.text
    r = r.json()
    assert r["estimate_cents"] == 120 and r["estimate_usd"] == "1.20"
    assert r["connector"] == "fal" and r["model"] == "fal-ai/veo3.1/fast"
    assert client.get(f"/tenants/{world.tenant_a}/video/estimate?duration_s=5",
                      headers=alice).status_code == 422


def test_owner_approves_the_cost_then_the_clip_is_made(client, world, alice, maker, owner_db,
                                                       drain, live):
    src = item(owner_db, world)
    out = ask(client, world, alice, src.id, prompt="Close-up of the grip snapping on")
    assert out["estimate_cents"] == 120
    Worker(maker, retry_base_seconds=0).run_once()
    run, tasks = run_state(owner_db, out["run_id"])
    assert tasks[0].status == "waiting_approval"
    assert live.calls == []  # nothing generated or paid before approval
    ap = owner_db.scalar(select(Approval).where(Approval.run_id == uuid.UUID(out["run_id"])))
    assert ap.category == "spend" and ap.amount_cents == 120 and "$1.20" in ap.description
    r = client.post(f"/tenants/{world.tenant_a}/approvals/{ap.id}/decision", headers=alice,
                    json={"decision": "approve"})
    assert r.status_code == 200, r.text
    Worker(maker, retry_base_seconds=0).run_once()
    run, tasks = run_state(owner_db, out["run_id"])
    assert run.status == "succeeded", tasks[0].error
    res = tasks[0].output
    assert res["cost_cents"] == 120 and res["connector"] == "fal"
    assert live.submitted()[0]["prompt"] == "Close-up of the grip snapping on"
    owner_db.expire_all()
    clip = owner_db.get(ContentItem, uuid.UUID(res["content_id"]))
    assert clip.status == "in_review" and clip.approved_by is None  # posting needs approval
    assert clip.assets[0]["url"] == "https://v3.fal.media/files/out.mp4"
    assert clip.assets[0]["rights"] == "generated" and clip.assets[0]["kind"] == "video"
    assert owner_db.get(ContentItem, src.id).assets == []  # the original is untouched
    kinds = {e.kind: e.amount_cents for e in owner_db.scalars(select(LedgerEntry).where(
        LedgerEntry.run_id == uuid.UUID(out["run_id"])))}
    assert kinds == {"reserve": 120, "commit": 120}


def test_rejected_cost_means_no_video(client, world, alice, maker, owner_db, drain, live):
    out = ask(client, world, alice, item(owner_db, world).id)
    Worker(maker, retry_base_seconds=0).run_once()
    ap = owner_db.scalar(select(Approval).where(Approval.run_id == uuid.UUID(out["run_id"])))
    client.post(f"/tenants/{world.tenant_a}/approvals/{ap.id}/decision", headers=alice,
                json={"decision": "reject"})
    Worker(maker, retry_base_seconds=0).run_once()
    assert run_state(owner_db, out["run_id"])[0].status == "failed"
    assert live.calls == []


def test_inside_a_launch_the_budget_is_the_approval(client, world, alice, maker, owner_db,
                                                    drain, live):
    la = make_launch(client, world, alice)
    src = item(owner_db, world, launch_id=uuid.UUID(la["id"]))
    rid = launch_run(owner_db, world, la["id"], kind="content_video",
                     input={"content_id": str(src.id), "duration_s": 8})
    Worker(maker, retry_base_seconds=0).run_once()
    run, tasks = run_state(owner_db, rid)
    assert run.status == "succeeded", tasks[0].error  # within the launch's caps: no inbox
    owner_db.expire_all()
    clip = owner_db.get(ContentItem, uuid.UUID(tasks[0].output["content_id"]))
    assert str(clip.launch_id) == la["id"]


def test_resumes_the_same_fal_request_after_a_retry(client, world, alice, maker, owner_db,
                                                    drain, live, monkeypatch):
    live.statuses = ["IN_PROGRESS", "COMPLETED"]
    monkeypatch.setenv("VIDEO_WAIT_SECONDS", "0")
    la = make_launch(client, world, alice)
    src = item(owner_db, world, launch_id=uuid.UUID(la["id"]))
    rid = launch_run(owner_db, world, la["id"], kind="content_video",
                     input={"content_id": str(src.id)})
    Worker(maker, retry_base_seconds=0).run_once()  # still rendering -> retried later
    assert run_state(owner_db, rid)[1][0].status != "succeeded"
    Worker(maker, retry_base_seconds=0).run_once()
    run, tasks = run_state(owner_db, rid)
    assert run.status == "succeeded", tasks[0].error
    assert len(live.submitted()) == 1  # paid once, not twice


def test_mock_makes_no_postable_video(client, world, alice, maker, owner_db, drain,
                                      monkeypatch):
    monkeypatch.delenv("FAL_KEY", raising=False)
    la = make_launch(client, world, alice)
    src = item(owner_db, world, launch_id=uuid.UUID(la["id"]))
    rid = launch_run(owner_db, world, la["id"], kind="content_video",
                     input={"content_id": str(src.id)})
    Worker(maker, retry_base_seconds=0).run_once()
    run, tasks = run_state(owner_db, rid)
    assert run.status == "succeeded", tasks[0].error
    assert tasks[0].output["connector"] == "mock" and tasks[0].output["cost_cents"] == 0
    assert tasks[0].output["video_url"].startswith("mock://")


def test_connector_panel_shows_live(client, world, alice, live):
    d = client.get(f"/tenants/{world.tenant_a}/dashboard", headers=alice).json()
    assert {c["id"]: c["mode"] for c in d["connectors"]}["video"] == "live"


def test_console_video_button_shows_the_cost_and_asks_for_approval(client, world, make_token,
                                                                  maker, owner_db, drain, live):
    from test_console_ui import console

    item(owner_db, world, title="Console clip")
    with console(client, world, make_token(world.alice)) as (page, errors):
        btn = page.wait_for_selector("#content li:has-text('Console clip') button.video")
        assert btn.text_content() == "Make 8s video ($1.20)"
        with page.expect_response(lambda r: r.url.endswith("/video") and
                                  r.request.method == "POST") as resp:
            btn.click()
        assert resp.value.status == 202
        assert errors == []
    Worker(maker, retry_base_seconds=0).run_once()
    ap = owner_db.scalar(select(Approval).where(Approval.description.like("%Console clip%")))
    assert ap.status == "pending" and ap.amount_cents == 120 and "$1.20" in ap.description
    assert live.calls == []  # nothing generated until the owner approves in the inbox
