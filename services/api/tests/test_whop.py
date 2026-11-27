"""ADR-041: Whop connector. Members and sales exactly as Whop reports them (the network is
faked with Whop's documented shapes), a signed webhook, course checkout links, KPIs."""

import json
import time
import uuid

import httpx
import pytest
from conftest import auth
from sqlalchemy import select

from jackson_api import connections as C
from jackson_api.connectors import whop as W
from jackson_api.lines import kpis
from jackson_api.models import Business, Course, RevenueEntry, WhopMember
from jackson_api.whop_sync import record_payment
from jackson_api.worker import Worker

KEY = "whop_" + "k" * 30
SECRET = "ws_" + "s" * 40


def money(a):
    return {"amount": a, "currency": "usd", "decimals": 2, "display_decimals": 2}


class FakeWhop:
    def __init__(self):
        self.calls = []
        self.members = [
            {"id": "mem_A1", "status": "active", "user_id": "user_1", "member_id": "mber_1",
             "product_id": "prod_COURSE", "variant_id": "plan_49",
             "external_accounts": [{"platform": "discord", "external_id": "111222333",
                                    "username": "steady"}],
             "current_period_end": "2026-12-27T00:00:00Z", "metadata": {}},
            {"id": "mem_B2", "status": "canceled", "user_id": "user_2",
             "product_id": "prod_COURSE", "variant_id": "plan_49", "external_accounts": [],
             "metadata": {}},
        ]
        self.payments = [
            {"id": "pay_1", "status": "paid", "substatus": "succeeded", "currency": "usd",
             "total": money("49.00"), "refunded_amount": money("0.00"),
             "created_at": "2026-11-20T10:00:00Z", "product_id": "prod_COURSE",
             "variant_id": "plan_49", "membership_id": "mem_A1", "metadata": {}},
            {"id": "pay_2", "status": "open", "total": money("49.00"),
             "created_at": "2026-11-21T10:00:00Z", "product_id": "prod_COURSE"},
        ]

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.calls.append(req)
        assert req.headers["authorization"] == f"Bearer {KEY}"
        assert req.headers["api-version-date"] == W.VERSION_DATE
        p = req.url.path
        if p == "/api/v1/accounts/me":
            return httpx.Response(200, json={"id": "biz_steady", "title": "Steady Trades"})
        if p == "/api/v1/memberships":
            if req.url.params.get("after") != "c1":
                return httpx.Response(200, json={"data": self.members[:1], "page_info": {
                    "end_cursor": "c1", "has_next_page": True}})
            return httpx.Response(200, json={"data": self.members[1:], "page_info": {
                "end_cursor": None, "has_next_page": False}})
        if p == "/api/v1/payments":
            return httpx.Response(200, json={"data": self.payments, "page_info": {
                "has_next_page": False}})
        if p == "/api/v1/checkout_configurations" and req.method == "POST":
            body = json.loads(req.content)
            assert body["variant_id"] == "plan_49" and body["metadata"]["jackson_course_id"]
            return httpx.Response(200, json={"id": "ch_9", "purchase_url":
                                             "https://whop.com/checkout/ch_9/"})
        return httpx.Response(404, json={"error": {"type": "not_found", "message": "nope"}})


@pytest.fixture()
def whop(monkeypatch):
    fake = FakeWhop()
    monkeypatch.setenv("WHOP_API_KEY", KEY)
    monkeypatch.setenv("WHOP_WEBHOOK_SECRET", SECRET)
    mk = lambda: httpx.Client(transport=httpx.MockTransport(fake))  # noqa: E731
    monkeypatch.setattr(W, "_new_client", mk)
    monkeypatch.setattr(C, "_new_client", mk)
    return fake


def courses_biz(owner_db, world, name="AI Wealth Academy"):
    b = Business(tenant_id=world.tenant_a, name=name, kind="education", line="courses")
    owner_db.add(b)
    owner_db.commit()
    return b.id


def link(client, world, make_token, **body):
    return client.post(f"/tenants/{world.tenant_a}/whop/links",
                       headers=auth(make_token, world.alice), json=body)


