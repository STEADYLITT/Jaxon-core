"""Course & Education Factory (ADR-040): the first end-to-end department slice.

"AI trading course is trending, build it" ->
  1. outline   (model, structured: outcomes, modules, lessons; capped 4 x 3)
  2. lessons   (model per lesson: script, slide cards, quiz, worksheet)
  3. student platform draft (static pages: sign-in, dashboard with progress, one page per
     lesson with its quiz, certificate, privacy) -> HTML/accessibility checks + real
     Chromium preview at desktop and phone sizes (the website factory's checks)
  4. sales page draft (curriculum, outcomes, price only if Steady gave one, Whop checkout
     placeholder) -> the same checks, plus the claims check (no income promises)
  5. a Launch PROPOSAL (status 'proposed', no scope): Steady sets budget, channels and
     kill rules and approves it once. Nothing is published, charged or sent here.
  6. what was built goes to memory with its source (the run).

No model key -> every step still runs on the MOCK provider and every string says [MOCK];
nothing is passed off as real. Payments/login/community are drafts until WHOP_API_KEY
(and the community connectors) exist: the pages say so.
"""

import html
import json
import re

from .connectors.claims import check_claims
from .models_ai import ModelRequest
from .worker_errors import NonRetryableError

MAX_MODULES, MAX_LESSONS = 4, 3
BRAND = {"primary": "#5b21b6", "text": "#1a1a1a", "background": "#ffffff"}
DISCLAIMER = ("Education only, not financial advice. Results depend on you; this course makes "
              "no promise of income.")
PRICE_RE = re.compile(r"\$\s?(\d{1,5}(?:\.\d{2})?)")

_STR = {"type": "string"}
_STRS = {"type": "array", "items": _STR}


def _obj(props: dict) -> dict:
    return {"type": "object", "properties": props, "required": list(props),
            "additionalProperties": False}


OUTLINE_SCHEMA = _obj({
    "title": _STR, "subtitle": _STR, "audience": _STR, "promise": _STR, "outcomes": _STRS,
    "modules": {"type": "array", "items": _obj({
        "title": _STR, "outcome": _STR,
        "lessons": {"type": "array", "items": _obj({"title": _STR, "objective": _STR})}})},
})
LESSON_SCHEMA = _obj({
    "script": _STR,
    "slides": {"type": "array", "items": _obj({"heading": _STR, "bullets": _STRS})},
    "quiz": {"type": "array", "items": _obj({"question": _STR, "choices": _STRS,
                                             "answer_index": {"type": "integer"},
                                             "why": _STR})},
    "worksheet": _STRS,
})

RULES = ("Teach; never promise income, profits or guaranteed results. No fake testimonials, "
         "student counts or reviews. Trading content: explain risk plainly, education only. "
         "Plain language a beginner understands.")


def _outline_request(brief: str) -> ModelRequest:
    return ModelRequest(
        system=f"You are a course designer for Steady's education brands. {RULES}",
        user=(f"Design a course from this command. Up to {MAX_MODULES} modules with up to "
              f"{MAX_LESSONS} lessons each; every module has a measurable outcome.\n"
              f"<command>{brief}</command>"),
        max_tokens=4000, json_schema=OUTLINE_SCHEMA)


def _lesson_request(outline: dict, module: dict, lesson: dict) -> ModelRequest:
    return ModelRequest(
        system=f"You write lessons for an online course. {RULES}",
        user=(f"Course: {outline['title']}\nModule: {module['title']} "
              f"(outcome: {module['outcome']})\nLesson: {lesson['title']} "
              f"(objective: {lesson['objective']})\nWrite the lesson script (about 400 "
              f"words), 3-5 slide cards, a 3-question multiple-choice quiz and 3 worksheet "
              f"prompts."), max_tokens=4000, json_schema=LESSON_SCHEMA)


