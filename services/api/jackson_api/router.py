"""Jaxon's router (ADR-037 phase 5, made line-aware by ADR-039).

After planning a command Steady typed, Jaxon decides:
1. which business line it is about (keywords from lines.py; the reason names the words);
2. which agent of that line can start it from the sentence:
   - posts/captions/content  -> Content Writer, for that line's business;
   - report/briefing/summary -> Daily Reporter;
   - research/ideas with source links -> Research Scout;
   - an ACTIVE AgentSpec of that line whose name/role matches the command;
3. otherwise Jaxon keeps it: the plan is the draft, and the reason says which proposed
   agent would take it and which Railway variables would make the line real.
A line whose connectors are MOCK still gets planned and drafted; the reason says which key
is missing. Explicit-content commands are refused before any planning.
"""

import json
import re

from sqlalchemy import select

from . import connections as C
from .lines import BY_KEY, Line, line_of_text
from .models_ai import ModelRequest

URL_RE = re.compile(r"https?://[^\s)>\"']+")
CHANNELS = ("instagram", "tiktok", "x", "facebook", "youtube_shorts")
REFUSED = ("explicit", "nsfw", "nude", "nudes", "porn", "xxx", "sexual", "lewd")

POST_WORDS = ("post", "posts", "caption", "content", "tweet", "reel", "hashtag", "thread")
REPORT_WORDS = ("report", "summary", "briefing", "recap", "how are we", "how did we")
RESEARCH_WORDS = ("research", "find", "idea", "opportunit", "scout")

ROUTABLE = ["daily_report", "content_generate", "opportunity_research"]
ROUTE_SCHEMA = {
    "type": "object",
    "properties": {"agent": {"type": "string", "enum": ["jaxon", *ROUTABLE]},
                   "reason": {"type": "string"}},
    "required": ["agent", "reason"],
    "additionalProperties": False,
}


def refused(text: str) -> str | None:
    low = text.lower()
    hit = [w for w in REFUSED if re.search(rf"\b{re.escape(w)}\b", low)]
    if hit:
        return ("explicit content is out of scope for every line: Jackson won't plan, draft "
                "or post it")
    return None


def _count(text: str) -> int:
    m = re.search(r"\b(\d{1,3})\b", text)
    return max(1, min(int(m.group(1)), 10)) if m else 1


def _channel(text: str) -> str:
    low = text.lower()
    if "twitter" in low or "tweet" in low:
        return "x"
    if "short" in low:
        return "youtube_shorts"
    if "reel" in low:
        return "instagram"
    return next((c for c in CHANNELS if c.replace("_shorts", "") in low), "instagram")


def _hits(text: str, words) -> list[str]:
    low = text.lower()
    return [w for w in words if w in low]


def _missing(ln: Line | None) -> list[str]:
    if ln is None:
        return []
    out = []
    for cid in ln.connectors:
        out += C.missing(C.BY_ID[cid])
    out += [v for v, _ in ln.needs_not_built]
    return sorted(set(out))


def _active_spec(ctx, ln: Line, text: str):
    """An active AgentSpec of this line whose name/role words appear in the command."""
    from .models import AgentSpec, Business

    with ctx.db_session() as db:
        biz = select(Business.id).where(Business.tenant_id == ctx.tenant_id,
                                        Business.line == ln.key)
        specs = db.scalars(select(AgentSpec).where(
            AgentSpec.tenant_id == ctx.tenant_id, AgentSpec.status == "active",
            (AgentSpec.business_id.in_(biz)) | (AgentSpec.source_ref == f"line:{ln.key}")))
        low = text.lower()
        for s in specs:
            words = {w for w in re.split(r"[^a-z]+", f"{s.name} {s.role}".lower()) if len(w) > 3}
            if any(w in low for w in words):
                return s.id, s.name
        db.rollback()
    return None


def _line_business(ctx, ln: Line | None):
    from .models import Business

    if ln is None:
        return None
    with ctx.db_session() as db:
        b = db.scalar(select(Business.id).where(Business.tenant_id == ctx.tenant_id,
                                                Business.line == ln.key)
                      .order_by(Business.created_at).limit(1))
        db.rollback()
        return b


def _proposed(ctx, ln: Line) -> list[str]:
    from .models import AgentSpec

    with ctx.db_session() as db:
        names = list(db.scalars(select(AgentSpec.name).where(
            AgentSpec.tenant_id == ctx.tenant_id, AgentSpec.status == "draft",
            AgentSpec.source_ref == f"line:{ln.key}")))
        db.rollback()
        return names


