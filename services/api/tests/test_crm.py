"""Phase 18: CRM and compliant outreach (spec vol. 03)."""

import re
import uuid
from datetime import UTC, datetime

import pytest
from conftest import auth, events, run_state
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError

from jackson_api import crm as crm_mod
from jackson_api.crm import (
    dedupe_key,
    norm_email,
    norm_phone,
    read_unsubscribe_token,
    review_message,
    score_lead,
    unsubscribe_token,
    within_quiet_hours,
)
from jackson_api.models import Business, OutreachCampaign, OutreachMessage, Suppression
from jackson_api.worker import Worker

SECRET = "test-unsubscribe-secret-" + "x" * 16
ADDRESS = "Steady Ventures, 100 Main St, Springfield, IL 62701"


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    monkeypatch.setenv("UNSUBSCRIBE_SECRET", SECRET)
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://jackson.example.com")


@pytest.fixture()
def alice(make_token, world):
    return auth(make_token, world.alice)


# --- Pure rules ---------------------------------------------------------------------------------

def test_normalisation_and_dedupe():
    assert norm_email(" Bob@Example.COM ") == "bob@example.com"
    assert norm_email("not-an-email") is None
    assert norm_phone("(217) 555-0101") == "+12175550101"
    assert norm_phone("12") is None
    assert dedupe_key("Bob", "Acme", "bob@example.com", None) == "email:bob@example.com"
    assert dedupe_key("Bob  Smith", "ACME, Inc", None, None) == "name:bob smith acme inc"


def test_lead_score_is_transparent():
    s = score_lead({"source_type": "inbound", "email": "a@b.co", "company": "Acme",
                    "industry": "Electrical", "region": "us-il"},
                   {"industries": ["electrical"], "regions": ["US-IL"]})
    assert s["score"] == 30 + 15 + 10 + 20 + 15
    assert s["parts"]["has_phone"] == 0


CAMP = {"channel": "email", "sender_name": "Steady", "sender_email": "steady@example.com",
        "postal_address": ADDRESS}
GOOD_BODY = f"Hi Bob,\n\nQuick note.\n\n{ADDRESS}\nUnsubscribe: https://x.example/u/abc"


def test_compliant_email_passes_review():
    assert review_message(CAMP, {"email": "b@x.co"}, "Quick question", GOOD_BODY,
                          False)["passed"]


@pytest.mark.parametrize("camp,contact,subject,body,suppressed,needle", [
    (CAMP, {"email": "b@x.co"}, "Hi", GOOD_BODY, True, "opted out"),
    ({**CAMP, "postal_address": ""}, {"email": "b@x.co"}, "Hi", GOOD_BODY, False, "postal"),
    (CAMP, {"email": "b@x.co"}, "Hi", GOOD_BODY.replace("/u/abc", ""), False, "unsubscribe"),
    (CAMP, {"email": "b@x.co"}, "RE: our call", GOOD_BODY, False, "reply or forward"),
    (CAMP, {"email": "b@x.co"}, "Hi", "Guaranteed results! " + GOOD_BODY, False, "guaranteed"),
    ({**CAMP, "sender_email": "nope"}, {"email": "b@x.co"}, "Hi", GOOD_BODY, False, "sender"),
    ({**CAMP, "channel": "sms"}, {"phone": "+12175550101"}, "", "Hi! Reply STOP to opt out",
     False, "consent"),
    ({**CAMP, "channel": "sms"}, {"phone": "+12175550101", "sms_consent_at": "x"}, "",
     "Hi there", False, "opt out"),
    ({**CAMP, "channel": "call"}, {"phone": "+12175550101"}, "", "script", False, "consent"),
])
def test_review_blocks_non_compliant_messages(camp, contact, subject, body, suppressed, needle):
    rv = review_message(camp, contact, subject, body, suppressed)
    assert not rv["passed"] and any(needle in p for p in rv["problems"]), rv["problems"]


def test_quiet_hours_use_the_recipients_time_zone():
    noon_utc = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
    assert not within_quiet_hours("America/Chicago", noon_utc)  # 07:00 CDT: too early
    assert not within_quiet_hours("Asia/Tokyo", noon_utc)  # 21:00
    assert within_quiet_hours("Europe/London", noon_utc)  # 13:00
    assert not within_quiet_hours("Not/AZone", noon_utc)


