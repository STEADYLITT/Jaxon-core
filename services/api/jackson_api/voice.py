"""Voice task (ADR-042): narrate a course lesson or an ad script with ElevenLabs.

The script is claims-checked first (no income promises, fake reviews ...), then the cost
estimate is a SPEND the owner approves (or an active Launch with budget left covers)
BEFORE ElevenLabs is called. The mp3 is stored as an artifact labeled "AI voice".
"""

import uuid

from .worker_errors import NonRetryableError

PURPOSES = ("narration", "ad")


def script_for(db, tenant_id, inp: dict) -> tuple[str, dict]:
    from .models import Course

    if inp.get("course_id"):
        c = db.get(Course, uuid.UUID(str(inp["course_id"])))
        if c is None or c.tenant_id != tenant_id:
            raise NonRetryableError("course not found")
        mi, li = int(inp.get("module", 0)), int(inp.get("lesson", 0))
        found = next((x for x in c.lessons or [] if x.get("module") == mi
                      and x.get("lesson") == li), None)
        if not found or not (found.get("script") or "").strip():
            raise NonRetryableError("that lesson has no script to narrate")
        title = c.outline["modules"][mi]["lessons"][li]["title"]
        return found["script"], {"course_id": str(c.id), "module": mi, "lesson": li,
                                 "title": title, "course_mode": c.mode}
    text = str(inp.get("text") or "").strip()
    if not text:
        raise NonRetryableError("give a script (text) or a course lesson to narrate")
    return text, {"title": str(inp.get("title") or text[:60])}


def voice_handler(ctx):
    from .connectors import voice
    from .connectors.claims import check_claims
    from .models import Artifact, RunEvent
    from .policy import Action, Category
    from .worker import TaskResult

    inp = ctx.input or {}
    purpose = inp.get("purpose") or "narration"
    if purpose not in PURPOSES:
        raise NonRetryableError(f"purpose must be one of {PURPOSES}")
    voice_id = str(inp.get("voice_id") or "").strip()
    if not voice_id or not voice_id.replace("-", "").replace("_", "").isalnum():
        raise NonRetryableError("pick a voice from your ElevenLabs voice library (voice_id)")
    with ctx.db_session() as db:
        text, about = script_for(db, ctx.tenant_id, inp)
        db.rollback()
    if about.get("course_mode") == "mock":
        raise NonRetryableError("this course was drafted by the MOCK model: rebuild it with "
                                "ANTHROPIC_API_KEY before paying for narration")
    if len(text) > voice.MAX_CHARS:
        raise NonRetryableError(f"script is {len(text)} characters; the limit per voice task "
                                f"is {voice.MAX_CHARS}")
    problems = check_claims(text)
    if problems:
        raise NonRetryableError("the script fails the claims check: " + "; ".join(problems))
    backend = voice.voice_backend()
    est = voice.estimate_cents(len(text), None if backend.backend == "mock" else backend.model)
    desc = (f"AI voice ({purpose}) for '{about['title'][:80]}': {len(text):,} characters via "
            f"ElevenLabs {backend.model} - est. ${est / 100:.2f}")
    ctx.authorize(Action(Category.SPEND, desc, est, channel="ai_voice", always_ask=True))
    reservation = ctx.reserve_budget(est, desc)
    audio = backend.speak(voice_id, text)
    paid = est if backend.backend == "elevenlabs" else 0
    ctx.settle_spend(reservation, paid, desc)
    out = {"purpose": purpose, "characters": len(text), "estimate_cents": est,
           "cost_cents": paid, "connector": backend.backend, "model": backend.model,
           "voice_id": voice_id, "label": "AI voice", **about}
    if backend.backend == "mock":
        out.update(artifact_id=None, mock=True,
                   summary=f"[MOCK] no audio made for '{about['title'][:60]}': set "
                           "ELEVENLABS_API_KEY")
        return TaskResult(out)
    key, sha, size = ctx.artifacts.put(ctx.tenant_id, audio)
    with ctx.db_session() as db:
        art = Artifact(tenant_id=ctx.tenant_id, run_id=ctx.run_id, task_id=ctx.task_id,
                       kind="audio", content_type="audio/mpeg", size_bytes=size, sha256=sha,
                       storage_key=key, meta={"type": "ai_voice", "ai_generated": True,
                                              **{k: v for k, v in out.items()
                                                 if k != "label"}})
        db.add(art)
        db.flush()
        out["artifact_id"] = str(art.id)
        db.add(RunEvent(tenant_id=ctx.tenant_id, run_id=ctx.run_id, type="voice.generated",
                        data=out))
        db.commit()
    out["summary"] = f"AI voice for '{about['title'][:60]}' ({len(text):,} chars)"
    return TaskResult(out)