def by_rules(text: str, ln: Line | None = None) -> dict | None:
    """Agent choice from the words (no database): kept for direct use and tests."""
    if _hits(text, REPORT_WORDS):
        return {"kind": "daily_report", "input": {}, "title": f"CEO briefing: {text[:200]}",
                "why": f"it asks for {', '.join(repr(h) for h in _hits(text, REPORT_WORDS)[:2])}"}
    if _hits(text, POST_WORDS):
        return {"kind": "content_generate",
                "input": {"brief": text[:2000], "channel": _channel(text), "count": _count(text)},
                "title": f"Draft content: {text[:200]}",
                "why": f"it asks for {', '.join(repr(h) for h in _hits(text, POST_WORDS)[:2])}"}
    urls = URL_RE.findall(text)
    if urls and _hits(text, RESEARCH_WORDS):
        return {"kind": "opportunity_research", "input": {"query": text[:500], "urls": urls[:10]},
                "title": f"Research: {text[:200]}", "why": "it asks for research with sources"}
    return None


def by_model(ctx, text: str) -> dict | None:
    resp = ctx.call_model("fast", ModelRequest(
        system="You route a business owner's command to one agent. Answer with JSON.",
        user=("Agents: daily_report (the owner's briefing), content_generate (drafts social "
              "posts from a brief), opportunity_research (needs source URLs in the command), "
              "jaxon (keep it: the plan is the answer).\n<command>\n"
              f"{json.dumps(text)}\n</command>"),
        max_tokens=300, json_schema=ROUTE_SCHEMA))
    try:
        out = json.loads(resp.text)
    except ValueError:
        return None
    reason = str(out.get("reason") or "")[:300]
    if resp.mode != "live":
        reason = f"[MOCK model] {reason}"
    kind = out.get("agent")
    r = by_rules(text) if kind in ROUTABLE else None
    if r is None or r["kind"] != kind:
        return {"kind": None, "reason": reason or "no agent fits", "via": f"model ({resp.mode})"}
    return {**r, "reason": reason, "via": f"model ({resp.mode})"}


def route(ctx, text: str) -> dict:
    bad = refused(text)
    if bad:
        return {"kind": None, "reason": bad, "via": "rules", "line": None, "refused": True}
    ln, line_hits = line_of_text(text)
    business_id = _line_business(ctx, ln)
    missing = _missing(ln)
    where = (f"line: {ln.name} (mentions {', '.join(repr(h) for h in line_hits[:3])})"
             if ln else "no line named")
    mock_note = f"; MOCK until {', '.join(missing)} {'is' if len(missing) == 1 else 'are'} set" \
        if missing else ""
    base = {"line": ln.key if ln else None, "business_id": str(business_id) if business_id
            else None, "missing": missing}
    if ln is not None:
        spec = _active_spec(ctx, ln, text)
        if spec:
            sid, name = spec
            return {**base, "kind": "agent", "input": {"objective": text[:2000]},
                    "title": f"{name}: {text[:200]}", "agent_spec_id": str(sid),
                    "reason": f"{where}; its agent {name} matches the command{mock_note}",
                    "via": "rules"}
    r = by_rules(text)
    if r is not None:
        return {**base, **{k: v for k, v in r.items() if k != "why"},
                "reason": f"{where}; {r['why']}{mock_note}", "via": "rules"}
    if ln is not None:
        proposed = _proposed(ctx, ln)
        staff = (f"proposed agent{'s' if len(proposed) > 1 else ''} "
                 f"{', '.join(proposed)} would take it once you approve "
                 f"{'them' if len(proposed) > 1 else 'it'}") if proposed else \
            "no agent of this line can start it from a sentence yet"
        keys = f"; to make it real set {', '.join(missing)}" if missing else ""
        return {**base, "kind": None, "via": "rules",
                "reason": f"{where}; Jaxon keeps it and the plan is the draft — {staff}{keys}"}
    m = by_model(ctx, text)
    if m and m.get("kind"):
        return {**base, **{k: v for k, v in m.items() if k != "why"}}
    return {**base, "kind": None, "via": (m or {}).get("via", "rules"),
            "reason": (m or {}).get("reason") or "no line or agent fits; Jaxon keeps it and "
                                                  "the plan is the result"}


def describe_line(key: str | None) -> str:
    return BY_KEY[key].name if key in BY_KEY else "Jaxon"
