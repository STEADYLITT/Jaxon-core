"""Web research task (ADR-043): Google search results, Google Trends, or Google Maps places
via Apify. Cost cap first (approval or a Launch), every result keeps its source URL.

- search / trends -> a JSON artifact of cited results (the research desk reads them)
- places          -> leads in the CRM: source_type public_business_listing, lawful basis
  legitimate_interest, source_ref = the Google Maps URL. Business phone/website only (it's
  a public business listing, not a person). Nobody is contacted by this task.
"""

import json

from .worker_errors import NonRetryableError


def research_handler(ctx):
    from sqlalchemy.dialects.postgresql import insert

    from .connectors import webresearch as R
    from .crm import dedupe_key
    from .models import Artifact, Contact, RunEvent
    from .policy import Action, Category
    from .worker import TaskResult

    inp = ctx.input or {}
    kind = inp.get("kind")
    if kind not in R.ACTORS:
        raise NonRetryableError(f"kind must be one of {sorted(R.ACTORS)}")
    terms = [str(t) for t in inp.get("terms") or []]
    items = int(inp.get("items") or 20)
    actor_input = R.build_input(kind, terms, str(inp.get("location") or ""), items,
                                str(inp.get("country") or "us"))
    api = R.client()  # names APIFY_TOKEN when it's missing: nothing is invented
    cap = R.estimate_cents(kind, min(items, R.MAX_ITEMS))
    desc = (f"Web research ({kind}) via Apify for {', '.join(terms)[:120]}"
            f"{' in ' + str(inp.get('location'))[:60] if inp.get('location') else ''}: up to "
            f"{min(items, R.MAX_ITEMS)} {R.ACTORS[kind][2]}, capped at ${cap / 100:.2f}")
    ctx.authorize(Action(Category.SPEND, desc, cap, channel="web_research", always_ask=True))
    reservation = ctx.reserve_budget(cap, desc)
    raw = api.run(kind, actor_input, cap)
    ctx.settle_spend(reservation, cap, desc)  # the cap is what Apify may charge at most
    rows = R.normalize(kind, raw)[:R.MAX_ITEMS]
    out = {"kind": kind, "terms": terms, "results": len(rows), "cap_cents": cap,
           "cost_cents": cap, "connector": "apify", "actor": R.ACTORS[kind][0]}
    data = json.dumps({"kind": kind, "terms": terms, "location": inp.get("location"),
                       "results": rows}, indent=1).encode()
    key, sha, size = ctx.artifacts.put(ctx.tenant_id, data)
    leads = 0
    with ctx.db_session() as db:
        art = Artifact(tenant_id=ctx.tenant_id, run_id=ctx.run_id, task_id=ctx.task_id,
                       kind="research", content_type="application/json", size_bytes=size,
                       sha256=sha, storage_key=key,
                       meta={"type": f"web_{kind}", "source": "apify", "results": len(rows)})
        db.add(art)
        db.flush()
        out["artifact_id"] = str(art.id)
        if kind == "places":
            for p in rows:
                name = str(p.get("name") or "")[:200]
                if not name:
                    continue
                phone = (str(p.get("phone") or "")[:32]) or None
                stmt = insert(Contact).values(
                    tenant_id=ctx.tenant_id, business_id=ctx.business_id, name=name,
                    company=name, phone=phone, industry=str(p.get("category") or "")[:100],
                    source_type="public_business_listing", source_ref=p["url"][:500],
                    lawful_basis="legitimate_interest",
                    notes=f"From Google Maps via Apify: {p.get('address') or ''}"
                          f"{' · ' + p['website'] if p.get('website') else ''}"[:2000],
                    dedupe_key=dedupe_key(name, name, None, phone)
                ).on_conflict_do_nothing(index_elements=["tenant_id", "dedupe_key"]
                                         ).returning(Contact.id)
                leads += db.execute(stmt).scalar() is not None
            out["new_leads"] = leads
        db.add(RunEvent(tenant_id=ctx.tenant_id, run_id=ctx.run_id, type="research.web",
                        data=out))
        db.commit()
    out["summary"] = (f"Web research ({kind}): {len(rows)} cited results"
                      + (f", {leads} new leads" if kind == "places" else ""))
    return TaskResult(out)