def normalize_outline(o: dict, mock: bool) -> dict:
    mods = []
    for m in (o.get("modules") or [])[:MAX_MODULES]:
        lessons = [x for x in (m.get("lessons") or [])[:MAX_LESSONS] if x.get("title")]
        if m.get("title") and lessons:
            mods.append({"title": m["title"], "outcome": m.get("outcome", ""),
                         "lessons": lessons})
    if not mods:  # the MOCK provider returns empty lists: say so, never invent a curriculum
        tag = "[MOCK] " if mock else ""
        mods = [{"title": f"{tag}Module 1", "outcome": "[MOCK] connect ANTHROPIC_API_KEY to "
                 "write the real outline" if mock else "",
                 "lessons": [{"title": f"{tag}Lesson 1", "objective": "[MOCK] placeholder"
                              if mock else ""}]}]
    return {"title": (o.get("title") or "Untitled course")[:200],
            "subtitle": o.get("subtitle", ""), "audience": o.get("audience", ""),
            "promise": o.get("promise", ""), "outcomes": list(o.get("outcomes") or [])[:8],
            "modules": mods}


# --- Pages ---------------------------------------------------------------------------------

def _e(s) -> str:
    return html.escape(str(s or ""), quote=True)


def _paras(text: str) -> str:
    return "".join(f"<p>{_e(p.strip())}</p>" for p in re.split(r"\n\s*\n", str(text or ""))
                   if p.strip())


CSS = (f"*{{box-sizing:border-box}}body{{margin:0;font-family:system-ui,sans-serif;"
       f"line-height:1.6;color:{BRAND['text']};background:{BRAND['background']}}}"
       "header,main,footer{max-width:52rem;margin:0 auto;padding:1rem}"
       "nav ul{list-style:none;display:flex;flex-wrap:wrap;gap:1rem;padding:0}"
       f"a{{color:{BRAND['primary']}}}.cta{{display:inline-block;background:{BRAND['primary']};"
       "color:#fff;padding:.75rem 1.25rem;border-radius:.375rem;text-decoration:none}"
       ".draft{border:2px dashed #b45309;padding:.5rem .75rem;border-radius:.375rem}"
       "progress{width:100%}label{display:block;margin:.5rem 0 .25rem}"
       "input{width:100%;max-width:24rem;padding:.5rem;font:inherit}"
       ".slide{border:1px solid #ddd;border-radius:.5rem;padding:.25rem 1rem;margin:.75rem 0}")


def _page(site: str, title: str, heading: str, desc: str, nav: list[tuple[str, str]],
          body: str) -> str:
    links = "".join(f'<li><a href="{h}">{_e(t)}</a></li>' for h, t in nav)
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            f"<title>{_e(title)}</title><meta name=\"description\" content=\"{_e(desc)}\">"
            f"<style>{CSS}</style></head><body><header><a href=\"index.html\">{_e(site)}</a>"
            f'<nav aria-label="Main"><ul>{links}</ul></nav></header>'
            f"<main><h1>{_e(heading)}</h1>{body}</main>"
            f'<footer><p>{_e(DISCLAIMER)} · <a href="privacy.html">Privacy notice</a></p>'
            "</footer></body></html>")


def _privacy(site: str, nav, what: str) -> str:
    return _page(site, f"Privacy notice | {site}", "Privacy notice", f"Privacy - {site}", nav,
                 f"<p>This notice explains what {_e(site)} collects.</p><h2>What we "
                 f"collect</h2><p>{_e(what)}</p><h2>Your choices</h2><p>You can ask us to "
                 "delete your data at any time.</p>")


def lesson_slug(mi: int, li: int) -> str:
    return f"lesson-{mi + 1}-{li + 1}.html"


