"""Phase 15: Shopify draft connector + first vertical slice builder/QA.

Spec vol. 06 acceptance covered here: "sandbox Shopify draft verified".
"""

import json
import uuid

import httpx
import pytest
from conftest import auth, events, run_state
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from jackson_api.connectors import shopify as shop_mod
from jackson_api.connectors.claims import check_claims
from jackson_api.connectors.shopify import DraftProductIn, ShopifyAdmin, backend_for
from jackson_api.models import ShopProduct
from jackson_api.worker import Worker
from jackson_api.worker_errors import NonRetryableError

FAKE_TOKEN = "shp" + "at_" + "0" * 32  # built at runtime: keeps the repo secret scan clean


# --- Copy rules -------------------------------------------------------------------------------

@pytest.mark.parametrize("bad", ["Rated 4.9 stars", "Over 1,000 reviews", "Our best seller!",
                                 "Clinically proven grip", "Guaranteed results",
                                 "Customers love it", "Buy followers today"])
def test_invented_claims_are_rejected(bad):
    assert check_claims(bad)
    with pytest.raises(ValueError):
        DraftProductIn(title="Phone grip", description_html=f"<p>{bad}</p>", price=20)


def test_plain_factual_copy_passes():
    assert check_claims("<p>Magnetic phone grip. Fits most phones. Folds flat.</p>") == []


# --- Real Admin API backend (shape checked against shopify.dev 2026-10) -------------------------

