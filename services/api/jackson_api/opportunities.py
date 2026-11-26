"""Opportunity engine (spec vol. 03, phase 13, ADR-023).

normalize -> dedupe -> timestamp -> score -> brief. Scoring is a transparent, pure
function: every component is shown with the signals behind it, missing data lowers
coverage (and raises uncertainty) instead of being guessed, and an IP-risk flag caps the
score. Signals must cite a source; research drops any signal whose source wasn't actually
fetched ("never invent").
"""

import math
import re
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

SIGNAL_TYPES = {
    # type: (meaning, unit)
    "search_interest": ("relative search interest", "0-100"),
    "trend_growth": ("year-over-year growth", "fraction, e.g. 0.35 = +35%"),
    "marketplace_rank": ("best-seller rank in category", "rank (1 = best)"),
    "review_count": ("number of reviews on leading listings", "count"),
    "review_rating": ("average rating", "1-5"),
    "competitor_count": ("number of comparable sellers", "count"),
    "avg_price": ("typical retail price", "USD"),
    "est_unit_cost": ("estimated landed unit cost", "USD"),
    "shipping_weight_kg": ("shipping weight", "kg"),
    "return_rate": ("expected return rate", "fraction"),
    "ip_flag": ("trademark/patent/brand risk spotted", "true/false"),
}

WEIGHTS = {"demand": 0.30, "competition": 0.15, "margin": 0.25, "logistics": 0.10,
           "returns": 0.10, "ip_safety": 0.10}
STALE_AFTER = timedelta(days=180)


def dedupe_key(title: str) -> str:
    words = sorted(set(re.findall(r"[a-z0-9]+", title.lower())) - {"the", "a", "an", "for",
                                                                   "and", "of", "with"})
    return "-".join(words)[:300] or "untitled"


def normalize_signal(raw: dict, now: datetime | None = None) -> dict | None:
    """Return a clean signal or None if it isn't usable (unknown type / no source / bad value)."""
    t = raw.get("type")
    src = (raw.get("source_url") or "").strip()
    if t not in SIGNAL_TYPES or not src.startswith(("http://", "https://")):
        return None
    v = raw.get("value")
    if t == "ip_flag":
        v = bool(v) if isinstance(v, bool) else str(v).lower() in ("true", "1", "yes")
    else:
        try:
            v = float(v)
        except (TypeError, ValueError):
            return None
        if math.isnan(v) or v < 0:
            return None
    observed = raw.get("observed_at") or (now or datetime.now(UTC)).isoformat()
    return {"type": t, "value": v, "source_url": src, "observed_at": observed,
            "note": (raw.get("note") or "")[:300]}


def merge_signals(existing: list[dict], new: list[dict]) -> list[dict]:
    """Dedupe by (type, source_url): a newer observation of the same thing replaces the old."""
    by = {(s["type"], s["source_url"]): s for s in existing}
    for s in new:
        k = (s["type"], s["source_url"])
        if k not in by or s["observed_at"] >= by[k]["observed_at"]:
            by[k] = s
    return sorted(by.values(), key=lambda s: (s["type"], s["observed_at"]))


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def _avg(vals: list[float]) -> float | None:
    return sum(vals) / len(vals) if vals else None


