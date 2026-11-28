"""Real-estate research tasks (ADR-046): comps and skip tracing via BatchData.

Research only: nothing here contacts an owner, makes an offer or sends a contract.
- property_comps: comparable properties near a subject address -> a JSON artifact with the
  median sale price and price per square foot (only from comps that have the numbers).
- skip_trace: owner contact data for ONE property. It's personal data, so it is always
  the owner's decision (SENSITIVE_DATA approval, with the cost shown). Phones flagged
  Do-Not-Call and litigator matches are kept marked so no call list ever includes them.
"""

import json
import statistics


def _artifact(ctx, kind: str, payload: dict, meta: dict) -> str:
    from .models import Artifact

    data = json.dumps(payload, indent=1, default=str).encode()
    key, sha, size = ctx.artifacts.put(ctx.tenant_id, data)
    with ctx.db_session() as db:
        art = Artifact(tenant_id=ctx.tenant_id, run_id=ctx.run_id, task_id=ctx.task_id,
                       kind=kind, content_type="application/json", size_bytes=size,
                       sha256=sha, storage_key=key, meta=meta)
        db.add(art)
        db.flush()
        aid = str(art.id)
        db.commit()
    return aid


def summarize(comps: list[dict]) -> dict:
    prices = [float(c["last_sale_price"]) for c in comps if c.get("last_sale_price")]
    ppsf = [float(c["last_sale_price"]) / float(c["sqft"]) for c in comps
            if c.get("last_sale_price") and c.get("sqft")]
    return {"comps": len(comps), "with_sale_price": len(prices),
            "median_sale_price": round(statistics.median(prices)) if prices else None,
            "median_price_per_sqft": round(statistics.median(ppsf), 2) if ppsf else None}


def comps_handler(ctx):
    from .connectors import realestate as RE
    from .policy import Action, Category
    from .worker import TaskResult

    inp = ctx.input or {}
    subject = RE.address(inp.get("address") or {})
    take = max(1, min(RE.MAX_COMPS, int(inp.get("take") or 10)))
    api = RE.client()
    est = RE.cents_per_record() * take
    where = ", ".join(subject.values())
    desc = f"Comps for {where[:120]} via BatchData: up to {take} records - est. ${est / 100:.2f}"
    ctx.authorize(Action(Category.SPEND, desc, est, channel="property_data", always_ask=True))
    reservation = ctx.reserve_budget(est, desc)
    comps = api.comps(subject, float(inp.get("miles") or 1.0), take)
    paid = RE.cents_per_record() * max(1, len(comps))
    ctx.settle_spend(reservation, min(paid, est), desc)
    stats = summarize(comps)
    aid = _artifact(ctx, "research", {"subject": subject, "comps": comps, **stats},
                    {"type": "property_comps", "source": "batchdata", **stats})
    return TaskResult({"artifact_id": aid, "subject": where, "cost_cents": min(paid, est),
                       **stats, "summary": f"{len(comps)} comps for {where[:80]}: median "
                       f"${stats['median_sale_price'] or 'n/a'}"})


def skip_trace_handler(ctx):
    from .connectors import realestate as RE
    from .policy import Action, Category
    from .worker import TaskResult

    inp = ctx.input or {}
    prop = RE.address(inp.get("address") or {})
    api = RE.client()
    est = RE.cents_per_record()
    where = ", ".join(prop.values())
    desc = (f"Skip trace (owner contact data) for {where[:120]} via BatchData - est. "
            f"${est / 100:.2f}. Personal data: used only for this deal, never for calls to "
            "Do-Not-Call numbers.")
    ctx.authorize(Action(Category.SENSITIVE_DATA, desc, est, channel="property_data"))
    reservation = ctx.reserve_budget(est, desc)
    res = api.skip_trace(prop)
    paid = est * res["matched"]
    ctx.settle_spend(reservation, paid, desc)
    callable_phones = sum(1 for p in res["people"] if not (p["dnc"] or p["litigator"])
                          for ph in p["phones"] if not ph["dnc"])
    aid = _artifact(ctx, "sensitive", {"property": prop, **res},
                    {"type": "skip_trace", "source": "batchdata", "matched": res["matched"],
                     "personal_data": True})
    return TaskResult({"artifact_id": aid, "property": where, "people": len(res["people"]),
                       "phones_ok_to_call": callable_phones, "cost_cents": paid,
                       "summary": f"Skip trace for {where[:80]}: {len(res['people'])} "
                                  f"people, {callable_phones} callable phones (DNC removed)"})
