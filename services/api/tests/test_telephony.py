"""ADR-059: SMS and calls through Twilio, behind every CRM guard; inbound STOP opts out."""

import base64
import hashlib
import hmac
import uuid
from urllib.parse import parse_qs

import httpx
import pytest
from conftest import auth, run_state
from sqlalchemy import select
from test_crm import approve, campaign, lead, new_tenant_contacts, prepare

from jackson_api import connections as C
from jackson_api import crm as crm_mod
from jackson_api.connectors import telephony as T
from jackson_api.crm import address_hash
from jackson_api.models import OutreachMessage, Suppression
from jackson_api.worker import Worker

SID, TOKEN, FROM = "AC" + "1" * 32, "tw-" + "s" * 29, "+12175550000"


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("UNSUBSCRIBE_SECRET", "test-unsubscribe-secret-" + "x" * 16)
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://jackson.example.com")


class FakeTwilio:
    def __init__(self):
        self.sent = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        assert req.headers["authorization"] == "Basic " + base64.b64encode(
            f"{SID}:{TOKEN}".encode()).decode()
        p = req.url.path
        if p == f"/2010-04-01/Accounts/{SID}.json":
            return httpx.Response(200, json={"friendly_name": "Steady", "status": "active",
                                             "type": "Full"})
        if p.endswith("/IncomingPhoneNumbers.json"):
            own = req.url.params["PhoneNumber"] == FROM
            return httpx.Response(200, json={"incoming_phone_numbers": [{}] if own else []})
        form = {k: v[0] for k, v in parse_qs(req.content.decode()).items()}
        if p.endswith("/Messages.json"):
            self.sent.append(("sms", form))
            return httpx.Response(201, json={"sid": f"SM{len(self.sent):032d}"})
        if p.endswith("/Calls.json"):
            self.sent.append(("call", form))
            return httpx.Response(201, json={"sid": f"CA{len(self.sent):032d}"})
        return httpx.Response(404, json={"message": "nope", "code": 20404})


@pytest.fixture()
def twilio(monkeypatch):
    for k, v in {"TWILIO_ACCOUNT_SID": SID, "TWILIO_AUTH_TOKEN": TOKEN,
                 "TWILIO_FROM_NUMBER": FROM}.items():
        monkeypatch.setenv(k, v)
    fake = FakeTwilio()
    mk = lambda: httpx.Client(transport=httpx.MockTransport(fake))  # noqa: E731
    monkeypatch.setattr(T, "_new_client", mk)
    monkeypatch.setattr(C, "_new_client", mk)
    monkeypatch.setattr(crm_mod, "within_quiet_hours", lambda tz, now=None: True)
    return fake


@pytest.fixture()
def alice(make_token, world):
    return auth(make_token, world.alice)


def _consented(client, world, alice, owner_db, channel):
    biz, _ = new_tenant_contacts(client, world, alice, owner_db, n=0)
    c = client.post(f"/tenants/{world.tenant_a}/contacts", headers=alice, json=lead(
        name="Pat Jones", email=None, phone=f"217555{uuid.uuid4().int % 10000:04d}",
        business_id=str(biz))).json()
    client.post(f"/tenants/{world.tenant_a}/contacts/{c['id']}/consent", headers=alice,
                json={"channel": channel, "evidence": "web form checkbox, 2026-11-20"})
    return biz, c


def test_approved_sms_goes_out_through_twilio(client, world, alice, maker, owner_db, drain,
                                              twilio):
    biz, c = _consented(client, world, alice, owner_db, "sms")
    k = campaign(client, world, alice, biz, channel="sms",
                 body_template="Hi {first_name}, spring slots open. Reply STOP to opt out.")
    assert prepare(client, world, alice, maker, owner_db, k["id"])["queued"] == 1
    assert twilio.sent == []  # nothing before a person approves
    run_id = approve(client, world, alice, k["id"])
    Worker(maker, retry_base_seconds=0).run_once()
    run, tasks = run_state(owner_db, run_id)
    assert run.status == "succeeded", tasks[0].error
    assert tasks[0].output["connector"] == "twilio" and tasks[0].output["sent"] == 1
    kind, form = twilio.sent[0]
    assert kind == "sms" and form["From"] == FROM and form["To"] == c["phone"]
    assert form["Body"].startswith("Hi Pat") and "STOP" in form["Body"]
    m = owner_db.scalar(select(OutreachMessage).where(OutreachMessage.campaign_id ==
                                                      uuid.UUID(k["id"])))
    assert m.status == "sent" and m.external_id.startswith("SM")


def test_approved_call_reads_the_script(client, world, alice, maker, owner_db, drain, twilio):
    biz, _ = _consented(client, world, alice, owner_db, "call")
    k = campaign(client, world, alice, biz, channel="call",
                 body_template="Hi {first_name}, this is Steady from Acme & Co, calling "
                               "about spring slots.")
    prepare(client, world, alice, maker, owner_db, k["id"])
    run_id = approve(client, world, alice, k["id"])
    Worker(maker, retry_base_seconds=0).run_once()
    assert run_state(owner_db, run_id)[0].status == "succeeded"
    kind, form = twilio.sent[0]
    assert kind == "call" and form["Twiml"].startswith("<Response><Say>Hi Pat")
    assert "Acme &amp; Co" in form["Twiml"]  # escaped: the script can't inject TwiML


def _sign(url, params):
    data = url + "".join(f"{k}{params[k]}" for k in sorted(params))
    return base64.b64encode(hmac.new(TOKEN.encode(), data.encode(), hashlib.sha1)
                            .digest()).decode()


def test_inbound_stop_suppresses_the_number(client, world, owner_db, twilio):
    url = f"https://jackson.example.com/webhooks/twilio/{world.tenant_a}"
    form = {"AccountSid": SID, "From": "+12175559876", "Body": "Stop", "To": FROM}
    bad = client.post(f"/webhooks/twilio/{world.tenant_a}", data=form,
                      headers={"X-Twilio-Signature": "forged"})
    assert bad.status_code == 403
    hello = {**form, "Body": "hello"}
    r = client.post(f"/webhooks/twilio/{world.tenant_a}", data=hello,
                    headers={"X-Twilio-Signature": _sign(url, hello)})
    assert r.status_code == 200
    assert owner_db.scalar(select(Suppression.id).where(
        Suppression.value_hash == address_hash("+12175559876"))) is None
    r = client.post(f"/webhooks/twilio/{world.tenant_a}", data=form,
                    headers={"X-Twilio-Signature": _sign(url, form)})
    assert r.status_code == 200 and "<Response/>" in r.text
    s = owner_db.scalar(select(Suppression).where(
        Suppression.tenant_id == world.tenant_a,
        Suppression.value_hash == address_hash("+12175559876")))
    assert s is not None and s.channel == "all"


def test_connection_test_sends_nothing(client, world, alice, twilio, monkeypatch):
    t = client.post(f"/tenants/{world.tenant_a}/admin/connectors/sms/test", headers=alice)
    assert t.json()["ok"], t.text
    assert t.json()["result"]["from_number_owned"] is True and TOKEN not in t.text
    assert twilio.sent == []
    monkeypatch.setenv("TWILIO_FROM_NUMBER", "+12175550001")
    t = client.post(f"/tenants/{world.tenant_a}/admin/connectors/sms/test", headers=alice)
    assert not t.json()["ok"] and "not a number on this Twilio account" in t.text


def test_without_keys_sms_stays_mock():
    assert crm_mod.sender_for("sms", "Steady", "").backend == "mock"
    assert T.is_stop(" STOP ") and T.is_stop("unsubscribe.") and not T.is_stop("stop by")
