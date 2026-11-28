"""Amazon/eBay product research (ADR-053): real prices, ranks, fees and margins, cited.

For each keyword: Amazon catalog matches with sales rank; for the top ones the Buy Box /
lowest new price and the FBA fee estimate at that price; eBay active-listing asking
prices. If Steady gives a landed cost per unit, the margin is computed from real numbers
only; anything missing stays missing. Read-only: nothing is listed or bought.
"""

import json

from .worker_errors import NonRetryableError

TOP = 5


def margin(price_cents, fees_cents, cost_cents) -> dict | None:
    if price_cents is None or fees_cents is None or cost_cents is None:
        return None
    profit = price_cents - fees_cents - cost_cents
    return {"profit_cents": profit, "margin_pct": round(100 * profit / price_cents, 1)
            if price_cents else None}


def research_handler(ctx):
    from .connectors import marketplaces as M
    from .models import Artifact
    from .worker import TaskResult

    inp = ctx.input or {}
    kws = [str(k).strip()[:200] for k in inp.get("keywords") or [] if str(k).strip()][:5]
    if not kws:
        raise NonRetryableError("give at least one keyword")
    cost = inp.get("landed_cost_cents")
    cost = int(cost) if cost is not None else None
    want_amz, want_ebay = bool(inp.get("amazon", True)), bool(inp.get("ebay", True))
    amz = M.amazon() if want_amz and M.amazon_configured() else None
    eb = M.ebay() if want_ebay and M.ebay_configured() else None
    if not (amz or eb):
        raise NonRetryableError("connect Amazon (AMAZON_SP_CLIENT_ID, AMAZON_SP_CLIENT_SECRET, "
                                "AMAZON_SP_REFRESH_TOKEN) or eBay (EBAY_CLIENT_ID, "
                                "EBAY_CLIENT_SECRET)")
    report = []
    for kw in kws:
        row = {"keyword": kw}
        if amz:
            items = amz.search(kw, 10)
            for it in items[:TOP]:
                p = amz.price(it["asin"])
                it.update(p)
                price = p["buy_box_cents"] or p["lowest_cents"]
                it["fba_fees_cents"] = amz.fba_fees(it["asin"], price) if price else None
                it["margin"] = margin(price, it["fba_fees_cents"], cost)
            row["amazon"] = items
        if eb:
            row["ebay"] = eb.search(kw, 50)
        report.append(row)
    data = json.dumps({"inputs": inp, "results": report}, indent=1).encode()
    key, sha, size = ctx.artifacts.put(ctx.tenant_id, data)
    with ctx.db_session() as db:
        art = Artifact(tenant_id=ctx.tenant_id, run_id=ctx.run_id, task_id=ctx.task_id,
                       kind="research", content_type="application/json", size_bytes=size,
                       sha256=sha, storage_key=key,
                       meta={"type": "marketplace_research", "keywords": kws,
                             "sources": [s for s, on in (("amazon", amz), ("ebay", eb)) if on]})
        db.add(art)
        db.flush()
        aid = str(art.id)
        db.commit()
    best = []
    for r in report:
        for it in r.get("amazon") or []:
            if it.get("margin"):
                best.append((it["margin"]["profit_cents"], it["title"], it["url"]))
    best.sort(reverse=True)
    return TaskResult({"artifact_id": aid, "keywords": kws,
                       "sources": [s for s, on in (("amazon", amz), ("ebay", eb)) if on],
                       "best_margins": [{"profit_cents": b[0], "title": b[1], "url": b[2]}
                                        for b in best[:5]],
                       "summary": f"Marketplace research for {', '.join(kws)[:80]}"})