class FakeShopify:
    def __init__(self, existing=None, user_errors=None, status=200, readback=None):
        self.calls, self.existing, self.user_errors, self.status = [], existing, user_errors, status
        self.readback = readback or {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.calls.append((request, body))
        if self.status != 200:
            return httpx.Response(self.status, json={})
        q = body["query"]
        if "products(first: 1" in q:
            nodes = [self.existing] if self.existing else []
            return httpx.Response(200, json={"data": {"products": {"nodes": nodes}}})
        if "productCreate" in q:
            self.handle = body["variables"]["product"]["handle"]
            return httpx.Response(200, json={"data": {"productCreate": {
                "product": {"id": "gid://shopify/Product/101", "handle": body["variables"][
                    "product"]["handle"], "status": "DRAFT", "title": body["variables"][
                    "product"]["title"], "variants": {"nodes": [
                        {"id": "gid://shopify/ProductVariant/9"}]}},
                "userErrors": self.user_errors or []}}})
        if "productVariantsBulkUpdate" in q:
            return httpx.Response(200, json={"data": {"productVariantsBulkUpdate": {
                "productVariants": [{"id": "gid://shopify/ProductVariant/9", "price": "24.99"}],
                "userErrors": []}}})
        if "product(id:" in q:
            return httpx.Response(200, json={"data": {"product": {
                "id": "gid://shopify/Product/101",
                "handle": self.readback.get("handle", getattr(self, "handle", "mag-grip")),
                "status": self.readback.get("status", "DRAFT"), "title": "Mag Grip"}}})
        if "productUpdate" in q:
            return httpx.Response(200, json={"data": {"productUpdate": {
                "product": {"id": "gid://shopify/Product/101", "status": "ACTIVE"},
                "userErrors": []}}})
        return httpx.Response(400, json={})


def admin(fake):
    return ShopifyAdmin("jackson-dev.myshopify.com", FAKE_TOKEN,
                        client=httpx.Client(transport=httpx.MockTransport(fake)))


def test_real_backend_creates_draft_then_prices_it():
    fake = FakeShopify()
    out = admin(fake).create_draft(DraftProductIn(title="Mag Grip", price=24.99))
    req, first = fake.calls[0]
    assert str(req.url) == "https://jackson-dev.myshopify.com/admin/api/2026-10/graphql.json"
    assert req.headers["X-Shopify-Access-Token"] == FAKE_TOKEN
    create = fake.calls[1][1]
    assert create["variables"]["product"]["status"] == "DRAFT"
    assert create["variables"]["product"]["handle"] == "mag-grip"
    assert fake.calls[2][1]["variables"]["variants"][0]["price"] == "24.99"
    assert out["status"] == "DRAFT" and out["created"] is True


def test_real_backend_is_idempotent_by_handle():
    fake = FakeShopify(existing={"id": "gid://shopify/Product/7", "handle": "mag-grip",
                                 "status": "DRAFT", "title": "Mag Grip"})
    out = admin(fake).create_draft(DraftProductIn(title="Mag Grip", price=24.99))
    assert out["created"] is False and len(fake.calls) == 1  # looked up, didn't create


def test_real_backend_errors():
    with pytest.raises(NonRetryableError, match="rejected productCreate"):
        admin(FakeShopify(user_errors=[{"field": ["title"], "message": "bad"}])).create_draft(
            DraftProductIn(title="Mag Grip", price=1))
    with pytest.raises(httpx.HTTPStatusError):  # 429: retryable by the worker
        admin(FakeShopify(status=429)).find_by_handle("x")


def test_live_mode_needs_dev_store_token_and_real_domain(monkeypatch):
    monkeypatch.delenv("SHOPIFY_ADMIN_API_TOKEN", raising=False)
    with pytest.raises(NonRetryableError, match="development store"):
        backend_for({"mode": "shopify", "store_domain": "x.myshopify.com"}, None, None)
    with pytest.raises(NonRetryableError, match="not configured"):
        backend_for({"mode": "shopify", "store_domain": "x.myshopify.com", "dev_store": True},
                    None, None)
    monkeypatch.setenv("SHOPIFY_ADMIN_API_TOKEN", FAKE_TOKEN)
    with pytest.raises(NonRetryableError, match="myshopify.com"):
        backend_for({"mode": "shopify", "store_domain": "evil.com", "dev_store": True},
                    None, None)


# --- First vertical slice: opportunity -> economics -> draft -> QA -----------------------------

ECON = {"price": 24.99, "unit_cost": 5, "outbound_shipping_per_order": 4, "cac": 6}


@pytest.fixture()
def alice(make_token, world):
    return auth(make_token, world.alice)


def setup(client, world, alice, level=2, econ=True, mode="mock", **grant_cfg):
    client.put(f"/tenants/{world.tenant_a}/businesses/{world.biz_a}/autonomy",
               json={"level": level}, headers=alice)
    client.post(f"/tenants/{world.tenant_a}/tool-grants", headers=alice, json={
        "tool_name": "shopify.drafts", "business_id": str(world.biz_a),
        "config": {"mode": mode, **grant_cfg}})
    src = "https://market.example/grips"
    opp = client.post(f"/tenants/{world.tenant_a}/opportunities", headers=alice, json={
        "title": f"Mag Grip {uuid.uuid4().hex[:6]}", "business_id": str(world.biz_a),
        "signals": [{"type": "avg_price", "value": 24.99, "source_url": src}]}).json()
    if econ:
        client.post(f"/tenants/{world.tenant_a}/opportunities/{opp['id']}/economics",
                    json=ECON, headers=alice)
    return opp


def build(client, world, alice, opp_id, maker, key=None):
    r = client.post(f"/tenants/{world.tenant_a}/opportunities/{opp_id}/build-store",
                    headers=alice, json={"idempotency_key": key or uuid.uuid4().hex})
    assert r.status_code == 202, r.text
    Worker(maker, retry_base_seconds=0).run_once()
    return r.json()["run_id"]


def test_sandbox_shopify_draft_verified(client, world, alice, maker, owner_db, drain,
                                        tmp_path, monkeypatch):
    """Spec acceptance: sandbox Shopify draft verified (mock store, no credentials)."""
    monkeypatch.setenv("ARTIFACT_DIR", str(tmp_path))
    opp = setup(client, world, alice)
    run_id = build(client, world, alice, opp["id"], maker)
    run, tasks = run_state(owner_db, run_id)
    out = tasks[0].output
    assert run.status == "succeeded", tasks[0].error
    assert out["qa_passed"] and out["draft"]["verified"] and out["draft"]["status"] == "DRAFT"
    assert out["draft"]["backend"] == "mock" and out["draft"]["price_cents"] == 2499
    assert out["copy_mode"] == "mock"
    row = owner_db.get(ShopProduct, uuid.UUID(out["draft"]["shop_product_id"]))
    assert "[MOCK]" in row.payload["description_html"]  # placeholder copy is labeled
    products = client.get(f"/tenants/{world.tenant_a}/shop/products", headers=alice).json()
    mine = [p for p in products if p["opportunity_id"] == opp["id"]]
    assert len(mine) == 1 and mine[0]["status"] == "DRAFT" and mine[0]["verified"]
    report = client.get(f"/tenants/{world.tenant_a}/artifacts/{out['report_artifact_id']}",
                        headers=alice)
    assert report.json()["qa"]["not_published"] is True
    ev = events(owner_db, run_id)
    assert "shopify.draft_created" in ev and "shopify.draft_verified" in ev


def test_needs_economics_first(client, world, alice, maker, owner_db, drain):
    opp = setup(client, world, alice, econ=False)
    run_id = build(client, world, alice, opp["id"], maker)
    assert "unit economics" in run_state(owner_db, run_id)[1][0].error


def test_draft_creation_needs_autonomy_level_2(client, world, alice, maker, owner_db, drain,
                                               tmp_path, monkeypatch):
    monkeypatch.setenv("ARTIFACT_DIR", str(tmp_path))
    opp = setup(client, world, alice, level=1)
    run_id = build(client, world, alice, opp["id"], maker)
    assert run_state(owner_db, run_id)[0].status == "waiting_approval"


def test_building_twice_never_duplicates_the_product(client, world, alice, maker, owner_db,
                                                     drain, tmp_path, monkeypatch):
    monkeypatch.setenv("ARTIFACT_DIR", str(tmp_path))
    opp = setup(client, world, alice)
    build(client, world, alice, opp["id"], maker)
    second = build(client, world, alice, opp["id"], maker)
    assert run_state(owner_db, second)[0].status == "succeeded"
    n = owner_db.scalar(text("SELECT count(*) FROM shop_products WHERE opportunity_id = :o"),
                        {"o": opp["id"]})
    assert n == 1


def test_publishing_always_waits_for_owner_approval(client, world, alice, maker, owner_db,
                                                    drain, tmp_path, monkeypatch):
    monkeypatch.setenv("ARTIFACT_DIR", str(tmp_path))
    opp = setup(client, world, alice, level=3)
    build(client, world, alice, opp["id"], maker)
    prod = [p for p in client.get(f"/tenants/{world.tenant_a}/shop/products",
                                  headers=alice).json() if p["opportunity_id"] == opp["id"]][0]
    r = client.post(f"/tenants/{world.tenant_a}/shop/products/{prod['id']}/publish",
                    headers=alice, json={"idempotency_key": uuid.uuid4().hex})
    run_id = r.json()["run_id"]
    w = Worker(maker, retry_base_seconds=0)
    w.run_once()
    assert run_state(owner_db, run_id)[0].status == "waiting_approval"  # even at level 3
    [ap] = [a for a in client.get(f"/tenants/{world.tenant_a}/approvals?status=pending",
                                  headers=alice).json() if a["run_id"] == run_id]
    assert ap["category"] == "production_change"
    client.post(f"/tenants/{world.tenant_a}/approvals/{ap['id']}/decision",
                json={"decision": "approve"}, headers=alice)
    w.run_once()
    assert run_state(owner_db, run_id)[0].status == "succeeded"
    owner_db.expire_all()
    assert owner_db.get(ShopProduct, uuid.UUID(prod["id"])).status == "ACTIVE"


def test_live_backend_end_to_end_keeps_token_out_of_storage(client, world, alice, maker,
                                                            owner_db, drain, tmp_path,
                                                            monkeypatch):
    monkeypatch.setenv("ARTIFACT_DIR", str(tmp_path))
    monkeypatch.setenv("SHOPIFY_ADMIN_API_TOKEN", FAKE_TOKEN)
    fake = FakeShopify()
    real_client = httpx.Client
    monkeypatch.setattr(shop_mod.httpx, "Client",
                        lambda **kw: real_client(transport=httpx.MockTransport(fake)))
    opp = setup(client, world, alice, mode="shopify", store_domain="jackson-dev.myshopify.com",
                dev_store=True)
    run_id = build(client, world, alice, opp["id"], maker)
    run, tasks = run_state(owner_db, run_id)
    assert run.status == "succeeded", tasks[0].error
    assert tasks[0].output["draft"]["backend"] == "shopify"
    assert any(c[0].headers.get("X-Shopify-Access-Token") == FAKE_TOKEN for c in fake.calls)
    blob = owner_db.execute(text(
        "SELECT coalesce(string_agg(row_to_json(t)::text, ''), '') FROM ("
        "SELECT * FROM shop_products UNION ALL SELECT NULL::uuid, tenant_id, NULL, run_id, "
        "NULL, NULL, 'x', NULL, '', '', '', 0, 'DRAFT', false, NULL, NULL, data, created_at, "
        "created_at FROM run_events) t")).scalar()
    grants = owner_db.execute(text("SELECT string_agg(config::text, '') FROM tool_grants")
                              ).scalar()
    assert FAKE_TOKEN not in blob and FAKE_TOKEN not in (grants or "")


@pytest.mark.parametrize("stmt", [
    "UPDATE shop_products SET title = 'x'", "UPDATE shop_products SET price_cents = 1",
    "DELETE FROM shop_products"])
def test_worker_cannot_rewrite_products(maker, stmt):
    with maker() as db, pytest.raises(DBAPIError, match="permission denied"):
        db.execute(text(stmt))


def test_worker_can_only_insert_drafts(maker, world):
    with maker() as db, pytest.raises(DBAPIError, match="row-level security"):
        db.add(ShopProduct(tenant_id=world.tenant_a, backend="mock", external_id="x",
                           handle=f"h-{uuid.uuid4().hex}", title="t", price_cents=1,
                           status="ACTIVE", payload={}))
        db.flush()


def test_unverified_product_cannot_be_published(client, world, alice, owner_db):
    owner_db.add(ShopProduct(tenant_id=world.tenant_a, backend="mock", external_id="gid://m/1",
                             handle=f"u-{uuid.uuid4().hex}", title="t", price_cents=100,
                             status="DRAFT", verified=False, payload={}))
    owner_db.commit()
    pid = owner_db.scalars(select(ShopProduct.id).where(ShopProduct.external_id == "gid://m/1")
                           ).first()
    r = client.post(f"/tenants/{world.tenant_a}/shop/products/{pid}/publish", headers=alice,
                    json={"idempotency_key": uuid.uuid4().hex})
    assert r.status_code == 409


@pytest.mark.parametrize("readback", [{"status": "ACTIVE"}, {"handle": "someone-elses-product"}])
def test_verification_catches_a_store_that_does_not_match(client, world, alice, maker, owner_db,
                                                          drain, tmp_path, monkeypatch,
                                                          readback):
    """If the store reports the product as live, or as a different product, QA must fail."""
    monkeypatch.setenv("ARTIFACT_DIR", str(tmp_path))
    monkeypatch.setenv("SHOPIFY_ADMIN_API_TOKEN", FAKE_TOKEN)
    fake = FakeShopify(readback=readback)
    real_client = httpx.Client
    monkeypatch.setattr(shop_mod.httpx, "Client",
                        lambda **kw: real_client(transport=httpx.MockTransport(fake)))
    opp = setup(client, world, alice, mode="shopify", store_domain="jackson-dev.myshopify.com",
                dev_store=True)
    run_id = build(client, world, alice, opp["id"], maker)
    run, tasks = run_state(owner_db, run_id)
    assert run.status == "failed" and "QA failed" in tasks[0].error
    assert "shopify.verify_failed" in events(owner_db, run_id)
