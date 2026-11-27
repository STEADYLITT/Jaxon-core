"""Shopify draft-product connector (phase 15, ADR-025; real store support ADR-033).

Backends:
- MockShopify: used when Shopify isn't configured. Clearly labeled `backend: "mock"`.
  Products live in the `shop_products` table.
- ShopifyAdmin: the real Admin GraphQL API (version 2026-10, checked against shopify.dev).
  Turned on by the owner's settings (names only):
    SHOPIFY_STORE_DOMAIN   the store, e.g. cha6cs-6c.myshopify.com
    SHOPIFY_CLIENT_ID + SHOPIFY_CLIENT_SECRET   a Dev Dashboard app created in the STORE'S
        organization and installed on it: Jackson exchanges them for an Admin API token
        with the client credentials grant (24h tokens, renewed automatically)
    or SHOPIFY_ADMIN_API_TOKEN   a static Admin API token, if the owner has one
  Secrets stay in the environment: never in the database, grants or logs.

Flow: create the product as DRAFT -> set the variant price -> read it back and verify
it's still DRAFT. Jackson only ever creates DRAFTS. Publishing (DRAFT -> ACTIVE) is a
separate `production_change`: it needs the owner's approval, or a Launch approval that
allows Shopify publishing.
"""

import os
import re
import threading
import time
import uuid

import httpx
from pydantic import BaseModel, Field, field_validator

from ..worker_errors import NonRetryableError
from .claims import check_claims

API_VERSION = "2026-10"


class DraftProductIn(BaseModel):
    title: str = Field(min_length=3, max_length=255)
    description_html: str = Field(default="", max_length=20_000)
    price: float = Field(gt=0, le=100_000)
    product_type: str = Field(default="", max_length=255)
    vendor: str = Field(default="", max_length=255)
    tags: list[str] = Field(default_factory=list, max_length=50)
    opportunity_id: str | None = None

    @field_validator("description_html", "title")
    @classmethod
    def _no_invented_claims(cls, v: str) -> str:
        problems = check_claims(v)
        if problems:
            raise ValueError("copy makes unverified claims: " + "; ".join(problems))
        return v

    @property
    def handle(self) -> str:
        return re.sub(r"[^a-z0-9]+", "-", self.title.lower()).strip("-")[:200]


class ProductRef(BaseModel):
    product_id: str = Field(min_length=3)


def _gql_errors(body: dict, key: str) -> None:
    if body.get("errors"):
        raise NonRetryableError(f"Shopify GraphQL error: {body['errors']}"[:500])
    user_errors = (body.get("data", {}).get(key) or {}).get("userErrors") or []
    if user_errors:
        raise NonRetryableError(f"Shopify rejected {key}: {user_errors}"[:500])


STORE_RE = re.compile(r"[a-z0-9][a-z0-9-]*\.myshopify\.com")


class ClientCredentials:
    """Admin API tokens from the client credentials grant (shopify.dev: "Authenticate an
    app for stores in your organization"): POST the app's client ID and secret to the
    store's token endpoint, get a token valid 24h, renew before it expires or after a
    401. Works only when the app and the store are in the same Shopify organization."""

    MARGIN_S = 600

    def __init__(self, store_domain: str, client_id: str, client_secret: str,
                 client: httpx.Client | None = None, clock=time.monotonic):
        self.url = f"https://{store_domain}/admin/oauth/access_token"
        self._id, self._secret = client_id, client_secret
        self._client = client or httpx.Client(timeout=20)
        self._clock = clock
        self._token, self._expires = None, 0.0
        self._lock = threading.Lock()

    def token(self) -> str:
        with self._lock:
            if self._token and self._clock() < self._expires - self.MARGIN_S:
                return self._token
            r = self._client.post(self.url, data={
                "grant_type": "client_credentials", "client_id": self._id,
                "client_secret": self._secret}, headers={"Accept": "application/json"})
            if r.status_code == 429 or r.status_code >= 500:
                r.raise_for_status()  # retryable
            body = r.json() if r.headers.get("content-type", "").startswith(
                "application/json") else {}
            if r.status_code >= 400 or "access_token" not in body:
                err = str(body.get("error") or body.get("errors") or r.status_code)
                if "shop_not_permitted" in err:
                    raise NonRetryableError(
                        "Shopify refused the token: the Jackson app isn't in this store's "
                        "organization. Create it from the store admin (Settings > Apps > "
                        "Develop apps > Build apps in Dev Dashboard) and install it there; "
                        "see docs/CONNECTORS.md")
                raise NonRetryableError(f"Shopify token request failed ({err[:120]})")
            self._token = body["access_token"]
            self._expires = self._clock() + float(body.get("expires_in", 86399))
            return self._token

    def invalidate(self) -> None:
        with self._lock:
            self._token, self._expires = None, 0.0