def score(signals: list[dict], economics: dict | None = None,
          now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    by: dict[str, list[float]] = {}
    for s in signals:
        by.setdefault(s["type"], []).append(s["value"])

    def vals(t):
        return [float(v) for v in by.get(t, []) if not isinstance(v, bool)]

    demand_parts = (
        [_clamp(v / 100) for v in vals("search_interest")]
        + [_clamp((v + 0.2) / 0.8) for v in vals("trend_growth")]
        + [_clamp(1 - math.log10(max(v, 1)) / 6) for v in vals("marketplace_rank")]
        + [_clamp(math.log10(v + 1) / 4) for v in vals("review_count")]
    )
    comp = {"demand": _avg(demand_parts)}
    cc = vals("competitor_count")
    comp["competition"] = _avg([_clamp(1 - math.log10(c + 1) / 3) for c in cc])
    if economics and economics.get("contribution_margin_pct") is not None:
        comp["margin"] = _clamp(float(economics["contribution_margin_pct"]) / 0.5)
        margin_basis = "unit economics"
    elif vals("avg_price") and vals("est_unit_cost"):
        p, c = _avg(vals("avg_price")), _avg(vals("est_unit_cost"))
        comp["margin"] = _clamp(((p - c) / p) / 0.7) if p > 0 else 0.0
        margin_basis = "price vs. unit cost (gross, before fees and ads)"
    else:
        comp["margin"], margin_basis = None, None
    comp["logistics"] = _avg([_clamp(1 - w / 5) for w in vals("shipping_weight_kg")])
    comp["returns"] = _avg([_clamp(1 - r / 0.3) for r in vals("return_rate")])
    ip = by.get("ip_flag")
    comp["ip_safety"] = None if ip is None else (0.0 if any(ip) else 1.0)

    have = {k: v for k, v in comp.items() if v is not None}
    covered = sum(WEIGHTS[k] for k in have)
    overall = (sum(WEIGHTS[k] * v for k, v in have.items()) / covered) if covered else 0.0

    hosts = {urlparse(s["source_url"]).hostname for s in signals}
    fresh = [s for s in signals if _age(s, now) <= STALE_AFTER]
    evidence = _clamp(len(hosts) / 4) * (0.5 + 0.5 * (len(fresh) / len(signals))) \
        if signals else 0.0
    uncertainty = round(1 - evidence * covered, 3)
    flags = []
    if comp["ip_safety"] == 0.0:
        flags.append("ip_risk: capped at 20")
        overall = min(overall, 0.20)
    return {
        "score": round(overall * 100, 1),
        "uncertainty": uncertainty,
        "components": {k: (round(v, 3) if v is not None else None) for k, v in comp.items()},
        "weights": WEIGHTS,
        "coverage": round(covered, 3),
        "evidence": round(evidence, 3),
        "independent_sources": len(hosts),
        "margin_basis": margin_basis,
        "missing": [k for k, v in comp.items() if v is None],
        "flags": flags,
    }


def _age(s: dict, now: datetime) -> timedelta:
    try:
        t = datetime.fromisoformat(s["observed_at"])
        if t.tzinfo is None:
            t = t.replace(tzinfo=UTC)
        return now - t
    except (ValueError, KeyError):
        return timedelta(days=9999)


def brief(title: str, scored: dict, signals: list[dict], summary: str | None = None) -> str:
    lines = [f"# {title}", "",
             f"**Score {scored['score']}/100** · uncertainty {scored['uncertainty']:.0%} · "
             f"{scored['independent_sources']} independent source(s)", ""]
    if summary:
        lines += [summary, ""]
    if scored["flags"]:
        lines += ["**Flags:** " + "; ".join(scored["flags"]), ""]
    lines += ["| Factor | Score (0-1) | Weight |", "|---|---|---|"]
    for k, w in scored["weights"].items():
        v = scored["components"][k]
        lines.append(f"| {k} | {'—' if v is None else v} | {w} |")
    if scored["margin_basis"]:
        lines += ["", f"Margin basis: {scored['margin_basis']}."]
    if scored["missing"]:
        lines += ["", "**Missing data (not guessed):** " + ", ".join(scored["missing"]) + "."]
    lines += ["", "## Evidence"]
    for s in signals:
        meaning = SIGNAL_TYPES[s["type"]][0]
        host = urlparse(s["source_url"]).hostname
        note = f" — {s['note']}" if s["note"] else ""
        lines.append(f"- {meaning}: **{s['value']}** — [{host}]({s['source_url']}) "
                     f"({s['observed_at'][:10]}){note}")
    lines += ["", "_Signals are observations from the cited pages, not verified facts. "
                  "Validate before spending money._"]
    return "\n".join(lines)


def upsert(db, *, tenant_id, title: str, signals: list[dict], business_id=None,
           summary: str | None = None, category: str | None = None, created_by: str = "owner",
           source_ref: str | None = None):
    """Create or enrich an opportunity (deduped by normalized title). Returns (opp, created)."""
    from sqlalchemy import select

    from .models import Opportunity

    clean = [s for s in (normalize_signal(r) for r in signals) if s]
    key = dedupe_key(title)
    opp = db.scalar(select(Opportunity).where(Opportunity.tenant_id == tenant_id,
                                              Opportunity.dedupe_key == key))
    created = opp is None
    if created:
        opp = Opportunity(tenant_id=tenant_id, business_id=business_id, title=title.strip(),
                          dedupe_key=key, category=category, summary=summary, signals=[],
                          created_by=created_by, source_ref=source_ref, status="new")
        db.add(opp)
    merged = merge_signals(list(opp.signals or []), clean)
    s = score(merged, opp.economics)
    opp.signals = merged
    opp.scores = s
    opp.score = s["score"]
    opp.uncertainty = s["uncertainty"]
    if summary:
        opp.summary = summary
    if category:
        opp.category = category
    opp.sources = sorted({x["source_url"] for x in merged})
    opp.brief = brief(opp.title, s, merged, opp.summary)
    db.flush()
    return opp, created
