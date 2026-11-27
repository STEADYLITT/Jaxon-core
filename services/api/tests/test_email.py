"""Resend email connector (ADR-035). Request shapes checked against resend.com/docs; the
network is faked. Campaigns still need the owner's approval or a covering Launch approval."""

import base64
import hashlib
import hmac
import json
import time
import uuid

import httpx
import pytest
from conftest import auth, run_state
from sqlalchemy import select
from test_crm import approve, campaign, new_tenant_contacts, prepare
from test_launches import launch_run, make_launch

from jackson_api.connectors import email
from jackson_api.connectors.email import ResendSender, verify_webhook
from jackson_api.crm import address_hash
from jackson_api.models import LaunchSignal, OutreachMessage, Suppression
from jackson_api.worker import Worker
from jackson_api.worker_errors import NonRetryableError

KEY = "re_" + "k" * 30  # built at runtime: keeps the repo secret scan clean
FROM = "Steady <steady@steadytradesuniversity.com>"
WHSEC = "whsec_" + base64.b64encode(b"w" * 24).decode()


class FakeResend:
    def __init__(self, status=200, body=None):
        self.calls, self.status, self.body = [], status, body

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request, json.loads(request.content)))
        return httpx.Response(self.status, json=self.body if self.body is not None
                              else {"id": f"em-{len(self.calls)}"})


def api(fake, sender=FROM):
    return ResendSender(KEY, sender, client=httpx.Client(transport=httpx.MockTransport(fake)))


def test_send_matches_the_documented_request():
    fake = FakeResend()
    out = api(fake).send(to="bob@x.example", subject="Hi", text="Body",
                         from_addr=FROM, unsubscribe_url="https://j.example/u/tok",
                         idempotency_key="msg-1")
    req, body = fake.calls[0]
    assert str(req.url) == "https://api.resend.com/emails"
    assert req.headers["Authorization"] == f"Bearer {KEY}"
    assert req.headers["Idempotency-Key"] == "msg-1"
    assert body["from"] == FROM and body["to"] == ["bob@x.example"]
    assert body["subject"] == "Hi" and body["text"] == "Body"
    assert body["headers"] == {"List-Unsubscribe": "<https://j.example/u/tok>",
                               "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"}
    assert out == "em-1"


def test_only_the_verified_domain_may_send():
    r = api(FakeResend())
    assert r.sender_for("Steady", "steady@steadytradesuniversity.com") == FROM
    assert r.sender_for("", "") == FROM  # campaign without a sender uses RESEND_FROM
    assert r.sender_for('Ev"il <x>', "hi@steadytradesuniversity.com") == \
        "Evil x <hi@steadytradesuniversity.com>"
    with pytest.raises(NonRetryableError, match="verified sending domain"):
        r.sender_for("Steady", "steady@gmail.com")


def test_refusals_and_rate_limits():
    with pytest.raises(NonRetryableError, match="domain is not verified"):
        api(FakeResend(status=403, body={"message": "The domain is not verified"})).send(
            to="a@x.example", subject="s", text="t", from_addr=FROM,
            unsubscribe_url="https://u", idempotency_key="k")
    with pytest.raises(httpx.HTTPStatusError):  # 429 / 5xx are retried by the worker
        api(FakeResend(status=429, body={})).send(
            to="a@x.example", subject="s", text="t", from_addr=FROM,
            unsubscribe_url="https://u", idempotency_key="k")


def test_mock_until_key_and_sender_exist(monkeypatch):
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.delenv("RESEND_FROM", raising=False)
    assert email.email_backend() is None
    monkeypatch.setenv("RESEND_API_KEY", KEY)
    assert email.email_backend() is None
    monkeypatch.setenv("RESEND_FROM", FROM)
    assert isinstance(email.email_backend(), ResendSender)


def sign(body: bytes, msg_id="msg_1", ts=None, secret=WHSEC) -> dict:
    ts = str(int(time.time()) if ts is None else ts)
    key = base64.b64decode(secret.split("_", 1)[1])
    sig = base64.b64encode(hmac.new(key, f"{msg_id}.{ts}.".encode() + body,
                                    hashlib.sha256).digest()).decode()
    return {"svix-id": msg_id, "svix-timestamp": ts, "svix-signature": f"v1,{sig}"}