def test_unsubscribe_tokens_are_signed():
    t = uuid.uuid4()
    tok = unsubscribe_token(t, "email", "bob@example.com")
    tenant, channel, h = read_unsubscribe_token(tok)
    assert (tenant, channel) == (t, "email") and len(h) == 64
    forged = tok[:-3] + ("AAA" if tok[-3:] != "AAA" else "BBB")
    assert read_unsubscribe_token(forged) is None
    assert read_unsubscribe_token("garbage") is None


# --- API ----------------------------------------------------------------------------------------

def lead(**kw):
    base = {"name": "Bob Smith", "company": "Acme Electric", "email": "bob@acme.example",
            "source_type": "inbound", "source_ref": "website contact form 2026-11-20",
            "lawful_basis": "consent", "industry": "electrical", "region": "US-IL",
            "timezone": "America/Chicago"}
    base.update(kw)
    return base


def test_leads_need_a_permitted_source_and_lawful_basis(client, world, alice):
    url = f"/tenants/{world.tenant_a}/contacts"
    assert client.post(url, headers=alice, json=lead(source_type="purchased_list")) \
        .status_code == 422
    assert client.post(url, headers=alice, json=lead(lawful_basis="none")).status_code == 422
    assert client.post(url, headers=alice, json=lead(source_ref="")).status_code == 422
    assert client.post(url, headers=alice, json=lead(email="bad")).status_code == 422
    r = client.post(url, headers=alice, json=lead(email=f"{uuid.uuid4().hex[:8]}@x.example"))
    assert r.status_code == 201 and r.json()["score"] > 0 and r.json()["stage"] == "new"


def test_import_dedupes_scores_and_reports_rejects(client, world, alice):
    tag = uuid.uuid4().hex[:6]
    rows = [lead(email=f"a{tag}@x.example"), lead(email=f"A{tag}@X.example"),  # duplicate
            lead(email=None, phone="217-555-0101", name=f"Phone {tag}"),
            lead(email="broken"), lead(email=f"b{tag}@x.example", source_type="event")]
    r = client.post(f"/tenants/{world.tenant_a}/contacts/import", headers=alice,
                    json={"contacts": rows, "icp": {"industries": ["electrical"]}}).json()
    assert (r["created"], r["duplicates"]) == (3, 1) and r["rejected"][0]["row"] == 3
    again = client.post(f"/tenants/{world.tenant_a}/contacts/import", headers=alice,
                        json={"contacts": rows[:1]}).json()
    assert again["duplicates"] == 1 and again["created"] == 0


def test_stage_tracking_is_audited(client, world, alice, owner_db):
    c = client.post(f"/tenants/{world.tenant_a}/contacts", headers=alice,
                    json=lead(email=f"{uuid.uuid4().hex[:8]}@x.example")).json()
    r = client.patch(f"/tenants/{world.tenant_a}/contacts/{c['id']}", headers=alice,
                     json={"stage": "meeting"})
    assert r.json()["stage"] == "meeting"
    n = owner_db.scalar(text("SELECT count(*) FROM audit_events WHERE entity_id = :i AND "
                             "action = 'contact.stage_changed'"), {"i": c["id"]})
    assert n == 1


def new_tenant_contacts(client, world, alice, owner_db, n=3, **kw):
    """Fresh contacts that only this test's campaign targets (via a dedicated business)."""
    b = Business(tenant_id=world.tenant_a, name=f"Biz {uuid.uuid4().hex[:6]}", kind="agency")
    owner_db.add(b)
    owner_db.commit()
    biz = b.id
    ids = []
    for i in range(n):
        c = client.post(f"/tenants/{world.tenant_a}/contacts", headers=alice, json=lead(
            name=f"Lead {i}", email=f"lead{i}-{biz.hex[:8]}@x.example",
            business_id=str(biz), **kw)).json()
        ids.append(c)
    return biz, ids


