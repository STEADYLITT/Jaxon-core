"""Clip scouting task (ADR-052): rank clippable moments from YouTube and Twitch, every pick
with its source link, its numbers and whether clipping it is allowed. Read-only."""

import json


def scout_handler(ctx):
    from .connectors import clips as K
    from .models import Artifact
    from .worker import TaskResult

    inp = ctx.input or {}
    days, limit = int(inp.get("days") or 7), int(inp.get("limit") or 20)
    picks, used = [], []
    if inp.get("youtube_query") or inp.get("youtube_channel_ids"):
        yt = K.youtube()
        for ch in inp.get("youtube_channel_ids") or [""]:
            picks += yt.find(str(inp.get("youtube_query") or ""), str(ch), days, limit)
        used.append("youtube")
    if inp.get("twitch_logins"):
        picks += K.twitch().find(list(inp["twitch_logins"]), days, limit)
        used.append("twitch")
    if not used:
        from .worker_errors import NonRetryableError
        raise NonRetryableError("give youtube_query / youtube_channel_ids or twitch_logins")
    picks.sort(key=lambda p: (p["rights"].startswith("allowed"), p["views"]), reverse=True)
    picks = picks[:200]
    allowed = sum(1 for p in picks if p["rights"].startswith("allowed"))
    data = json.dumps({"inputs": inp, "picks": picks}, indent=1).encode()
    key, sha, size = ctx.artifacts.put(ctx.tenant_id, data)
    with ctx.db_session() as db:
        art = Artifact(tenant_id=ctx.tenant_id, run_id=ctx.run_id, task_id=ctx.task_id,
                       kind="research", content_type="application/json", size_bytes=size,
                       sha256=sha, storage_key=key,
                       meta={"type": "clip_candidates", "platforms": used,
                             "picks": len(picks), "allowed": allowed})
        db.add(art)
        db.flush()
        aid = str(art.id)
        db.commit()
    top = [{k: p.get(k) for k in ("platform", "title", "creator", "views", "url", "rights")}
           for p in picks[:5]]
    return TaskResult({"artifact_id": aid, "picks": len(picks), "allowed": allowed,
                       "platforms": used, "top": top,
                       "summary": f"{len(picks)} clip candidates ({allowed} allowed to clip)"})
