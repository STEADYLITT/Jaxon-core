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


@pytest.fixture()
def no_shopify_env(monkeypatch):
    for v in ("SHOPIFY_ADMIN_API_TOKEN", "SHOPIFY_STORE_DOMAIN", "SHOPIFY_CLIENT_ID",
              "SHOPIFY_CLIENT_SECRET"):
        monkeypatch.delenv(v, raising=False)
    shop_mod._providers.clear()


def test_mock_until_the_owner_configures_shopify(no_shopify_env, monkeypatch):
    assert backend_for({}, None, None).backend == "mock"
    with pytest.raises(NonRetryableError, match="isn't configured"):
        backend_for({"mode": "shopify"}, None, None)
    monkeypatch.setenv("SHOPIFY_STORE_DOMAIN", "cha6cs-6c.myshopify.com")
    assert backend_for({}, None, None).backend == "mock"  # domain alone isn't enough
    monkeypatch.setenv("SHOPIFY_CLIENT_ID", "client-id-1")
    monkeypatch.setenv("SHOPIFY_CLIENT_SECRET", "s" * 32)
    live = backend_for({}, None, None)
    assert live.backend == "shopify" and live.store_domain == "cha6cs-6c.myshopify.com"
    assert backend_for({"mode": "mock"}, None, None).backend == "mock"  # owner can force mock


def test_grants_cannot_point_at_another_store(no_shopify_env, monkeypatch):
    monkeypatch.setenv("SHOPIFY_STORE_DOMAIN", "cha6cs-6c.myshopify.com")
    monkeypatch.setenv("SHOPIFY_ADMIN_API_TOKEN", FAKE_TOKEN)
    with pytest.raises(NonRetryableError, match="different store"):
        backend_for({"mode": "shopify", "store_domain": "evil.myshopify.com"}, None, None)
    monkeypatch.setenv("SHOPIFY_STORE_DOMAIN", "evil.com")
    with pytest.raises(NonRetryableError, match="myshopify.com"):
        backend_for({}, None, None)


class TokenEndpoint:
    """Fake Shopify: the client credentials token endpoint + the GraphQL API."""

    def __init__(self, graphql: FakeShopify, error=None, expire_first=False):
        self.graphql, self.error, self.expire_first = graphql, error, expire_first
        self.token_calls, self.n = [], 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/admin/oauth/access_token":
            self.token_calls.append(dict(httpx.QueryParams(request.content.decode())))
            if self.error:
                return httpx.Response(400, json={"error": self.error})
            self.n += 1
            return httpx.Response(200, json={"access_token": f"tok-{self.n}",
                                             "scope": "read_products,write_products",
                                             "expires_in": 86399})
        tok = request.headers["X-Shopify-Access-Token"]
        if self.expire_first and tok == "tok-1":
            return httpx.Response(401, json={"errors": "expired"})
        return self.graphql(request)


def creds(endpoint, clock=None):
    client = httpx.Client(transport=httpx.MockTransport(endpoint))
    kw = {"clock": clock} if clock else {}
    return shop_mod.ClientCredentials("cha6cs-6c.myshopify.com", "client-id-1", "s" * 32,
                                      client=client, **kw), client


def test_client_credentials_grant_gets_and_reuses_a_token():
    ep = TokenEndpoint(FakeShopify())
    provider, client = creds(ep)
    api = ShopifyAdmin("cha6cs-6c.myshopify.com", provider, client=client)
    api.create_draft(DraftProductIn(title="Mag Grip", price=24.99))
    assert ep.token_calls == [{"grant_type": "client_credentials", "client_id": "client-id-1",
                               "client_secret": "s" * 32}]
    graphql_calls = ep.graphql.calls
    assert all(c[0].headers["X-Shopify-Access-Token"] == "tok-1" for c in graphql_calls)
    assert len(ep.token_calls) == 1  # one token for all three calls


def test_token_is_renewed_before_it_expires_and_after_a_401():
    now = [0.0]
    ep = TokenEndpoint(FakeShopify())
    provider, client = creds(ep, clock=lambda: now[0])
    assert provider.token() == "tok-1"
    now[0] = 86399 - 599  # within 10 minutes of expiry: renew
    assert provider.token() == "tok-2"
    ep2 = TokenEndpoint(FakeShopify(), expire_first=True)
    provider2, client2 = creds(ep2)
    ShopifyAdmin("cha6cs-6c.myshopify.com", provider2, client=client2).find_by_handle("x")
    assert len(ep2.token_calls) == 2  # 401 -> fresh token -> retried once


def test_wrong_organization_gives_clear_instructions():
    provider, _ = creds(TokenEndpoint(FakeShopify(), error="shop_not_permitted"))
    with pytest.raises(NonRetryableError, match="Build apps in Dev Dashboard"):
        provider.token()



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
    monkeypatch.setenv("SHOPIFY_STORE_DOMAIN", "jackson-dev.myshopify.com")
    opp = setup(client, world, alice, mode="shopify")
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
    monkeypatch.setenv("SHOPIFY_STORE_DOMAIN", "jackson-dev.myshopify.com")
    opp = setup(client, world, alice, mode="shopify")
    run_id = build(client, world, alice, opp["id"], maker)
    run, tasks = run_state(owner_db, run_id)
    assert run.status == "failed" and "QA failed" in tasks[0].error
    assert "shopify.verify_failed" in events(owner_db, run_id)