def campaign(client, world, alice, biz, **kw):
    body = {"name": "Spring check-in", "channel": "email", "sender_name": "Steady",
            "sender_email": "steady@example.com", "postal_address": ADDRESS,
            "subject_template": "Quick question for {company}",
            "body_template": "Hi {first_name},\n\nWe help electricians.\n\n{postal_address}\n"
                             "Unsubscribe: {unsubscribe_url}",
            "business_id": str(biz)}
    body.update(kw)
    r = client.post(f"/tenants/{world.tenant_a}/campaigns", headers=alice, json=body)
    assert r.status_code == 201, r.text
    return r.json()


def prepare(client, world, alice, maker, owner_db, cid):
    r = client.post(f"/tenants/{world.tenant_a}/campaigns/{cid}/prepare", headers=alice,
                    json={"idempotency_key": uuid.uuid4().hex})
    Worker(maker, retry_base_seconds=0).run_once()
    run, tasks = run_state(owner_db, r.json()["run_id"])
    assert run.status == "succeeded", tasks[0].error
    return tasks[0].output


def approve(client, world, alice, cid):
    r = client.post(f"/tenants/{world.tenant_a}/campaigns/{cid}/approve", headers=alice,
                    json={"idempotency_key": uuid.uuid4().hex})
    assert r.status_code == 202, r.text
    return r.json()["run_id"]


def test_campaign_flow_prepare_review_approve_send(client, world, alice, maker, owner_db,
                                                   drain):
    biz, contacts = new_tenant_contacts(client, world, alice, owner_db, n=3)
    k = campaign(client, world, alice, biz)
    out = prepare(client, world, alice, maker, owner_db, k["id"])
    assert out["queued"] == 3
    msgs = client.get(f"/tenants/{world.tenant_a}/campaigns/{k['id']}/messages",
                      headers=alice).json()
    m = msgs[0]
    assert m["subject"] == "Quick question for Acme Electric"
    assert ADDRESS in m["body"] and "https://jackson.example.com/u/" in m["body"]
    assert m["status"] == "queued" and m["external_id"] is None  # nothing sent yet

    run_id = approve(client, world, alice, k["id"])
    Worker(maker, retry_base_seconds=0).run_once()
    run, tasks = run_state(owner_db, run_id)
    assert run.status == "succeeded" and tasks[0].output["sent"] == 3
    assert tasks[0].output["connector"] == "mock"
    assert "outreach.sent" in events(owner_db, run_id)
    got = client.get(f"/tenants/{world.tenant_a}/campaigns/{k['id']}", headers=alice).json()
    assert got["status"] == "completed" and got["counts"] == {"sent": 3}
    c0 = client.get(f"/tenants/{world.tenant_a}/contacts?stage=contacted",
                    headers=alice).json()
    assert {c["id"] for c in contacts} <= {c["id"] for c in c0}


def test_unapproved_campaign_cannot_send(client, world, alice, maker, owner_db, drain):
    biz, _ = new_tenant_contacts(client, world, alice, owner_db, n=1)
    k = campaign(client, world, alice, biz)
    r = client.post(f"/tenants/{world.tenant_a}/campaigns/{k['id']}/approve", headers=alice,
                    json={"idempotency_key": uuid.uuid4().hex})
    assert r.status_code == 409  # not prepared/reviewed yet


def test_non_compliant_campaign_is_blocked_at_review(client, world, alice, maker, owner_db,
                                                     drain):
    biz, _ = new_tenant_contacts(client, world, alice, owner_db, n=2)
    k = campaign(client, world, alice, biz, postal_address="",
                 body_template="Hi {first_name}, guaranteed results!")
    out = prepare(client, world, alice, maker, owner_db, k["id"])
    assert out["blocked"] == 2 and out["queued"] == 0
    msgs = client.get(f"/tenants/{world.tenant_a}/campaigns/{k['id']}/messages",
                      headers=alice).json()
    probs = " ".join(msgs[0]["review"]["problems"])
    assert "postal address" in probs and "unsubscribe" in probs and "guaranteed" in probs
    r = client.post(f"/tenants/{world.tenant_a}/campaigns/{k['id']}/approve", headers=alice,
                    json={"idempotency_key": uuid.uuid4().hex})
    assert r.status_code == 409 and "passed review" in r.text