def test_webhook_signatures():
    body = b'{"type":"email.bounced"}'
    assert verify_webhook(WHSEC, sign(body), body)
    assert not verify_webhook(WHSEC, sign(body), body + b" ")  # tampered
    assert not verify_webhook(WHSEC, sign(body, ts=int(time.time()) - 3600), body)  # stale
    other = "whsec_" + base64.b64encode(b"z" * 24).decode()
    assert not verify_webhook(WHSEC, sign(body, secret=other), body)
    assert not verify_webhook(WHSEC, {}, body)


# --- End to end -----------------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _secrets(monkeypatch):
    monkeypatch.setenv("UNSUBSCRIBE_SECRET", "test-unsubscribe-secret-" + "x" * 16)
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://jackson.example.com")


@pytest.fixture()
def alice(make_token, world):
    return auth(make_token, world.alice)


@pytest.fixture()
def live(monkeypatch):
    fake = FakeResend()
    monkeypatch.setenv("RESEND_API_KEY", KEY)
    monkeypatch.setenv("RESEND_FROM", FROM)
    monkeypatch.setenv("RESEND_WEBHOOK_SECRET", WHSEC)
    monkeypatch.setattr(email, "_new_client",
                        lambda: httpx.Client(transport=httpx.MockTransport(fake)))
    return fake


VERIFIED = {"sender_email": "steady@steadytradesuniversity.com"}