def build_platform(outline: dict, lessons: dict[tuple[int, int], dict]) -> dict[str, str]:
    """The student platform draft: static pages, no scripts. Real sign-in, payments,
    progress tracking and certificates need Whop (WHOP_API_KEY): the pages say so."""
    t = outline["title"]
    nav = [("dashboard.html", "Dashboard"), ("certificate.html", "Certificate")]
    order = [(mi, li) for mi, m in enumerate(outline["modules"])
             for li in range(len(m["lessons"]))]
    files = {}
    files["index.html"] = _page(t, f"Sign in | {t}", f"{t}: student sign-in",
                                f"Student sign-in for {t}", nav, (
        '<p class="draft">Draft: students sign in with their Whop membership. Sign-in turns '
        "on when WHOP_API_KEY is set; nothing on this page works yet.</p>"
        '<form><label for="email">Email</label><input id="email" type="email" '
        'autocomplete="email"></form>'
        '<p><a class="cta" href="dashboard.html">Preview the student dashboard</a></p>'))
    mods = ""
    for mi, m in enumerate(outline["modules"]):
        items = "".join(f'<li><a href="{lesson_slug(mi, li)}">{_e(x["title"])}</a></li>'
                        for li, x in enumerate(m["lessons"]))
        mods += (f"<section><h2>Module {mi + 1}: {_e(m['title'])}</h2>"
                 f"<p>{_e(m['outcome'])}</p><ol>{items}</ol></section>")
    files["dashboard.html"] = _page(t, f"Dashboard | {t}", "Your dashboard",
                                    f"Course dashboard for {t}", nav, (
        f'<p><label for="progress">Progress: 0 of {len(order)} lessons complete</label>'
        f'<progress id="progress" max="{len(order)}" value="0">0%</progress></p>'
        '<p class="draft">Draft: progress, quizzes and certificates are saved per student '
        "once Whop sign-in is connected. Community (Discord/Telegram role sync) is not built "
        "yet.</p>" + mods))
    for n, (mi, li) in enumerate(order):
        lesson = outline["modules"][mi]["lessons"][li]
        body = lessons.get((mi, li)) or {}
        slides = "".join(f'<div class="slide"><h3>{_e(s.get("heading"))}</h3><ul>'
                         + "".join(f"<li>{_e(b)}</li>" for b in s.get("bullets") or [])
                         + "</ul></div>" for s in body.get("slides") or [])
        quiz = ""
        for qi, q in enumerate(body.get("quiz") or []):
            ch = q.get("choices") or []
            ai = q.get("answer_index", 0)
            right = ch[ai] if isinstance(ai, int) and 0 <= ai < len(ch) else ""
            quiz += (f"<h3>Question {qi + 1}. {_e(q.get('question'))}</h3><ol type=\"A\">"
                     + "".join(f"<li>{_e(c)}</li>" for c in ch) + "</ol><details><summary>"
                     f"Show the answer</summary><p>{_e(right)}: {_e(q.get('why'))}</p>"
                     "</details>")
        work = "".join(f"<li>{_e(w)}</li>" for w in body.get("worksheet") or [])
        nxt = (f'<p><a class="cta" href="{lesson_slug(*order[n + 1])}">Next lesson</a></p>'
               if n + 1 < len(order) else
               '<p><a class="cta" href="certificate.html">Get your certificate</a></p>')
        files[lesson_slug(mi, li)] = _page(
            t, f"{lesson['title']} | {t}", lesson["title"], lesson.get("objective") or t, nav,
            f"<p>{_e(lesson.get('objective'))}</p><h2>Lesson</h2>"
            f"{_paras(body.get('script')) or '<p>Not written yet.</p>'}"
            f"<h2>Slides</h2>{slides or '<p>None yet.</p>'}"
            f"<h2>Quiz</h2>{quiz or '<p>None yet.</p>'}"
            f"<h2>Worksheet</h2>{f'<ol>{work}</ol>' if work else '<p>None yet.</p>'}{nxt}")
    files["certificate.html"] = _page(t, f"Certificate | {t}", "Certificate of completion",
                                      f"Certificate for {t}", nav, (
        '<p class="draft">Template: issued automatically when a signed-in student passes '
        "every quiz (needs Whop sign-in).</p>"
        f"<p>This certifies that <strong>[student name]</strong> completed <strong>{_e(t)}"
        "</strong>.</p>"))
    files["privacy.html"] = _privacy(t, nav, "When sign-in is connected: your email, your "
                                     "lesson progress and quiz results, used only to run the "
                                     "course. Payments are handled by Whop.")
    return files