def test_unsubscribe_link_works_and_is_honoured_after_approval(client, world, alice, maker,
                                                               owner_db, drain):
    biz, contacts = new_tenant_contacts(client, world, alice, owner_db, n=2)
    k = campaign(client, world, alice, biz)
    prepare(client, world, alice, maker, owner_db, k["id"])
    msgs = client.get(f"/tenants/{world.tenant_a}/campaigns/{k['id']}/messages",
                      headers=alice).json()
    link = re.search(r"https://jackson\.example\.com(/u/\S+)", msgs[0]["body"]).group(1)
    page = client.get(link)  # no login needed; GET changes nothing
    assert page.status_code == 200 and "<form method=\"post\"" in page.text
    assert owner_db.scalar(select(Suppression.id).where(
        Suppression.value_hash == crm_mod.address_hash(msgs[0]["to"]))) is None
    run_id = approve(client, world, alice, k["id"])
    done = client.post(link)  # the recipient opts out after the owner approved
    assert done.status_code == 200 and "unsubscribed" in done.text
    Worker(maker, retry_base_seconds=0).run_once()
    out = run_state(owner_db, run_id)[1][0].output
    assert (out["sent"], out["suppressed"]) == (1, 1)
    statuses = {m["to"]: m["status"] for m in client.get(
        f"/tenants/{world.tenant_a}/campaigns/{k['id']}/messages", headers=alice).json()}
    assert statuses[msgs[0]["to"]] == "skipped"


def test_forged_unsubscribe_link_is_rejected(client):
    assert client.post("/u/not-a-real-token").status_code == 404


def test_opted_out_contact_is_never_queued(client, world, alice, maker, owner_db, drain):
    biz, contacts = new_tenant_contacts(client, world, alice, owner_db, n=2)
    client.post(f"/tenants/{world.tenant_a}/suppressions", headers=alice,
                json={"address": contacts[0]["email"].upper(), "channel": "email"})
    k = campaign(client, world, alice, biz)
    out = prepare(client, world, alice, maker, owner_db, k["id"])
    assert (out["queued"], out["blocked"]) == (1, 1)


def test_deleting_a_contact_keeps_them_suppressed(client, world, alice, maker, owner_db,
                                                  drain):
    biz, contacts = new_tenant_contacts(client, world, alice, owner_db, n=1)
    email = contacts[0]["email"]
    assert client.delete(f"/tenants/{world.tenant_a}/contacts/{contacts[0]['id']}",
                         headers=alice).status_code == 204
    # the address is gone from contacts, only a hash remains
    assert owner_db.scalar(text("SELECT count(*) FROM contacts WHERE email = :e"),
                           {"e": email}) == 0
    # re-imported later: still never queued
    client.post(f"/tenants/{world.tenant_a}/contacts", headers=alice,
                json=lead(email=email, business_id=str(biz)))
    out = prepare(client, world, alice, maker, owner_db,
                  campaign(client, world, alice, biz)["id"])
    assert (out["queued"], out["blocked"]) == (0, 1)


def test_sms_needs_recorded_consent_and_respects_quiet_hours(client, world, alice, maker,
                                                             owner_db, drain, monkeypatch):
    biz, _ = new_tenant_contacts(client, world, alice, owner_db, n=0)
    a, b = (client.post(f"/tenants/{world.tenant_a}/contacts", headers=alice, json=lead(
        name=f"Sms {i}", email=None, phone=f"217555{uuid.uuid4().int % 10000:04d}",
        business_id=str(biz))).json() for i in range(2))
    r = client.post(f"/tenants/{world.tenant_a}/contacts/{a['id']}/consent", headers=alice,
                    json={"channel": "sms", "evidence": "web form checkbox, 2026-11-20"})
    assert r.json()["sms_consent_at"] and b["sms_consent_at"] is None
    k = campaign(client, world, alice, biz, channel="sms",
                 body_template="Hi {first_name}, spring slots open. Reply STOP to opt out.")
    out = prepare(client, world, alice, maker, owner_db, k["id"])
    assert (out["queued"], out["blocked"]) == (1, 1)  # b has no consent
    run_id = approve(client, world, alice, k["id"])
    monkeypatch.setattr(crm_mod, "within_quiet_hours", lambda tz, now=None: False)
    w = Worker(maker, retry_base_seconds=0)
    w.run_once()
    out = run_state(owner_db, run_id)[1][0].output
    assert (out["sent"], out["deferred_quiet_hours"], out["remaining"]) == (0, 1, 1)
    # the database itself refuses to text the contact without consent
    no_consent = owner_db.scalar(select(OutreachMessage.id).where(
        OutreachMessage.contact_id == uuid.UUID(b["id"])))
    with maker() as db, pytest.raises(DBAPIError, match="no recorded SMS consent"):
        db.execute(update(OutreachMessage).where(OutreachMessage.id == no_consent)
                   .values(status="sending"))