def test_approved_campaign_goes_out_through_resend(client, world, alice, maker, owner_db,
                                                   drain, live):
    biz, _ = new_tenant_contacts(client, world, alice, owner_db, n=2)
    k = campaign(client, world, alice, biz, **VERIFIED)
    prepare(client, world, alice, maker, owner_db, k["id"])
    run_id = approve(client, world, alice, k["id"])
    Worker(maker, retry_base_seconds=0).run_once()
    run, tasks = run_state(owner_db, run_id)
    assert run.status == "succeeded", tasks[0].error
    assert tasks[0].output["sent"] == 2 and tasks[0].output["connector"] == "resend"
    msgs = owner_db.scalars(select(OutreachMessage).where(
        OutreachMessage.campaign_id == uuid.UUID(k["id"]))).all()
    assert sorted(m.external_id for m in msgs) == ["em-1", "em-2"]
    keys = {req.headers["Idempotency-Key"] for req, _ in live.calls}
    assert keys == {f"msg-{m.id}" for m in msgs}  # one key per message: retries never resend
    for _req, body in live.calls:
        assert body["from"] == FROM
        assert body["headers"]["List-Unsubscribe"].startswith("<https://jackson.example.com/u/")
        assert body["headers"]["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"


def test_unverified_sender_never_reaches_resend(client, world, alice, maker, owner_db, drain,
                                                live):
    biz, _ = new_tenant_contacts(client, world, alice, owner_db, n=1)
    k = campaign(client, world, alice, biz)  # steady@example.com: not the verified domain
    prepare(client, world, alice, maker, owner_db, k["id"])
    run_id = approve(client, world, alice, k["id"])
    Worker(maker, retry_base_seconds=0).run_once()
    run, tasks = run_state(owner_db, run_id)
    assert run.status == "failed" and "verified sending domain" in tasks[0].error
    assert live.calls == []


def test_unapproved_campaign_never_reaches_resend(client, world, alice, maker, owner_db, drain,
                                                  live):
    from sqlalchemy import text

    from jackson_api.runs import create_run

    biz, _ = new_tenant_contacts(client, world, alice, owner_db, n=1)
    k = campaign(client, world, alice, biz, **VERIFIED)
    prepare(client, world, alice, maker, owner_db, k["id"])  # prepared, NOT approved
    uid = owner_db.scalar(text("SELECT id FROM users WHERE auth_subject = :s"),
                          {"s": world.alice})
    run, _ = create_run(owner_db, world.tenant_a, uid, {
        "type": "BUILD", "objective": "sneak a send", "idempotency_key": uuid.uuid4().hex},
        first_task={"kind": "outreach_send", "title": "send", "tool_scope": [],
                    "input": {"campaign_id": k["id"]}})
    rid = str(run.id)
    owner_db.commit()
    Worker(maker, retry_base_seconds=0).run_once()
    assert run_state(owner_db, rid)[0].status in ("failed", "waiting_approval")
    assert live.calls == []


def launch_campaign_send(client, world, alice, maker, owner_db):
    la = make_launch(client, world, alice)
    client.post(f"/tenants/{world.tenant_a}/contacts", headers=alice, json={
        "name": "Vip Buyer", "email": f"v{uuid.uuid4().hex[:6]}@x.example",
        "business_id": str(world.biz_a), "source_type": "existing_customer",
        "source_ref": "shopify customer", "lawful_basis": "existing_relationship"})
    addr = "Steady Ventures, 100 Main St, Springfield, IL 62701"
    k = client.post(f"/tenants/{world.tenant_a}/campaigns", headers=alice, json={
        "name": "VIP drop", "channel": "email", "sender_name": "Steady", **VERIFIED,
        "postal_address": addr, "subject_template": "New grip for {first_name}",
        "body_template": "Hi {first_name}\n\n{postal_address}\nUnsubscribe: {unsubscribe_url}",
        "audience": {"list": "vip"}, "business_id": str(world.biz_a),
        "launch_id": la["id"]}).json()
    client.post(f"/tenants/{world.tenant_a}/campaigns/{k['id']}/prepare", headers=alice,
                json={"idempotency_key": uuid.uuid4().hex})
    Worker(maker, retry_base_seconds=0).run_once()
    rid = launch_run(owner_db, world, la["id"], kind="outreach_send",
                     input={"campaign_id": k["id"]})
    Worker(maker, retry_base_seconds=0).run_once()
    run, tasks = run_state(owner_db, rid)
    assert run.status == "succeeded", tasks[0].error
    return la, k


def signals(owner_db, launch_id):
    return sorted((s.kind, s.source) for s in owner_db.scalars(select(LaunchSignal).where(
        LaunchSignal.launch_id == uuid.UUID(launch_id))))


def post_event(client, world, event, headers=None):
    body = json.dumps(event).encode()
    return client.post(f"/webhooks/resend/{world.tenant_a}", content=body,
                       headers=headers if headers is not None else sign(body))


def test_bounce_opts_out_and_feeds_the_launch(client, world, alice, maker, owner_db, drain,
                                              live):
    la, k = launch_campaign_send(client, world, alice, maker, owner_db)
    assert signals(owner_db, la["id"]) == [("send", "resend")]
    m = owner_db.scalar(select(OutreachMessage).where(
        OutreachMessage.campaign_id == uuid.UUID(k["id"])))
    event = {"type": "email.bounced", "created_at": "2026-11-27T00:00:00Z",
             "data": {"email_id": m.external_id, "to": [m.to_address],
                      "bounce": {"type": "Permanent"}}}
    r = post_event(client, world, event)
    assert r.status_code == 200 and r.json()["recorded"] == "bounce"
    assert post_event(client, world, event).status_code == 200  # Resend retries: no double
    assert signals(owner_db, la["id"]) == [("bounce", "resend"), ("send", "resend")]
    sup = owner_db.scalars(select(Suppression).where(
        Suppression.tenant_id == world.tenant_a,
        Suppression.value_hash == address_hash(m.to_address))).all()
    assert [(s.channel, s.reason) for s in sup] == [("email", "bounce")]
    complaint = {**event, "type": "email.complained"}
    assert post_event(client, world, complaint).json()["recorded"] == "complaint"
    assert ("complaint", "resend") in signals(owner_db, la["id"])


def test_webhook_refuses_unsigned_and_ignores_unknown(client, world, live, monkeypatch):
    event = {"type": "email.bounced", "data": {"email_id": "nope"}}
    assert post_event(client, world, event, headers={}).status_code == 401
    body = json.dumps(event).encode()
    forged = sign(body, secret="whsec_" + base64.b64encode(b"q" * 24).decode())
    assert post_event(client, world, event, headers=forged).status_code == 401
    assert post_event(client, world, event).json()["ignored"] is True  # unknown email id
    assert post_event(client, world, {"type": "email.opened", "data": {}}).json()["ignored"]
    monkeypatch.delenv("RESEND_WEBHOOK_SECRET")
    assert post_event(client, world, event).status_code == 503


def test_connector_panel_shows_live(client, world, alice, live):
    d = client.get(f"/tenants/{world.tenant_a}/dashboard", headers=alice).json()
    row = {c["id"]: c for c in d["connectors"]}["outreach"]
    assert row["mode"] == "live"
    assert row["setup"] == ("Resend webhook URL: https://jackson.example.com/webhooks/resend/"
                            f"{world.tenant_a}")
