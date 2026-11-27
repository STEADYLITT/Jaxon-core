"""Jaxon's router (phase 23, ADR-037): after planning Steady's command, send it to the
department agent that can actually do it, and say why.

1. Clear keywords decide first, and the reason names the words that matched.
2. Otherwise the model picks among the agents that can start from a sentence. The reason
   is the model's; in MOCK mode it is labeled as such.
3. Nothing fits: Jaxon keeps it (the plan is the result) and says so.

Only agents whose input can come from a typed sentence are routable. The others need
records first (a product, a campaign, an opportunity): their work comes from launches and
approvals, not from a free-text command.
"""

import json
import re

from .models_ai import ModelRequest

URL_RE = re.compile(r"https?://[^\s)>\"']+")

# kind -> (keywords, needs URLs, title prefix)
RULES = [
    ("daily_report", ("report", "summary", "how are we", "how did we", "status update",
                      "recap"), False, "Daily report"),
    ("content_generate", ("post", "caption", "content", "social", "instagram", "tiktok",
                          "tweet", "reel", "hashtag", "write a thread"), False, "Draft content"),
    ("opportunity_research", ("research", "find", "opportunit", "market", "competitor",
                              "product idea"), True, "Research"),
]
ROUTABLE = [k for k, *_ in RULES]
CHANNELS = ("instagram", "tiktok", "x", "facebook", "youtube_shorts")

ROUTE_SCHEMA = {
    "type": "object",
    "properties": {
        "agent": {"type": "string", "enum": ["jaxon", *ROUTABLE]},
        "reason": {"type": "string"},
    },
    "required": ["agent", "reason"],
    "additionalProperties": False,
}


def _input(kind: str, text: str) -> dict | None:
    if kind == "content_generate":
        low = text.lower()
        channel = next((c for c in CHANNELS if c.replace("_shorts", "") in low), "instagram")
        if "twitter" in low or "tweet" in low:
            channel = "x"
        return {"brief": text[:2000], "channel": channel, "count": 1}
    if kind == "opportunity_research":
        urls = URL_RE.findall(text)
        return {"query": text[:500], "urls": urls[:10]} if urls else None
    return {}


def by_rules(text: str) -> dict | None:
    low = text.lower()
    for kind, words, _needs_urls, title in RULES:
        hits = [w for w in words if w in low]
        if not hits:
            continue
        inp = _input(kind, text)
        if inp is None:  # e.g. research without a source to read
            continue
        return {"kind": kind, "input": inp, "title": f"{title}: {text[:200]}",
                "reason": f"the command mentions {', '.join(repr(h) for h in hits[:3])}",
                "via": "rules"}
    return None


def by_model(ctx, text: str) -> dict | None:
    resp = ctx.call_model("fast", ModelRequest(
        system="You route a business owner's command to one agent. Answer with JSON.",
        user=("Agents: daily_report (writes the owner's report), content_generate (drafts "
              "social posts from a brief), opportunity_research (needs source URLs in the "
              "command), jaxon (keep it: the plan is the answer).\n<command>\n"
              f"{json.dumps(text)}\n</command>"),
        max_tokens=300, json_schema=ROUTE_SCHEMA))
    try:
        out = json.loads(resp.text)
    except ValueError:
        return None
    kind, reason = out.get("agent"), str(out.get("reason") or "")[:300]
    if resp.mode != "live":
        reason = f"[MOCK model] {reason}"
    inp = _input(kind, text) if kind in ROUTABLE else None
    if inp is None:
        return {"kind": None, "reason": reason or "no agent fits", "via": f"model ({resp.mode})"}
    title = dict((k, t) for k, _, _, t in RULES)[kind]
    return {"kind": kind, "input": inp, "title": f"{title}: {text[:200]}", "reason": reason,
            "via": f"model ({resp.mode})"}


def route(ctx, text: str) -> dict:
    r = by_rules(text) or by_model(ctx, text)
    if r is None or not r.get("kind"):
        return {"kind": None, "via": (r or {}).get("via", "rules"),
                "reason": (r or {}).get("reason") or "no department agent fits; Jaxon "
                                                     "keeps it and the plan is the result"}
    return r