def signed(body: dict, secret=SECRET, ts=None):
    raw = json.dumps(body).encode()
    ts = int(time.time()) if ts is None else ts
    mid = f"msg_{uuid.uuid4().hex[:8]}"
    return raw, {"webhook-id": mid, "webhook-timestamp": str(ts),
                 "webhook-signature": W.sign(secret, mid, ts, raw),
                 "content-type": "application/json"}


def test_money_is_dollars_strings():
    assert W.money_cents(money("49.00")) == 4900
    assert W.money_cents(money("0.5")) == 50
    assert W.money_cents(19.99) == 1999 and W.money_cents(None) == 0
    assert W.money_currency(money("1")) == "USD"


def test_connection_test_reads_the_account_and_hides_the_key(client, world, make_token, whop):
    r = client.post(f"/tenants/{world.tenant_a}/admin/connectors/whop/test",
                    headers=auth(make_token, world.alice))
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["ok"] is True and out["result"]["account"] == "biz_steady"
    assert KEY not in r.text and SECRET not in r.text
    rows = client.get(f"/tenants/{world.tenant_a}/admin/connectors",
                      headers=auth(make_token, world.alice)).json()
    assert {c["id"]: c for c in rows}["whop"]["mode"] == "LIVE"


def test_sync_records_members_and_real_sales_once(client, world, make_token, maker, owner_db,
                                                  drain, whop):
    biz = courses_biz(owner_db, world)
    assert link(client, world, make_token, business_id=str(biz),
                product_id="prod_COURSE").status_code == 201
    h = auth(make_token, world.alice)
    for _ in range(2):  # the second sync must not double-count
        r = client.post(f"/tenants/{world.tenant_a}/whop/sync", headers=h,
                        json={"idempotency_key": uuid.uuid4().hex})
        assert r.status_code == 202, r.text
        Worker(maker, retry_base_seconds=0).run_once()
    owner_db.expire_all()
    mem = {m.membership_id: m for m in owner_db.scalars(select(WhopMember).where(
        WhopMember.tenant_id == world.tenant_a))}
    assert set(mem) == {"mem_A1", "mem_B2"}  # both pages were read
    assert mem["mem_A1"].discord_id == "111222333" and mem["mem_A1"].business_id == biz
    rev = owner_db.scalars(select(RevenueEntry).where(RevenueEntry.tenant_id == world.tenant_a,
                                                      RevenueEntry.source == "whop")).all()
    assert [(e.kind, e.amount_cents, e.reference, e.period) for e in rev] == [
        ("actual", 4900, "whop:pay_1", "2026-11")]  # the open payment isn't a sale
    st = client.get(f"/tenants/{world.tenant_a}/whop", headers=h).json()
    assert st["members_with_access"] == 1 and st["members_by_status"]["canceled"] == 1

    from sqlalchemy.orm import Session

    from jackson_api.db import bind_tenant  # KPIs read through RLS like the app does
    with Session(owner_db.get_bind()) as s:
        bind_tenant(s, world.tenant_a)
        k = {x["name"]: x for x in kpis(s, world.tenant_a, "courses")}
        c = {x["name"]: x for x in kpis(s, world.tenant_a, "community")}
    assert k["Students with access (Whop)"]["value"] == 1
    if "2026-11" == __import__("datetime").datetime.now().strftime("%Y-%m"):
        assert k["Course revenue this month (Whop)"]["value"] == 49.0
    assert c["Members with Discord linked"]["value"] == 1


def test_no_key_means_no_sync_and_no_numbers(client, world, make_token, owner_db, monkeypatch):
    monkeypatch.delenv("WHOP_API_KEY", raising=False)
    r = client.post(f"/tenants/{world.tenant_a}/whop/sync",
                    headers=auth(make_token, world.alice),
                    json={"idempotency_key": uuid.uuid4().hex})
    assert r.status_code == 409 and "WHOP_API_KEY" in r.text
    k = {x["name"]: x for x in kpis(owner_db, world.tenant_b, "courses")}
    assert k["Students with access (Whop)"]["value"] is None