class ShopifyAdmin:
    backend = "shopify"

    def __init__(self, store_domain: str, token, client: httpx.Client | None = None):
        """`token` is a static Admin API token (str) or a ClientCredentials provider."""
        if not STORE_RE.fullmatch(store_domain or ""):
            raise NonRetryableError("store_domain must be a <name>.myshopify.com domain")
        self.url = f"https://{store_domain}/admin/api/{API_VERSION}/graphql.json"
        self._auth = token
        self._client = client or httpx.Client(timeout=20)
        self.store_domain = store_domain

    def _headers(self) -> dict:
        tok = self._auth if isinstance(self._auth, str) else self._auth.token()
        return {"X-Shopify-Access-Token": tok, "Content-Type": "application/json"}

    def _gql(self, query: str, variables: dict) -> dict:
        r = self._client.post(self.url, json={"query": query, "variables": variables},
                              headers=self._headers())
        if r.status_code == 401 and not isinstance(self._auth, str):
            self._auth.invalidate()  # token expired or revoked: get a fresh one, once
            r = self._client.post(self.url, json={"query": query, "variables": variables},
                                  headers=self._headers())
        if r.status_code == 429 or r.status_code >= 500:
            r.raise_for_status()  # retryable: the worker backs off
        if r.status_code >= 400:
            raise NonRetryableError(f"Shopify HTTP {r.status_code}")
        return r.json()

    def find_by_handle(self, handle: str) -> dict | None:
        q = ("query($q: String!) { products(first: 1, query: $q) "
             "{ nodes { id handle status title } } }")
        body = self._gql(q, {"q": f"handle:{handle}"})
        _gql_errors(body, "products")
        nodes = body["data"]["products"]["nodes"]
        return nodes[0] if nodes else None

    def create_draft(self, p: DraftProductIn) -> dict:
        existing = self.find_by_handle(p.handle)
        if existing:  # idempotent: never create a second copy
            return {**existing, "created": False}
        m = ("mutation($product: ProductCreateInput!) { productCreate(product: $product) { "
             "product { id handle status title variants(first: 1) { nodes { id } } } "
             "userErrors { field message } } }")
        body = self._gql(m, {"product": {
            "title": p.title, "descriptionHtml": p.description_html, "handle": p.handle,
            "productType": p.product_type, "vendor": p.vendor, "tags": p.tags,
            "status": "DRAFT"}})
        _gql_errors(body, "productCreate")
        prod = body["data"]["productCreate"]["product"]
        variant = prod["variants"]["nodes"][0]["id"]
        m2 = ("mutation($productId: ID!, $variants: [ProductVariantsBulkInput!]!) { "
              "productVariantsBulkUpdate(productId: $productId, variants: $variants) { "
              "productVariants { id price } userErrors { field message } } }")
        body2 = self._gql(m2, {"productId": prod["id"],
                               "variants": [{"id": variant, "price": f"{p.price:.2f}"}]})
        _gql_errors(body2, "productVariantsBulkUpdate")
        return {"id": prod["id"], "handle": prod["handle"], "status": prod["status"],
                "title": prod["title"], "created": True}

    def get(self, product_id: str) -> dict | None:
        q = "query($id: ID!) { product(id: $id) { id handle status title } }"
        body = self._gql(q, {"id": product_id})
        _gql_errors(body, "product")
        return body["data"]["product"]

    def set_status(self, product_id: str, status: str) -> dict:
        m = ("mutation($product: ProductUpdateInput!) { productUpdate(product: $product) { "
             "product { id status } userErrors { field message } } }")
        body = self._gql(m, {"product": {"id": product_id, "status": status}})
        _gql_errors(body, "productUpdate")
        return body["data"]["productUpdate"]["product"]

    def admin_url(self, product_id: str) -> str:
        return f"https://{self.store_domain}/admin/products/{product_id.rsplit('/', 1)[-1]}"


