"""First vertical slice, builder + QA steps (spec vol. 06): opportunity -> Shopify DRAFT.

store_build:
  1. needs the opportunity AND its unit economics (finance step, phase 14);
  2. writes product copy (model; MOCK placeholder copy if no model) and rejects invented
     claims;
  3. creates the product as a DRAFT through ctx.shopify() (policy: sandbox_change);
  4. QA verifies it independently: still DRAFT, right price, right title, clean copy;
  5. stores a JSON report artifact. Nothing is published; publishing is a separate,
     approval-gated task.
"""

import json
import uuid

from .connectors.claims import check_claims
from .models import Artifact, Opportunity
from .models_ai import ModelRequest
from .worker_errors import NonRetryableError

COPY_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "description_html": {"type": "string"},
        "product_type": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["title", "description_html", "product_type", "tags"],
    "additionalProperties": False,
}

COPY_RULES = """Write a Shopify product title and description for a DRAFT listing.
Describe only what the product is and does, using the facts given. No ratings, review
counts, rankings, "best seller", medical/scientific or guaranteed-results claims, and no
invented specs. Plain, clear and confident. description_html uses only <p> and <ul><li>."""


def _result(output):
    from .worker import TaskResult

    return TaskResult(output=output)


def store_build_handler(ctx):
    opp_id = (ctx.input or {}).get("opportunity_id")
    with ctx.db_session() as db:
        opp = db.get(Opportunity, uuid.UUID(opp_id)) if opp_id else None
        if opp is None or opp.tenant_id != ctx.tenant_id:
            raise NonRetryableError("opportunity not found")
        if not opp.economics:
            raise NonRetryableError("run unit economics on this opportunity first (finance "
                                    "step) so the price and margin are known")
        title, summary = opp.title, opp.summary or ""
        econ = opp.economics
        db.rollback()
    price = float(econ["inputs"]["price"])
    margin = econ["contribution_margin_pct"]

    resp = ctx.call_model("standard", ModelRequest(
        system=COPY_RULES,
        user=json.dumps({"product": title, "summary": summary, "price_usd": price}),
        max_tokens=4000, json_schema=COPY_SCHEMA, effort="low"))
    copy = json.loads(resp.text)
    if resp.mode == "mock":  # placeholder, and labeled as one
        copy = {"title": title[:255], "product_type": "",
                "description_html": "<p>[MOCK] Placeholder description. Write real copy "
                                    "before publishing.</p>", "tags": ["jackson-draft"]}
    problems = check_claims(copy["title"] + " " + copy["description_html"])
    if problems:
        raise NonRetryableError("QA rejected the copy: " + "; ".join(problems))

    shop = ctx.shopify()
    draft = shop.create_draft(title=copy["title"], description_html=copy["description_html"],
                              price=price, product_type=copy["product_type"],
                              tags=copy["tags"], opportunity_id=opp_id)

    checks = {
        "is_draft": draft["status"] == "DRAFT" and draft["verified"],
        "price_matches_economics": draft["price_cents"] == round(price * 100),
        "title_matches": draft["title"] == copy["title"],
        "copy_has_no_unverified_claims": not problems,
        "not_published": draft["status"] != "ACTIVE",
    }
    report = {"opportunity_id": opp_id, "opportunity": title, "draft": draft,
              "copy_mode": resp.mode, "unit_margin_pct": margin, "qa": checks,
              "qa_passed": all(checks.values()),
              "next": "review the draft; publishing needs your approval"}
    if not report["qa_passed"]:
        raise NonRetryableError(f"QA failed: {[k for k, v in checks.items() if not v]}")
    data = json.dumps(report, indent=2, default=str).encode()
    key, sha, size = ctx.artifacts.put(ctx.tenant_id, data)
    with ctx.db_session() as db:
        art = Artifact(tenant_id=ctx.tenant_id, run_id=ctx.run_id, task_id=ctx.task_id,
                       kind="report", content_type="application/json", size_bytes=size,
                       sha256=sha, storage_key=key, meta={"type": "store_build_qa"})
        db.add(art)
        db.flush()
        report["report_artifact_id"] = str(art.id)
        db.commit()
    return _result(report)


def publish_handler(ctx):
    row_id = (ctx.input or {}).get("shop_product_id")
    if not row_id:
        raise NonRetryableError("shop_product_id required")
    return _result(ctx.shopify().publish(uuid.UUID(row_id)))
