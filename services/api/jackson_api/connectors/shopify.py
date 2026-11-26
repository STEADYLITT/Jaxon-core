"""Shopify draft-product connector (phase 15, ADR-025).

Backends:
- MockShopify: the default. Clearly labeled `backend: "mock"`. Products live in the
  `shop_products` table.
- ShopifyAdmin: the real Admin GraphQL API (version 2026-10, checked against shopify.dev).
  Used only when the grant sets mode "shopify" and SHOPIFY_ADMIN_API_TOKEN is in the
  environment. The token never goes in the database, grants or logs. It's meant for a
  DEVELOPMENT store: the grant must set dev_store=true.

Flow: create the product as DRAFT -> set the variant price -> read it back and verify
it's still DRAFT. Publishing (DRAFT -> ACTIVE) is a separate tool in the
`production_change` category, so it always needs owner approval.
"""

import os
import re
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


class ShopifyAdmin:
    backend = "shopify"

    def __init__(self, store_domain: str, token: str, client: httpx.Client | None = None):
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*\.myshopify\.com", store_domain or ""):
            raise NonRetryableError("store_domain must be a <name>.myshopify.com domain")
        self.url = f"https://{store_domain}/admin/api/{API_VERSION}/graphql.json"
        self._headers = {"X-Shopify-Access-Token": token, "Content-Type": "application/json"}
        self._client = client or httpx.Client(timeout=20)
        self.store_domain = store_domain

    def _gql(self, query: str, variables: dict) -> dict:
        r = self._client.post(self.url, json={"query": query, "variables": variables},
                              headers=self._headers)
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


def backend_for(grant_config: dict, db_session, tenant_id):
    mode = (grant_config or {}).get("mode", "mock")
    if mode == "mock":
        return MockShopify(db_session, tenant_id)
    if mode != "shopify":
        raise NonRetryableError(f"unknown Shopify mode '{mode}'")
    if not grant_config.get("dev_store"):
        raise NonRetryableError("live Shopify access is for a development store only: the "
                                "grant must set dev_store=true")
    token = os.environ.get("SHOPIFY_ADMIN_API_TOKEN")
    if not token:
        raise NonRetryableError("SHOPIFY_ADMIN_API_TOKEN is not configured")
    return ShopifyAdmin(grant_config.get("store_domain", ""), token)