def test_daily_limit_and_cancel(client, world, alice, maker, owner_db, drain):
    biz, _ = new_tenant_contacts(client, world, alice, owner_db, n=3)
    k = campaign(client, world, alice, biz, daily_limit=2)
    prepare(client, world, alice, maker, owner_db, k["id"])
    run_id = approve(client, world, alice, k["id"])
    Worker(maker, retry_base_seconds=0).run_once()
    out = run_state(owner_db, run_id)[1][0].output
    assert (out["sent"], out["deferred_daily_limit"], out["remaining"]) == (2, 1, 1)
    got = client.get(f"/tenants/{world.tenant_a}/campaigns/{k['id']}", headers=alice).json()
    assert got["status"] == "sending"
    assert client.post(f"/tenants/{world.tenant_a}/campaigns/{k['id']}/cancel",
                       headers=alice).json()["status"] == "cancelled"


def test_rerunning_a_send_does_not_send_twice(client, world, alice, maker, owner_db, drain):
    biz, _ = new_tenant_contacts(client, world, alice, owner_db, n=2)
    k = campaign(client, world, alice, biz)
    prepare(client, world, alice, maker, owner_db, k["id"])
    first_run = approve(client, world, alice, k["id"])
    w = Worker(maker, retry_base_seconds=0)
    w.run_once()
    from jackson_api.crm import send_handler  # same task input, run again by hand

    class Ctx:
        input = {"campaign_id": k["id"]}
        tenant_id, run_id = world.tenant_a, uuid.UUID(first_run)

        @staticmethod
        def db_session():
            return maker()

        @staticmethod
        def authorize(action):
            return None
    with pytest.raises(Exception, match="completed"):
        send_handler(Ctx)
    assert owner_db.scalar(text("SELECT count(*) FROM outreach_messages WHERE campaign_id = "
                                ":c AND status = 'sent'"), {"c": k["id"]}) == 2


# --- Database guarantees ------------------------------------------------------------------------

def test_database_refuses_to_send_to_opted_out_or_unapproved(client, world, alice, maker,
                                                             owner_db, drain):
    biz, contacts = new_tenant_contacts(client, world, alice, owner_db, n=1)
    k = campaign(client, world, alice, biz)
    prepare(client, world, alice, maker, owner_db, k["id"])
    kid = uuid.UUID(k["id"])
    mid = owner_db.scalar(select(OutreachMessage.id).where(OutreachMessage.campaign_id == kid))
    # campaign not approved: no way to mark sent
    with maker() as db, pytest.raises(DBAPIError, match="outreach_blocked: campaign"):
        db.execute(update(OutreachMessage).where(OutreachMessage.id == mid)
                   .values(status="sending"))
    with maker() as db, pytest.raises(DBAPIError, match="claimed"):  # can't skip the claim
        db.execute(update(OutreachMessage).where(OutreachMessage.id == mid)
                   .values(status="sent"))
    # the worker can't approve the campaign itself
    with maker() as db, pytest.raises(DBAPIError, match="permission denied"):
        db.execute(update(OutreachCampaign).where(OutreachCampaign.id == kid)
                   .values(approved_by=None))
    with maker() as db:  # ...or skip approval: the row isn't updatable from 'prepared'
        r = db.execute(update(OutreachCampaign).where(OutreachCampaign.id == kid)
                       .values(status="sending"))
        assert r.rowcount == 0
    # even an approved campaign can't send to an opted-out address
    approve(client, world, alice, k["id"])
    client.post(f"/tenants/{world.tenant_a}/suppressions", headers=alice,
                json={"address": contacts[0]["email"]})
    with maker() as db, pytest.raises(DBAPIError, match="opted out"):
        db.execute(update(OutreachMessage).where(OutreachMessage.id == mid)
                   .values(status="sending"))
    # the worker can't insert an already-sent message, or delete an opt-out
    with maker() as db, pytest.raises(DBAPIError, match="row-level security"):
        db.add(OutreachMessage(tenant_id=world.tenant_a, campaign_id=kid, channel="email",
                               to_address="z@x.example", body="b", status="sending",
                               review={}))
        db.flush()
    with maker() as db, pytest.raises(DBAPIError, match="permission denied"):
        db.execute(text("DELETE FROM suppressions"))