def build_sales_page(outline: dict, price: str | None) -> dict[str, str]:
    t = outline["title"]
    nav = [("index.html", "Overview"), ("privacy.html", "Privacy")]
    curriculum = "".join(
        f"<h3>Module {mi + 1}: {_e(m['title'])}</h3><ul>"
        + "".join(f"<li>{_e(x['title'])}</li>" for x in m["lessons"]) + "</ul>"
        for mi, m in enumerate(outline["modules"]))
    outcomes = "".join(f"<li>{_e(o)}</li>" for o in outline["outcomes"])
    price_txt = (f"<p><strong>Price: ${_e(price)}</strong></p>" if price else
                 '<p class="draft">Price: not set. You set it before the launch.</p>')
    files = {"index.html": _page(t, t, t, outline["subtitle"] or t, nav, (
        f"<p>{_e(outline['subtitle'])}</p>{_paras(outline['promise'])}"
        f"<h2>Who it's for</h2><p>{_e(outline['audience'])}</p>"
        + (f"<h2>What you'll learn</h2><ul>{outcomes}</ul>" if outcomes else "")
        + f'<h2 id="curriculum">Curriculum</h2>{curriculum}'
        f'<h2 id="enroll">Enroll</h2>{price_txt}'
        + CHECKOUT_DRAFT))}
    files["privacy.html"] = _privacy(t, nav, "This page has no forms, cookies or tracking. "
                                     "Checkout is handled by Whop.")
    return files


CHECKOUT_DRAFT = ('<p class="draft">Draft: checkout through Whop connects when WHOP_API_KEY is '
                  "set. Nothing can be bought from this page yet.</p>")


def with_checkout(html: str, purchase_url: str | None) -> str:
    """Swap the checkout placeholder for the course's real Whop checkout link (ADR-041)."""
    if not purchase_url or not purchase_url.startswith("https://"):
        return html
    return html.replace(CHECKOUT_DRAFT, f'<p><a class="cta" href="{_e(purchase_url)}">Enroll '
                        "now</a> (secure checkout by Whop)</p>")


def copy_of(outline: dict, lessons: dict) -> str:
    parts = [outline["title"], outline["subtitle"], outline["promise"], *outline["outcomes"]]
    for body in lessons.values():
        parts.append(body.get("script", ""))
        parts += [b for s in body.get("slides") or [] for b in s.get("bullets") or []]
    return "\n".join(str(p) for p in parts)


def check_pages(files: dict[str, str], outline: dict, extra_copy: str) -> dict:
    from .sites import preview_check, static_checks

    req = {"brand": BRAND, "business_name": outline["title"], "tagline": outline["subtitle"],
           "intro": outline["promise"]}
    static = static_checks(files, req)
    claims = [f"copy: {c}" for c in check_claims(extra_copy)]
    static["problems"] += [c for c in claims if c not in static["problems"]]
    static["passed"] = not static["problems"]
    preview = preview_check(files)
    preview.pop("_screenshot", None)
    return {"passed": static["passed"] and preview["passed"],
            "problems": static["problems"] + preview["problems"],
            "pages": sorted(files), "preview_ran": preview.get("ran", False)}


# --- Handler -------------------------------------------------------------------------------