class MockShopify:
    """In-database mock store. Every result says backend='mock'."""

    backend = "mock"

    def __init__(self, db_session, tenant_id):
        self._session, self.tenant_id = db_session, tenant_id

    def _row(self, db, **where):
        from sqlalchemy import select

        from ..models import ShopProduct

        q = select(ShopProduct).where(ShopProduct.tenant_id == self.tenant_id,
                                      ShopProduct.backend == "mock")
        for k, v in where.items():
            q = q.where(getattr(ShopProduct, k) == v)
        return db.scalar(q)

    def find_by_handle(self, handle: str) -> dict | None:
        with self._session() as db:
            r = self._row(db, handle=handle)
            return {"id": r.external_id, "handle": r.handle, "status": r.status,
                    "title": r.title} if r else None

    def create_draft(self, p: DraftProductIn) -> dict:
        existing = self.find_by_handle(p.handle)
        if existing:
            return {**existing, "created": False}
        return {"id": f"gid://mock/Product/{uuid.uuid4().hex[:12]}", "handle": p.handle,
                "status": "DRAFT", "title": p.title, "created": True}

    def get(self, product_id: str) -> dict | None:
        with self._session() as db:
            r = self._row(db, external_id=product_id)
            return {"id": r.external_id, "handle": r.handle, "status": r.status,
                    "title": r.title} if r else None

    def set_status(self, product_id: str, status: str) -> dict:
        return {"id": product_id, "status": status}

    def admin_url(self, product_id: str) -> str:
        return f"mock://shopify/admin/products/{product_id.rsplit('/', 1)[-1]}"


_providers: dict[tuple[str, str], ClientCredentials] = {}


def live_config() -> dict | None:
    """The owner's Shopify settings, if complete (values never leave this process)."""
    domain = (os.environ.get("SHOPIFY_STORE_DOMAIN") or "").strip().lower()
    token = os.environ.get("SHOPIFY_ADMIN_API_TOKEN")
    cid, secret = os.environ.get("SHOPIFY_CLIENT_ID"), os.environ.get("SHOPIFY_CLIENT_SECRET")
    if not domain or not (token or (cid and secret)):
        return None
    return {"domain": domain, "auth": "token" if token else "client_credentials"}


def backend_for(grant_config: dict, db_session, tenant_id):
    """Mock unless the owner configured Shopify. A grant may force mock; it can't point
    Jackson at a different store than the one the owner configured."""
    cfg = grant_config or {}
    live = live_config()
    mode = cfg.get("mode") or ("shopify" if live else "mock")
    if mode == "mock":
        return MockShopify(db_session, tenant_id)
    if mode != "shopify":
        raise NonRetryableError(f"unknown Shopify mode '{mode}'")
    if live is None:
        raise NonRetryableError("Shopify isn't configured: set SHOPIFY_STORE_DOMAIN and "
                                "SHOPIFY_CLIENT_ID + SHOPIFY_CLIENT_SECRET (or "
                                "SHOPIFY_ADMIN_API_TOKEN)")
    domain = live["domain"]
    if cfg.get("store_domain") and cfg["store_domain"].strip().lower() != domain:
        raise NonRetryableError("the grant names a different store than SHOPIFY_STORE_DOMAIN")
    if not STORE_RE.fullmatch(domain):
        raise NonRetryableError("SHOPIFY_STORE_DOMAIN must be a <name>.myshopify.com domain")
    if live["auth"] == "token":
        return ShopifyAdmin(domain, os.environ["SHOPIFY_ADMIN_API_TOKEN"])
    key = (domain, os.environ["SHOPIFY_CLIENT_ID"])
    if key not in _providers:  # one cached token per store/app across tasks
        _providers[key] = ClientCredentials(domain, os.environ["SHOPIFY_CLIENT_ID"],
                                            os.environ["SHOPIFY_CLIENT_SECRET"])
    return ShopifyAdmin(domain, _providers[key])