def test_crm_is_tenant_isolated(client, world, alice, make_token):
    c = client.post(f"/tenants/{world.tenant_a}/contacts", headers=alice,
                    json=lead(email=f"{uuid.uuid4().hex[:8]}@x.example")).json()
    bob = auth(make_token, world.bob)
    assert client.patch(f"/tenants/{world.tenant_b}/contacts/{c['id']}", headers=bob,
                        json={"stage": "lost"}).status_code == 404
    assert all(x["id"] != c["id"] for x in client.get(f"/tenants/{world.tenant_b}/contacts",
                                                      headers=bob).json())


def test_send_task_without_an_approval_waits_for_one(client, world, alice, maker, owner_db,
                                                     drain):
    """Even for an approved campaign, a send task with no approval on file (e.g. created by
    an agent) stops and asks; outbound campaigns always need a person."""
    from jackson_api.runs import create_run

    biz, _ = new_tenant_contacts(client, world, alice, owner_db, n=1)
    k = campaign(client, world, alice, biz)
    prepare(client, world, alice, maker, owner_db, k["id"])
    approve(client, world, alice, k["id"])
    uid = owner_db.scalar(text("SELECT id FROM users WHERE auth_subject = :s"),
                          {"s": world.alice})
    run, _ = create_run(owner_db, world.tenant_a, uid, {
        "type": "BUILD", "objective": "send again", "idempotency_key": uuid.uuid4().hex},
        first_task={"kind": "outreach_send", "title": "send", "tool_scope": [],
                    "input": {"campaign_id": k["id"]}})
    rid = str(run.id)
    owner_db.commit()
    w = Worker(maker, retry_base_seconds=0)
    while run_state(owner_db, rid)[0].status not in ("waiting_approval", "succeeded",
                                                     "failed"):
        assert w.run_once()
    assert run_state(owner_db, rid)[0].status == "waiting_approval"


def test_cancelling_mid_send_stops_cleanly(client, world, alice, maker, owner_db, drain,
                                           monkeypatch):
    biz, _ = new_tenant_contacts(client, world, alice, owner_db, n=3)
    k = campaign(client, world, alice, biz)
    prepare(client, world, alice, maker, owner_db, k["id"])
    run_id = approve(client, world, alice, k["id"])
    real_send = crm_mod.MockSender.send

    def send_then_cancel(self, *a):
        client.post(f"/tenants/{world.tenant_a}/campaigns/{k['id']}/cancel", headers=alice)
        return real_send(self, *a)
    monkeypatch.setattr(crm_mod.MockSender, "send", send_then_cancel)
    Worker(maker, retry_base_seconds=0).run_once()
    run, tasks = run_state(owner_db, run_id)
    assert run.status == "succeeded", tasks[0].error
    # the message already handed to the provider is recorded as sent; nothing after it
    assert tasks[0].output["stopped"] is True and tasks[0].output["sent"] == 1
    got = client.get(f"/tenants/{world.tenant_a}/campaigns/{k['id']}", headers=alice).json()
    assert got["status"] == "cancelled" and got["counts"] == {"sent": 1, "queued": 2}