def build_handler(ctx):
    from sqlalchemy import select

    from .lines import BY_KEY
    from .memory import write_memory
    from .models import Business, Course, Launch
    from .worker import TaskResult

    inp = ctx.input or {}
    brief = str(inp.get("brief") or ctx.objective or "").strip()[:2000]
    if not brief:
        raise NonRetryableError("a course needs a topic")
    with ctx.db_session() as db:
        biz = ctx.business_id or db.scalar(
            select(Business.id).where(Business.tenant_id == ctx.tenant_id,
                                      Business.line == "courses")
            .order_by(Business.created_at).limit(1))
        db.rollback()
    if biz is None:
        raise NonRetryableError("no business in the Courses department yet: put one there "
                                "(PATCH /businesses/{id}/line) and try again")

    calls, modes = 0, set()

    def ask(req: ModelRequest) -> dict:
        nonlocal calls
        resp = ctx.call_model("planning", req)
        calls += 1
        modes.add(resp.mode)
        try:
            return json.loads(resp.text)
        except ValueError as exc:
            raise NonRetryableError(f"the model returned something that isn't JSON: {exc}") \
                from exc

    raw = ask(_outline_request(brief))
    mock = modes == {"mock"}
    outline = normalize_outline(raw, mock)
    lessons: dict[tuple[int, int], dict] = {}
    for mi, m in enumerate(outline["modules"]):
        for li, lesson in enumerate(m["lessons"]):
            lessons[(mi, li)] = ask(_lesson_request(outline, m, lesson))
    mock = "live" not in modes
    m = PRICE_RE.search(brief)
    price = m.group(1) if m else None
    platform = build_platform(outline, lessons)
    sales = build_sales_page(outline, price)
    copy = copy_of(outline, lessons)
    platform_checks = check_pages(platform, outline, copy)
    sales_checks = check_pages(sales, outline, "")
    passed = platform_checks["passed"] and sales_checks["passed"]
    n_lessons = len(lessons)
    ln = BY_KEY["courses"]
    plan = (f"Course: {outline['title']}\n{outline['subtitle']}\n"
            f"{len(outline['modules'])} modules, {n_lessons} lessons, quizzes and worksheets.\n"
            f"Student platform draft: {len(platform)} pages, "
            f"{'passed' if platform_checks['passed'] else 'has problems in'} its checks.\n"
            f"Sales page draft: {'passed' if sales_checks['passed'] else 'has problems in'} "
            f"its checks. Price: {('$' + price) if price else 'not set (you set it)'}.\n"
            f"Suggested channels: {', '.join(ln.launch['channels']['social_accounts'])}; "
            f"email list {', '.join(ln.launch['channels']['email_lists'])}.\n"
            "Needs before selling: WHOP_API_KEY (checkout, sign-in, progress, certificates).\n"
            + ("[MOCK] No model key: this course is a labeled placeholder.\n" if mock else "")
            + "You set the budget, daily cap, channels and kill rules, then approve once.")
    with ctx.db_session() as db:
        la = Launch(tenant_id=ctx.tenant_id, business_id=biz,
                    title=f"Launch course: {outline['title']}"[:200], plan=plan[:20_000],
                    proposed_by="jackson", status="proposed", version=1)
        db.add(la)
        db.flush()
        c = Course(tenant_id=ctx.tenant_id, business_id=biz, run_id=ctx.run_id, brief=brief,
                   title=outline["title"], status="ready" if passed else "needs_fixes",
                   mode="mock" if mock else "live", outline=outline,
                   lessons=[{"module": mi, "lesson": li, **body}
                            for (mi, li), body in sorted(lessons.items())],
                   platform_files=platform, sales_files=sales,
                   checks={"platform": platform_checks, "sales": sales_checks},
                   launch_id=la.id, price=price)
        db.add(c)
        db.flush()
        if not mock:  # the lesson for memory: what was built, with its source (this run)
            write_memory(db, tenant_id=ctx.tenant_id, kind="project_state", epistemic="fact",
                         content=(f"Course drafted: {outline['title']} ({n_lessons} lessons); "
                                  f"launch proposed, waiting for Steady."),
                         source_type="run", source_ref=str(ctx.run_id), business_id=biz,
                         agent="course_build")
        cid, lid = str(c.id), str(la.id)
        db.commit()
    return TaskResult({"course_id": cid, "launch_id": lid, "title": outline["title"],
                       "modules": len(outline["modules"]), "lessons": n_lessons,
                       "passed": passed, "mode": "mock" if mock else "live",
                       "model_calls": calls,
                       "problems": platform_checks["problems"] + sales_checks["problems"]})
