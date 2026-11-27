"""Worker-side Shopify capability: `ctx.shopify()` (phase 15, ADR-025).

create_draft: policy `sandbox_change` -> backend creates a DRAFT -> canonical row ->
              read back and VERIFY it's still a draft (spec acceptance "sandbox Shopify
              draft verified").
publish:      policy `production_change`, which ALWAYS needs owner approval -> ACTIVE.
"""

import uuid

from sqlalchemy import func, select

from .connectors.shopify import DraftProductIn, backend_for
from .models import RunEvent, ShopProduct
from .policy import Action, Category
from .worker_errors import NonRetryableError


class ShopHandle:
    def __init__(self, worker, task_id, grant_config: dict):
        self.w, self.task_id = worker, task_id
        with worker.maker() as db:
            task, run = worker._locked(db, task_id)
            self.tenant_id, self.run_id = task.tenant_id, task.run_id
            self.business_id = run.business_id
            db.rollback()
        self.backend = backend_for(grant_config, worker.maker, self.tenant_id)
        self.store = grant_config.get("store_domain")

    def _event(self, db, type_, **data):
        db.add(RunEvent(tenant_id=self.tenant_id, run_id=self.run_id, type=type_,
                        data={"task_id": str(self.task_id), "backend": self.backend.backend,
                              **data}))

    def create_draft(self, **fields) -> dict:
        try:
            p = DraftProductIn(**fields)
        except ValueError as e:
            raise NonRetryableError(f"draft product rejected: {e}") from None
        self.w._authorize(self.task_id,
                          Action(Category.SANDBOX_CHANGE, f"shopify draft: {p.handle}"))
        created = self.backend.create_draft(p)
        with self.w.maker() as db:
            row = db.scalar(select(ShopProduct).where(
                ShopProduct.tenant_id == self.tenant_id,
                ShopProduct.backend == self.backend.backend, ShopProduct.handle == p.handle))
            if row is None:
                row = ShopProduct(
                    tenant_id=self.tenant_id, business_id=self.business_id, run_id=self.run_id,
                    task_id=self.task_id,
                    opportunity_id=uuid.UUID(p.opportunity_id) if p.opportunity_id else None,
                    backend=self.backend.backend, store=self.store,
                    external_id=created["id"], handle=p.handle, title=p.title,
                    price_cents=round(p.price * 100), status="DRAFT",
                    admin_url=self.backend.admin_url(created["id"]),
                    payload=p.model_dump(exclude={"opportunity_id"}))
                db.add(row)
                db.flush()
            self._event(db, "shopify.draft_created", product_id=row.external_id,
                        handle=p.handle, new=bool(created.get("created")))
            db.commit()
            row_id = row.id
        return self.verify(row_id)

    def verify(self, row_id) -> dict:
        with self.w.maker() as db:
            row = db.get(ShopProduct, row_id)
            remote = self.backend.get(row.external_id)
            ok = remote is not None and remote["status"] == "DRAFT" and \
                remote["handle"] == row.handle
            row.verified, row.verified_at = ok, func.now() if ok else None
            self._event(db, "shopify.draft_verified" if ok else "shopify.verify_failed",
                        product_id=row.external_id, remote_status=(remote or {}).get("status"))
            db.commit()
            return {"shop_product_id": str(row.id), "product_id": row.external_id,
                    "handle": row.handle, "title": row.title, "price_cents": row.price_cents,
                    "status": (remote or {}).get("status"), "verified": ok,
                    "backend": self.backend.backend, "admin_url": row.admin_url}

    def publish(self, row_id) -> dict:
        with self.w.maker() as db:
            row = db.get(ShopProduct, row_id)
            if row is None or row.tenant_id != self.tenant_id:
                raise NonRetryableError("product not found")
            ext = row.external_id
            db.rollback()
        # Needs an approval on file (production_change is in ALWAYS_APPROVE), or an active
        # Launch approval that allows Shopify publishing (phase 21).
        self.w._authorize(self.task_id, Action(
            Category.PRODUCTION_CHANGE, f"publish shopify product {ext}",
            channel="shopify_publish"))
        res = self.backend.set_status(ext, "ACTIVE")
        with self.w.maker() as db:
            row = db.get(ShopProduct, row_id)
            row.status = res["status"]
            self._event(db, "shopify.published", product_id=ext)
            db.commit()
        return {"product_id": ext, "status": res["status"], "backend": self.backend.backend}