def test_webhook_is_signed_and_records_a_sale_once(client, world, make_token, owner_db, whop):
    biz = courses_biz(owner_db, world)
    pay = {**whop.payments[0], "id": "pay_hook", "metadata": {
        "jackson_business_id": str(biz)}}
    event = {"id": "msg_1", "type": "payment.succeeded", "api_version": "v1",
             "timestamp": "2026-11-27T00:00:00Z", "data": pay}
    url = f"/webhooks/whop/{world.tenant_a}"
    raw, h = signed(event)
    assert client.post(url, content=raw, headers={**h, "webhook-signature": "v1,bad"}
                       ).status_code == 401
    old_raw, old_h = signed(event, ts=int(time.time()) - 3600)
    assert client.post(url, content=old_raw, headers=old_h).status_code == 401  # replay
    assert client.post(url, content=raw, headers=h).json()["recorded"] == "sale"
    raw2, h2 = signed(event)
    assert client.post(url, content=raw2, headers=h2).json()["recorded"] == "already recorded"
    rows = owner_db.scalars(select(RevenueEntry).where(
        RevenueEntry.reference == "whop:pay_hook")).all()
    assert len(rows) == 1 and rows[0].business_id == biz and rows[0].tenant_id == world.tenant_a

    m_raw, m_h = signed({"type": "membership.updated", "data": {
        "id": "mem_HOOK", "status": "expired", "product_id": "prod_X",
        "external_accounts": None}})
    assert client.post(url, content=m_raw, headers=m_h).json()["recorded"] == "membership"
    assert owner_db.scalar(select(WhopMember.status).where(
        WhopMember.membership_id == "mem_HOOK")) == "expired"


def test_refunds_record_only_the_new_amount(owner_db, world):
    p = {"id": "pay_r", "status": "paid", "total": money("100.00"),
         "refunded_amount": money("30.00"), "created_at": "2026-11-02T00:00:00Z",
         "refunded_at": "2026-11-05T00:00:00Z", "metadata": {}}
    from jackson_api.db import bind_tenant
    bind_tenant(owner_db, world.tenant_a)
    assert record_payment(owner_db, world.tenant_a, p) == {"sale": True, "refund_cents": 3000}
    assert record_payment(owner_db, world.tenant_a, p)["refund_cents"] == 0
    p["refunded_amount"] = money("100.00")
    assert record_payment(owner_db, world.tenant_a, p) == {"sale": False, "refund_cents": 7000}
    owner_db.commit()


def test_course_gets_a_real_checkout_link(client, world, make_token, owner_db, whop):
    biz = courses_biz(owner_db, world)
    from test_courses import OUTLINE

    from jackson_api.courses import build_sales_page
    c = Course(tenant_id=world.tenant_a, business_id=biz, brief="b", title="AI for Traders",
               status="ready", mode="live", price="49", outline=OUTLINE, lessons=[],
               platform_files={}, sales_files=build_sales_page(OUTLINE, "49"), checks={})
    owner_db.add(c)
    owner_db.commit()
    h = auth(make_token, world.alice)
    page = f"/tenants/{world.tenant_a}/courses/{c.id}/files/sales/index.html"
    assert "Nothing can be bought" in client.get(page, headers=h).text
    assert link(client, world, make_token, business_id=str(biz), course_id=str(c.id)
                ).status_code == 422  # needs the plan id
    r = link(client, world, make_token, business_id=str(biz), course_id=str(c.id),
             variant_id="plan_49")
    assert r.status_code == 201, r.text
    assert r.json()["purchase_url"] == "https://whop.com/checkout/ch_9/"
    html = client.get(page, headers=h).text
    assert 'href="https://whop.com/checkout/ch_9/"' in html and "Nothing can be bought" not in html
    got = client.get(f"/tenants/{world.tenant_a}/courses/{c.id}", headers=h).json()
    assert got["checkout_url"] == "https://whop.com/checkout/ch_9/"
    # another tenant can't link to this business
    r = client.post(f"/tenants/{world.tenant_b}/whop/links",
                    headers=auth(make_token, world.bob),
                    json={"business_id": str(biz), "product_id": "prod_COURSE"})
    assert r.status_code in (403, 404)
